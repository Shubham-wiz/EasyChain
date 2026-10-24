"""The model gateway (Phase 5.5, runtime/model_policy.py, server/gateway.py, docs/gateway.md):
budgets and alerts, caching, fallbacks, retries, smoothing, prices, and what model calls cost
per flow, person, environment, model and run."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration
from langchain_core.tools import tool
from pydantic import BaseModel

from easychain.runtime.gateway import (
    RunSettings,
    gateway_init_chat_model,
    reset_settings,
    use_settings,
)
from easychain.runtime.model_policy import (
    CACHED,
    AnswerCache,
    BudgetReached,
    FallbackChatModel,
    GatewayPolicy,
    call_usage,
)
from easychain.server.app import create_app
from easychain.server.db import Database
from easychain.server.gateway import alert_text, period_of, used_up
from easychain.testing import fake_openai as fake_module

from . import pg
from .conftest import input_step, output_step

ASK = {
    "name": "Ask",
    "steps": [
        input_step("question"),
        {
            "id": "answer_it",
            "type": "ai_model",
            "settings": {"model": "openai:gpt-4o-mini", "prompt": "question", "save_as": "answer"},
        },
        output_step("answer"),
    ],
    "connections": [{"from": "input", "to": "answer_it"}, {"from": "answer_it", "to": "output"}],
}


class Ledger:
    """A meter that keeps the calls in a list and stops calls once ``stop`` is set."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.stop: str | None = None

    def check(self) -> None:
        if self.stop:
            raise BudgetReached(self.stop, "drafts")

    def record(self, call: dict[str, Any]) -> None:
        self.calls.append(call)


class Shelf:
    """A cache store in a dict."""

    def __init__(self) -> None:
        self.kept: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.kept.get(key)

    def put(self, key: str, value: str, expires_at: float) -> None:
        self.kept[key] = value


@pytest.fixture
def policy() -> Iterator[GatewayPolicy]:
    made = GatewayPolicy(meter=Ledger())
    token = use_settings(RunSettings(gateway=made))
    yield made
    reset_settings(token)


@pytest.fixture
def fake(fake_openai: Any) -> Any:
    """The fake OpenAI server (shared by the session) with its logs emptied."""
    fake_module.requests_log.clear()
    fake_module.effects["calls"].clear()
    fake_module.effects["applied"].clear()
    return fake_openai


def _chat_requests(fake: Any) -> list[dict]:
    return [r for r in httpx.get(f"{fake.url}/__requests").json() if "chat" in r["path"]]


# ── the runtime: a policy around each model call ───────────────────────────


def test_each_call_is_recorded_with_its_tokens_and_cost(fake, policy):
    reply = gateway_init_chat_model("openai:gpt-4o-mini").invoke("How long do refunds take?")
    assert reply.content
    (call,) = policy.meter.calls
    assert call["provider"] == "openai" and call["model"] == "gpt-4o-mini"
    assert call["input_tokens"] > 0 and call["output_tokens"] > 0
    assert call["cost"] == pytest.approx(
        (call["input_tokens"] * 0.15 + call["output_tokens"] * 0.60) / 1_000_000
    )
    assert call["cached"] is False and call["error"] is None and call["duration_ms"] >= 0


def test_own_prices_win_and_price_models_easy_chain_doesnt_know(fake, policy):
    """Fix later F13: prices for any model."""
    policy.prices = {"openai:gpt-4o-mini": (1_000_000.0, 0.0), "openai:my-tuned": (2.0, 4.0)}
    gateway_init_chat_model("openai:gpt-4o-mini").invoke("Hi")
    gateway_init_chat_model("openai:my-tuned").invoke("Hi")
    priced, tuned = policy.meter.calls
    assert priced["cost"] == pytest.approx(priced["input_tokens"])
    assert tuned["cost"] == pytest.approx(
        (tuned["input_tokens"] * 2 + tuned["output_tokens"] * 4) / 1_000_000
    )
    policy.prices = {}
    gateway_init_chat_model("openai:my-tuned").invoke("Hi")
    assert policy.meter.calls[-1]["cost"] is None, "no price: unknown, never made up"


