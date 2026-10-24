"""Dashboards and tracing (Phase 5.7, server/dashboard.py, server/tracing.py,
server/observe_api.py, docs/dashboard.md, docs/tracing.md)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from easychain.server import tracing
from easychain.server.app import create_app
from easychain.server.dashboard import percentile, summarise, window

from .test_api_phase2 import APPROVAL
from .test_gateway import ASK, _run, _until

DAY = 86400
NOW = 1_761_300_000.0  # 24 October 2025, 10:00 UTC


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


# ── the dashboard's numbers ────────────────────────────────────────────────


def test_percentiles_are_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([5], 0.95) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2
    assert percentile(list(range(1, 101)), 0.95) == 95


def test_the_dashboard_adds_up_runs_errors_latency_tokens_and_cost_per_day():
    since, days = window(7, NOW)
    assert len(days) == 7 and days[-1] == NOW - NOW % DAY and since == days[0]
    run = {"flow_id": "a", "flow_name": "Support bot", "status": "ok"}
    runs = [
        {**run, "id": "r1", "created_at": NOW - 60, "duration_ms": 1000},
        {**run, "id": "r2", "created_at": NOW - 30, "duration_ms": 3000},
        {
            **run,
            "id": "r3",
            "created_at": NOW - DAY,
            "status": "error",
            "duration_ms": 500,
            "error": {"kind": "rate_limit", "message": "OpenAI is limiting requests right now."},
        },
        {
            **run,
            "id": "r4",
            "created_at": NOW - DAY + 5,
            "status": "error",
            "duration_ms": 700,
            "error": {"kind": "rate_limit", "message": "OpenAI is limiting requests right now."},
        },
        {"id": "r5", "flow_id": "b", "flow_name": "Triage", "status": "paused", "created_at": NOW},
        {**run, "id": "old", "created_at": NOW - 30 * DAY, "duration_ms": 9},
    ]
    calls = [
        {"at": NOW - 59, "flow_id": "a", "input_tokens": 100, "output_tokens": 20, "cost": 0.5},
        {"at": NOW - DAY, "flow_id": "a", "input_tokens": 10, "output_tokens": 2, "cost": None},
        {"at": NOW - 40 * DAY, "flow_id": "a", "input_tokens": 9, "output_tokens": 9, "cost": 9},
    ]
    found = summarise(runs, calls, days=7, now=NOW)
    totals = found["totals"]
    assert (totals["runs"], totals["ok"], totals["errors"], totals["paused"]) == (5, 2, 2, 1)
    assert totals["error_rate"] == 0.5
    assert totals["p50_ms"] == 700 and totals["p95_ms"] == 3000
    assert (totals["input_tokens"], totals["output_tokens"], totals["cost"]) == (110, 22, 0.5)
    today, yesterday = found["series"][-1], found["series"][-2]
    assert today["day"] == "2025-10-24" and today["runs"] == 3 and today["p95_ms"] == 3000
    assert yesterday["errors"] == 2 and yesterday["p50_ms"] == 500
    assert [d["runs"] for d in found["series"][:-2]] == [0] * 5, "every day, empty or not"
    support, triage = found["flows"]
    assert support["flow"] == "Support bot" and support["runs"] == 4 and support["cost"] == 0.5
    assert support["error_rate"] == 0.5 and triage["error_rate"] is None
    (error,) = found["errors"]
    assert error["count"] == 2 and error["kind"] == "rate_limit"
    assert error["last_run_id"] == "r4" and error["flow"] == "Support bot"


def test_the_dashboard_api(client: TestClient, fake):
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    broken = {
        **ASK,
        "name": "Broken",
        "steps": [
            ASK["steps"][0],
            {
                **ASK["steps"][1],
                "settings": {**ASK["steps"][1]["settings"], "model": "openai:gpt-4o-down"},
            },
            ASK["steps"][2],
        ],
    }
    broken_id = client.post("/api/flows", json={"spec": broken}).json()["id"]
    client.put("/api/gateway", json={"retries": 0})
    assert _run(client, flow_id, "What is LangGraph?")["status"] == "ok"
    assert _run(client, flow_id, "And LangChain?")["status"] == "ok"
    assert _run(client, broken_id, "Hello?")["status"] == "error"

    found = client.get("/api/dashboard", params={"days": 7}).json()
    totals = found["totals"]
    assert (totals["runs"], totals["ok"], totals["errors"]) == (3, 2, 1)
    assert totals["input_tokens"] > 0 and totals["cost"] > 0 and totals["p95_ms"] > 0
    assert len(found["series"]) == 7 and found["series"][-1]["runs"] == 3
    assert {f["flow"]: f["runs"] for f in found["flows"]} == {"Ask": 2, "Broken": 1}
    assert found["errors"][0]["flow"] == "Broken"

    one = client.get("/api/dashboard", params={"days": 7, "flow_id": broken_id}).json()
    assert one["totals"]["runs"] == 1 and one["flow_id"] == broken_id
    drafts = client.get("/api/dashboard", params={"environment": "drafts"}).json()
    assert drafts["totals"]["runs"] == 3 and drafts["days"] == 30
    production = client.get("/api/dashboard", params={"environment": "production"}).json()
    assert production["totals"]["runs"] == 0 and production["totals"]["error_rate"] is None
    assert client.get("/api/dashboard", params={"days": 12}).status_code == 422
    assert client.get("/api/dashboard", params={"environment": "qa"}).status_code == 422


# ── tracing: OpenTelemetry ──────────────────────────────────────────────────


def _lower(headers: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


class Collector:
    """A tiny OTLP/HTTP collector and LangSmith endpoint: keeps every request."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, str], bytes]] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, status: int, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                owner.requests.append(("GET", self.path, _lower(self.headers), b""))
                self._reply(200, b'{"version": "0.1"}' if self.path.startswith("/info") else b"{}")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                owner.requests.append(("POST", self.path, _lower(self.headers), body))
                self._reply(200 if self.path.endswith("/v1/traces") else 202, b"{}")

            do_PATCH = do_POST

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def posts(self, path: str) -> list[tuple[dict[str, str], bytes]]:
        return [(h, b) for m, p, h, b in self.requests if m == "POST" and p.startswith(path)]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def collector() -> Iterator[Collector]:
    found = Collector()
    yield found
    found.close()