def test_a_budget_used_up_stops_the_call_before_the_provider_is_asked(fake, policy):
    policy.meter.stop = "The Drafts budget is used up: $5.00 of $5.00 today."
    before = len(_chat_requests(fake))
    with pytest.raises(BudgetReached, match="Drafts budget is used up"):
        gateway_init_chat_model("openai:gpt-4o-mini").invoke("Hi")
    assert len(_chat_requests(fake)) == before
    assert policy.meter.calls == []


def test_the_same_question_gets_the_kept_answer_for_free(fake, policy):
    policy.cache = AnswerCache(Shelf(), 3600, scope="default")
    model = gateway_init_chat_model("openai:gpt-4o-mini", temperature=0)
    first = model.invoke("What is LangGraph?")
    again = gateway_init_chat_model("openai:gpt-4o-mini", temperature=0).invoke(
        "What is LangGraph?"
    )
    other = model.invoke("What is LangChain?")
    assert again.content == first.content and again.response_metadata[CACHED] is True
    assert CACHED not in first.response_metadata and CACHED not in other.response_metadata
    assert len(_chat_requests(fake)) == 2, "the second ask didn't reach the provider"
    kept = policy.meter.calls[1]
    assert kept["cached"] is True and kept["cost"] == 0 and kept["input_tokens"] == 0
    # Other settings are another question.
    gateway_init_chat_model("openai:gpt-4o-mini", temperature=1).invoke("What is LangGraph?")
    assert len(_chat_requests(fake)) == 3


def test_answers_are_kept_per_workspace():
    shelf = Shelf()
    kept = [ChatGeneration(message=AIMessage("x"))]
    AnswerCache(shelf, 60, scope="default").update("p", "m", kept)
    assert AnswerCache(shelf, 60, scope="default").lookup("p", "m")[0].message.content == "x"
    assert AnswerCache(shelf, 60, scope="team-b").lookup("p", "m") is None


def test_a_failing_model_falls_back_to_the_next(fake, policy):
    policy.retries = 0
    policy.fallbacks = {"openai:gpt-4o-down": ["openai:gpt-4o-mini"]}
    model = gateway_init_chat_model("openai:gpt-4o-down", temperature=0.2)
    assert isinstance(model, FallbackChatModel) and isinstance(model, BaseChatModel)
    assert model.model_name == "gpt-4o-down", "the step's own model answers for itself"
    assert model.models[1].temperature == 0.2, "settings any provider understands carry over"
    assert model.invoke("Hello?").content
    failed, answered = policy.meter.calls
    assert failed["model"] == "gpt-4o-down" and "503" in failed["error"]
    assert answered["model"] == "gpt-4o-mini" and answered["error"] is None

    @tool
    def lookup_order(order_id: str) -> str:
        """Look an order up."""
        return "shipped"

    called = model.bind_tools([lookup_order]).invoke("Where is order 42? Use lookup_order.")
    assert called.tool_calls and called.tool_calls[0]["name"] == "lookup_order"

    class Answer(BaseModel):
        answer: str

    shaped = model.with_structured_output(Answer).invoke("Say hello")
    assert isinstance(shaped, Answer)
    # Streaming falls back too (before the first piece arrives).
    assert "".join(c.content for c in model.stream("Hello?"))


def test_a_fallback_with_no_key_here_is_left_out(fake, policy, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    policy.fallbacks = {"openai:gpt-4o-mini": ["anthropic:claude-haiku-4-5"]}
    model = gateway_init_chat_model("openai:gpt-4o-mini")
    assert not isinstance(model, FallbackChatModel)


def test_budgets_dont_fall_back(fake, policy):
    policy.fallbacks = {"openai:gpt-4o-mini": ["openai:gpt-4.1-mini"]}
    policy.meter.stop = "used up"
    with pytest.raises(BudgetReached):
        gateway_init_chat_model("openai:gpt-4o-mini").invoke("Hi")
    assert _chat_requests(fake) == []


def test_retries_and_smoothing(fake, policy):
    policy.retries = 4
    policy.per_second = {"openai": 50}
    one = gateway_init_chat_model("openai:gpt-4o-mini")
    two = gateway_init_chat_model("openai:gpt-4o")
    assert one.max_retries == 4 and two.max_retries == 4
    assert one.rate_limiter is two.rate_limiter, "one limiter per workspace and provider"
    assert gateway_init_chat_model("openai:gpt-4o-mini", max_retries=1).max_retries == 1
    started = time.perf_counter()
    for _ in range(3):
        one.invoke("Hi")
    assert time.perf_counter() - started < 5


def test_no_policy_no_change(fake_openai):
    model = gateway_init_chat_model("openai:gpt-4o-mini")
    assert model.cache is None and model.rate_limiter is None and not model.callbacks
    usage = call_usage(type("R", (), {"generations": [], "llm_output": None})(), None, None, None)
    assert usage["cost"] is None


def test_budget_messages():
    start, end, name = period_of("month", 1_761_264_000)  # 24 October 2025, UTC
    assert name == "2025-10" and end - start == 31 * 86400
    assert period_of("day", 1_761_264_000)[2] == "2025-10-24"
    budget = {"limit": 50.0, "period": "month", "stop": True, "alert_at": 0.8}
    assert used_up("production", budget, 50.2, end) == (
        "The Production budget is used up: $50.20 of $50.00 this month. Model calls stop "
        "until 1 November (UTC)."
    )
    day = {**budget, "period": "day"}
    assert "until tomorrow (UTC)" in used_up("workspace", day, 51, end)
    assert used_up("workspace", day, 51, end).startswith("The workspace's budget")
    assert alert_text("drafts", budget, 40.0, 80) == (
        "The Drafts budget is 80% used: $40.00 of $50.00 this month."
    )
    assert alert_text("drafts", budget, 50.0, 100).endswith("New model calls stop until it renews.")


# ── the server: budgets, alerts, caching and spending ─────────────────────


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        home=tmp_path / "home",
        workspace=tmp_path / "flows",
        static_dir=tmp_path / "no-web",
        worker_options={"poll": 0.05, "schedules": False},
    )
    with TestClient(app) as test_client:
        yield test_client


def _run(client: TestClient, flow_id: str, question: str) -> dict:
    response = client.post(
        "/api/runs",
        json={"flow_id": flow_id, "inputs": {"question": question}, "background": True},
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    for _ in range(300):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("ok", "error", "cancelled", "paused"):
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} didn't finish")


def _until(check: Any, tries: int = 100) -> Any:
    for _ in range(tries):
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError("it didn't happen")