@pytest.fixture
def spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(tracing, "make_exporter", lambda settings: exporter)
    monkeypatch.setattr(tracing, "_providers", {})
    yield exporter


def _flush() -> None:
    for provider in tracing._providers.values():
        provider.force_flush()


def _spans_of(exporter: InMemorySpanExporter, run_id: str) -> list[Any]:
    def found() -> list[Any]:
        _flush()
        want = tracing.trace_id_of(run_id)
        return [s for s in exporter.get_finished_spans() if s.context.trace_id == want]

    return _until(found)


def test_tracing_settings_hide_header_values(client: TestClient):
    shown = client.get("/api/settings/tracing").json()
    assert shown["otlp"]["enabled"] is False and shown["langsmith"]["project"] == "easychain"
    assert shown["langsmith_key"] is False
    bad = client.put("/api/settings/tracing", json={"otlp": {"endpoint": "collector:4318"}})
    assert bad.status_code == 422
    assert "Give the collector's address" in bad.json()["problems"][0]
    saved = client.put(
        "/api/settings/tracing",
        json={
            "otlp": {
                "enabled": True,
                "endpoint": "http://collector:4318",
                "headers": {"x-api-key": "abc123secret", "x-team": "{secret:TEAM}"},
            }
        },
    ).json()
    assert saved["otlp"]["headers"] == {"x-api-key": "••••••", "x-team": "{secret:TEAM}"}
    # Sent back hidden, the saved value stays.
    client.put("/api/settings/tracing", json=saved)
    stored = client.app.state.hub.db  # the raw setting keeps the real value
    raw = client.portal.call(stored.get_setting, "tracing", {})
    assert raw["otlp"]["headers"]["x-api-key"] == "abc123secret"
    refused = client.put(
        "/api/settings/tracing",
        json={"otlp": {**saved["otlp"], "headers": {"x-new": "••••••"}}},
    )
    assert refused.status_code == 422
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert "settings.tracing" in audit


def test_a_run_becomes_a_trace_of_steps_and_model_calls(client: TestClient, fake, spans):
    client.put(
        "/api/settings/tracing",
        json={"otlp": {"enabled": True, "endpoint": "http://collector:4318"}},
    )
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    run = _run(client, flow_id, "What is LangGraph?")
    found = _spans_of(spans, run["run_id"])
    by_name = {s.name: s for s in found}
    assert {"run Ask", "step answer_it", "chat gpt-4o-mini"} <= set(by_name)
    root, step, chat = by_name["run Ask"], by_name["step answer_it"], by_name["chat gpt-4o-mini"]
    assert root.parent is None and root.attributes["easychain.run_id"] == run["run_id"]
    assert root.attributes["easychain.status"] == "ok"
    assert step.parent.span_id == root.context.span_id
    assert chat.parent.span_id == step.context.span_id, "a model call sits under its step"
    assert chat.attributes["gen_ai.request.model"] == "gpt-4o-mini"
    assert chat.attributes["gen_ai.system"] == "openai"
    assert chat.attributes["gen_ai.usage.input_tokens"] > 0
    assert root.start_time <= step.start_time <= chat.start_time <= chat.end_time <= root.end_time
    assert step.attributes["easychain.step"] == "answer_it"