def test_a_budget_alerts_then_stops_runs(client: TestClient, fake):
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    client.put(
        "/api/settings/notifications",
        json={"webhook": {"enabled": True, "url": f"{fake.url}/effects"}},
    )
    bad = client.put(
        "/api/gateway",
        json={
            "budgets": {"drafts": {"limit": -1}, "qa": {"limit": 1}},
            "fallbacks": {"gpt-4o": ["openai:gpt-4o"]},
            "per_second": {"nobody": 2},
        },
    )
    assert bad.status_code == 422
    problems = " ".join(bad.json()["problems"])
    assert "greater than 0" in problems and "isn't a model as provider:model" in problems
    assert "There's no provider “nobody”" in problems
    # Each call costs about a dollar: ten cents an input token (about ten a question).
    saved = client.put(
        "/api/gateway",
        json={
            "budgets": {
                "drafts": {"limit": 1.5, "period": "day", "alert_at": 0.5},
                "production": {"limit": 100},
            },
            "prices": {"openai:gpt-4o-mini": {"input": 100_000, "output": 0}},
        },
    )
    assert saved.status_code == 200, saved.text
    first = _run(client, flow_id, "What is LangGraph, and what is it for?")
    assert first["status"] == "ok" and first["started_by"] == "you"
    shown = client.get("/api/gateway").json()
    drafts = next(b for b in shown["budgets"] if b["scope"] == "drafts")
    production = next(b for b in shown["budgets"] if b["scope"] == "production")
    assert 0.5 <= drafts["spent"] < 1.5 and drafts["budget"]["limit"] == 1.5
    assert production["spent"] == 0, "a draft's calls don't count toward Production"
    assert [a["level"] for a in shown["alerts"]] == [50]
    sent = _until(
        lambda: [
            c["body"]
            for c in httpx.get(f"{fake.url}/effects").json()["calls"]
            if c["body"].get("event") == "budget"
        ]
    )
    assert sent[0]["budget"] == "drafts" and sent[0]["level"] == 50
    assert sent[0]["text"].startswith("The Drafts budget is 50% used: $")

    second = _run(client, flow_id, "And what is LangChain, then, in a few words?")
    assert second["status"] == "ok"
    third = _run(client, flow_id, "One more question?")
    assert third["status"] == "error"
    assert third["error"]["kind"] == "budget"
    assert third["error"]["message"].startswith("The Drafts budget is used up: $")
    assert third["error"]["fixes"][0]["params"] == {"tab": "gateway"}
    assert sorted(a["level"] for a in client.get("/api/gateway").json()["alerts"]) == [50, 100]

    by_flow = client.get("/api/spending", params={"by": "flow", "period": "today"}).json()
    assert by_flow["total"]["calls"] == 2 and by_flow["total"]["cost"] >= 1.5
    (row,) = by_flow["rows"]
    assert row["label"] == "Ask" and row["key"] == flow_id and row["calls"] == 2
    person = client.get("/api/spending", params={"by": "person"}).json()["rows"]
    assert [r["label"] for r in person] == ["you"]
    environment = client.get("/api/spending", params={"by": "environment"}).json()["rows"]
    assert [r["label"] for r in environment] == ["Drafts"]
    model = client.get("/api/spending", params={"by": "model"}).json()["rows"]
    assert [r["label"] for r in model] == ["openai:gpt-4o-mini"]
    runs = client.get("/api/spending", params={"by": "run", "period": "7d"}).json()["rows"]
    assert {r["key"] for r in runs} == {first["run_id"], second["run_id"]}
    assert client.get("/api/spending", params={"by": "week"}).status_code == 422
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert "settings.gateway" in audit


def test_drafts_cache_answers_until_forgotten(client: TestClient, fake):
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    client.put("/api/gateway", json={"cache": {"scopes": ["drafts"], "hours": 1}})
    one = _run(client, flow_id, "What is LangGraph?")
    two = _run(client, flow_id, "What is LangGraph?")
    assert one["output"] == two["output"]
    assert len(_chat_requests(fake)) == 1
    assert two["cost"] == 0
    spent = client.get("/api/spending").json()["total"]
    assert spent["calls"] == 2 and spent["cached"] == 1
    assert client.delete("/api/gateway/cache").json() == {"forgotten": 1}
    _run(client, flow_id, "What is LangGraph?")
    assert len(_chat_requests(fake)) == 2
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert "settings.gateway_cache" in audit


def test_a_failing_provider_falls_back_in_a_run(client: TestClient, fake):
    down = {
        **ASK,
        "steps": [
            ASK["steps"][0],
            {
                **ASK["steps"][1],
                "settings": {**ASK["steps"][1]["settings"], "model": "openai:gpt-4o-down"},
            },
            ASK["steps"][2],
        ],
    }
    flow_id = client.post("/api/flows", json={"spec": down}).json()["id"]
    client.put(
        "/api/gateway",
        json={"fallbacks": {"openai:gpt-4o-down": ["openai:gpt-4o-mini"]}, "retries": 0},
    )
    run = _run(client, flow_id, "Hello?")
    assert run["status"] == "ok", run["error"]
    rows = client.get("/api/spending", params={"by": "model"}).json()["rows"]
    assert {r["label"]: r["errors"] for r in rows} == {
        "openai:gpt-4o-mini": 0,
        "openai:gpt-4o-down": 1,
    }


def test_test_runs_and_published_runs_are_counted_as_theirs(client: TestClient, fake):
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    client.put(
        f"/api/flows/{flow_id}/tests",
        json={"cases": [{"name": "Hi", "inputs": {"question": "Hi"}}]},
    )
    started = client.post(f"/api/flows/{flow_id}/test-runs", json={}).json()["id"]
    _until(lambda: client.get(f"/api/test-runs/{started}").json()["status"] == "done", 300)
    assert client.post(f"/api/flows/{flow_id}/tests/run", json={}).status_code == 200
    client.post(f"/api/flows/{flow_id}/publish", json={"environment": "dev"})
    key = client.post(
        f"/api/flows/{flow_id}/keys", json={"environment": "dev", "kind": "secret", "name": "Shop"}
    ).json()["key"]
    answer = client.post(
        f"/api/published/{flow_id}/dev",
        json={"inputs": {"question": "Hello?"}},
        headers={"Authorization": f"Bearer {key}"},
    ).json()
    assert answer["status"] == "ok"
    labels = {r["label"] for r in client.get("/api/spending").json()["rows"]}
    assert labels == {"Ask", "Ask · Test Runs", "Ask · Tests tab"}
    people = {
        r["label"] for r in client.get("/api/spending", params={"by": "person"}).json()["rows"]
    }
    assert "Access key “Shop”" in people
    environments = {
        r["label"] for r in client.get("/api/spending", params={"by": "environment"}).json()["rows"]
    }
    assert environments == {"Drafts", "Development"}


async def _ledger_works(url: str) -> None:
    db = await Database.connect(url)
    try:
        now = time.time()
        row = {"workspace_id": "default", "kind": "run", "input_tokens": 10, "output_tokens": 5}
        await db.add_model_call({**row, "at": now, "cost": 1.0, "cached": False, "flow_id": "a"})
        await db.add_model_call({**row, "at": now, "cost": None, "cached": True, "flow_id": "a"})
        await db.add_model_call(
            {**row, "at": now, "cost": 2.0, "cached": False, "environment": "production"}
        )
        await db.add_model_call({**row, "at": now - 86400 * 40, "cost": 9.0, "cached": False})
        await db.add_model_call(
            {**row, "workspace_id": "b", "at": now, "cost": 5.0, "cached": False}
        )
        since = now - 60
        assert await db.spent("default", "workspace", since) == 3.0
        assert await db.spent("default", "drafts", since) == 1.0
        assert await db.spent("default", "production", since) == 2.0
        assert await db.spent("default", "staging", since) == 0.0
        (total,) = await db.spending("default", since=since, by="total")
        assert (total["calls"], total["cached"], total["unpriced"], total["cost"]) == (3, 1, 1, 3.0)
        flows = await db.spending("default", since=since, by="flow")
        assert [(r["flow_id"], r["calls"]) for r in flows] == [(None, 1), ("a", 2)]
        assert await db.mark_alert("default", "drafts", "2026-10", 80, 1.0, 1.25)
        assert not await db.mark_alert("default", "drafts", "2026-10", 80, 1.1, 1.25)
        assert [a["level"] for a in await db.alerts("default")] == [80]
        await db.cache_put("k", "default", "old", now + 60)
        await db.cache_put("k", "default", "new", now + 60)
        await db.cache_put("gone", "default", "x", now - 1)
        assert await db.cache_get("k") == "new" and await db.cache_get("gone") is None
        assert await db.clear_cache("default") == 1
        run_id = await db.create_run(
            flow_id="f",
            flow_name="F",
            version_id="v",
            thread_id="t",
            inputs={},
            options={},
            by=("u1", "Olive"),
        )
        run = await db.get_run(run_id)
        assert (run["started_by"], run["started_by_name"]) == ("u1", "Olive")
    finally:
        await db.close()


async def test_the_ledger_on_sqlite(tmp_path: Path):
    await _ledger_works(f"sqlite:///{tmp_path / 'ledger.db'}")


async def test_the_ledger_on_postgres():
    if not pg.available():
        pytest.skip("Postgres isn't available here")
    with pg.server() as base:
        await _ledger_works(pg.new_database(base))