def test_a_run_that_waits_for_someone_is_one_trace(client: TestClient, spans):
    client.put(
        "/api/settings/tracing",
        json={"otlp": {"enabled": True, "endpoint": "http://collector:4318"}},
    )
    flow_id = client.post("/api/flows", json={"spec": APPROVAL}).json()["id"]
    response = client.post(
        "/api/runs", json={"flow_id": flow_id, "inputs": {"draft": "hi"}, "background": True}
    )
    run_id = response.json()["run_id"]
    _until(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "paused")
    first = _spans_of(spans, run_id)
    paused = next(s for s in first if s.name == "step check")
    assert paused.attributes["easychain.paused"] is True
    client.post(f"/api/runs/{run_id}/resume", json={"answer": {"action": "approve"}})
    _until(lambda: client.get(f"/api/runs/{run_id}").json()["status"] == "ok")

    def both_parts() -> list[Any]:
        found = _spans_of(spans, run_id)
        return found if [s.name for s in found].count("run Approval") == 2 else []

    both = _until(both_parts)
    names = [s.name for s in both]
    assert names.count("run Approval") == 2, "one span for each part of the run"
    assert "step send" in names
    assert {s.context.trace_id for s in both} == {tracing.trace_id_of(run_id)}


def test_spans_reach_a_real_collector_and_the_test_button(client: TestClient, fake, collector):
    tracing._providers.clear()
    client.put(
        "/api/settings/tracing",
        json={
            "otlp": {
                "enabled": True,
                "endpoint": collector.url,
                "headers": {"x-api-key": "k-123"},
                "service_name": "shop-flows",
            }
        },
    )
    assert client.post("/api/settings/tracing/test").json() == {"ok": True}
    headers, body = collector.posts("/v1/traces")[0]
    assert headers["content-type"] == "application/x-protobuf"
    assert headers["x-api-key"] == "k-123"
    sent = ExportTraceServiceRequest.FromString(body)
    names = [s.name for rs in sent.resource_spans for ss in rs.scope_spans for s in ss.spans]
    assert names == ["Easy Chain test span"]
    resource = {a.key: a.value.string_value for a in sent.resource_spans[0].resource.attributes}
    assert resource["service.name"] == "shop-flows"

    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    _run(client, flow_id, "Hello?")

    def exported() -> list[str]:
        _flush()
        out = []
        for _h, raw in collector.posts("/v1/traces")[1:]:
            request = ExportTraceServiceRequest.FromString(raw)
            out += [
                s.name for rs in request.resource_spans for ss in rs.scope_spans for s in ss.spans
            ]
        return out if "run Ask" in out else []

    assert "chat gpt-4o-mini" in _until(exported)
    tracing._providers.clear()

    client.put(
        "/api/settings/tracing",
        json={"otlp": {"enabled": True, "endpoint": "http://127.0.0.1:9"}},
    )
    failed = client.post("/api/settings/tracing/test").json()
    assert failed["ok"] is False and "The collector didn't take the span" in failed["error"]


# ── tracing: LangSmith ──────────────────────────────────────────────────────


def test_runs_go_to_langsmith_with_the_workspaces_key(client: TestClient, fake, collector):
    client.put(
        "/api/settings/tracing",
        json={"langsmith": {"enabled": True, "project": "shop", "endpoint": collector.url}},
    )
    flow_id = client.post("/api/flows", json={"spec": ASK}).json()["id"]
    _run(client, flow_id, "Hello?")
    time.sleep(0.3)
    assert collector.posts("/runs") == [], "no key: nothing is sent"
    client.put("/api/secrets/LANGSMITH_API_KEY", json={"value": "lsv2_pt_test_key"})
    assert client.get("/api/settings/tracing").json()["langsmith_key"] is True
    run = _run(client, flow_id, "Hello again?")
    sent = _until(lambda: collector.posts("/runs"))
    headers, body = sent[0]
    assert headers.get("x-api-key") == "lsv2_pt_test_key"
    text = body.decode("utf-8", "replace")
    assert '"name":"Ask"' in text and "shop" in text
    assert run["run_id"] in text, "the run's id is in the trace's metadata"
    assert '"name":"answer_it"' in text, "each step is a child run"
