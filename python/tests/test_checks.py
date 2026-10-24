"""Checks and the Test Sets API (Phase 5.1, checks.py, server/testsets_api.py): every kind of
check passes and fails as it should and says why; budgets add up every part of a run; the AI
judge grades with a real model or is skipped without a key, never faked; a flow's Test Set is
read, saved and run from the app."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from easychain.checks import Check, RunFacts, check_value, evaluate, judge
from easychain.server.app import create_app

from .conftest import input_step, output_step
from .test_api_phase2 import APPROVAL, code

ECHO = {
    "name": "Echo",
    "steps": [
        input_step("text"),
        code("shout", "    return {'loud': data['text'].upper(), 'size': len(data['text'])}"),
        output_step("loud", "size"),
    ],
    "connections": [{"from": "input", "to": "shout"}, {"from": "shout", "to": "output"}],
}


def facts(*tools: str, parts: list[dict] | None = None, output: dict | None = None) -> RunFacts:
    events = [{"type": "tool_started", "tool": t} for t in tools]
    events += parts or []
    final = {"type": "run_finished", "status": "ok", "output": output or {}}
    if parts:
        final = {**parts[-1], "output": output or {}}
    return RunFacts(final, events, {"question": "Where is my order?"})


def test_values_fit_a_schema_numbers_and_choices():
    schema = {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}}
    assert check_value({"id": 3}, {"schema": schema}) == []
    assert check_value('{"id": 3}', {"schema": schema}) == [], "a JSON answer given as text"
    assert check_value({"id": "x"}, {"schema": schema}) == [
        "doesn't fit the schema: id: 'x' is not of type 'integer'"
    ]
    assert check_value({}, {"schema": schema}) == [
        "doesn't fit the schema: 'id' is a required property"
    ]
    assert check_value(5, {"at_least": 3, "at_most": 5}) == []
    assert check_value("7", {"at_most": 5}) == ["expected at most 5, got '7'"]
    assert check_value("many", {"at_least": 1}) == ["expected a number, got 'many'"]
    assert check_value("Billing", {"one_of": ["Billing", "Sales"]}) == []
    assert check_value("Other", {"one_of": ["Billing"]}) == [
        "expected one of ['Billing'], got 'Other'"
    ]
    assert "unknown check 'similar'" in check_value("x", {"similar": "y"})[0]


def test_the_order_of_tool_calls():
    def failures(rule, *tools):
        return [
            c.message for c in evaluate({"trajectory": rule}, facts(*tools)) if c.passed is False
        ]

    assert failures(["find", "reply"], "find", "check", "reply") == []
    assert failures(["reply", "find"], "find", "reply") == [
        "expected reply → find in this order; the agent called find → reply"
    ]
    assert failures({"tools": ["find", "reply"], "order": "exact"}, "find", "check", "reply") == [
        "expected exactly find → reply; the agent called find → check → reply"
    ]
    assert failures({"tools": ["reply", "find"], "order": "any_order"}, "find", "reply") == []
    assert failures({"tools": ["find", "find"], "order": "any_order"}, "find") == [
        "expected all of find, find; the agent called find"
    ]
    assert failures(["find"]) == ["expected find in this order; the agent called no tools"]
    assert "unknown order" in failures({"tools": ["a"], "order": "sideways"}, "a")[0]
    counted = evaluate({"tool_calls": {"min": 2}}, facts("find"))
    assert [c.message for c in counted if c.passed is False] == ["expected at least 2, got 1"]


def test_budgets_add_up_every_part_of_the_run():
    parts = [
        {"type": "run_finished", "status": "paused", "duration_ms": 1200, "cost": 0.004,
         "usage": {"input_tokens": 300, "output_tokens": 100}},
        {"type": "run_finished", "status": "ok", "duration_ms": 900, "cost": 0.003,
         "usage": {"input_tokens": 200, "output_tokens": 50}},
    ]  # fmt: skip
    run = facts(parts=parts)
    assert run.metrics() == {"seconds": 2.1, "tokens": 650, "cost": 0.007, "tools": []}
    checks = {
        c.name: c for c in evaluate({"max_seconds": 2, "max_tokens": 1000, "max_cost": 0.01}, run)
    }
    assert checks["max_seconds"].passed is False
    assert checks["max_seconds"].message == "expected at most 2 s, took 2.1 s"
    assert checks["max_tokens"].passed is True and checks["max_cost"].passed is True
    unpriced = facts(parts=[{**parts[1], "cost": None}])
    skipped = next(c for c in evaluate({"max_cost": 0.01}, unpriced) if c.name == "max_cost")
    assert skipped.passed is None and "price isn't known" in skipped.message


async def test_the_judge_is_skipped_without_a_model_key_or_with_the_stand_in(fake_openai):
    rubric = {"rubric": "Says when the order arrives."}
    with_stand_in = await judge(rubric, facts(output={"a": "Soon"}), stand_in=True)
    assert with_stand_in.passed is None and "stand-in AI" in with_stand_in.message
    assert (await judge({"rubric": ""}, facts())).passed is False


async def test_the_judge_is_skipped_without_a_model_key():
    verdict = await judge({"rubric": "Says when the order arrives."}, facts(output={"a": "Soon"}))
    assert verdict.passed is None
    assert "no AI model has a key" in verdict.message


async def test_the_judge_grades_with_a_real_model(fake_openai):
    run = facts(output={"answer": "Your order arrives on Friday."})
    httpx.post(
        f"{fake_openai.url}/__script",
        json=[{"answer": {"score": 0.9, "reason": "Gives the day."}}],
    )
    passed = await judge({"rubric": "Says when the order arrives.", "pass_score": 0.8}, run)
    assert passed == Check(
        "judge", True, "score 0.90 (needs 0.80) from openai:gpt-4.1: Gives the day."
    )
    httpx.post(f"{fake_openai.url}/__script", json=[{"answer": {"score": 0.3, "reason": "Vague."}}])
    failed = await judge("Says when the order arrives.", run)
    assert failed.passed is False and failed.message.startswith("score 0.30 (needs 0.70)")


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


def test_a_flows_test_set_is_read_saved_and_run(client: TestClient, tmp_path: Path):
    flow_id = client.post("/api/flows", json={"spec": ECHO}).json()["id"]
    empty = client.get(f"/api/flows/{flow_id}/tests").json()
    assert empty["exists"] is False and empty["cases"] == []

    bad = client.put(
        f"/api/flows/{flow_id}/tests",
        json={
            "cases": [
                {"name": "A", "inputs": "hi"},
                {"name": "A", "expect": {"output.loud": {"similar": 1}}},
            ]
        },
    )
    assert bad.status_code == 422
    assert bad.json()["problems"] == [
        "“A”: inputs should be a mapping of field: value.",
        "Two cases are called “A”; give each its own name.",
        "“A”: output.loud has an unknown check similar (use one of: equals, contains, "
        "not_contains, matches, not_empty, max_length, min_length, count, schema, at_least, "
        "at_most, one_of).",
    ]
    cases = [
        {
            "name": "Shouts",
            "inputs": {"text": "hi"},
            "expect": {"output.loud": "HI", "max_seconds": 30},
        },
        {
            "name": "Counts",
            "inputs": {"text": "${word}"},
            "expect": {"output.size": {"at_least": 10}},
        },
        {"name": "Judged", "inputs": {"text": "ok"}, "expect": {"judge": "Is loud."}},
    ]
    saved = client.put(f"/api/flows/{flow_id}/tests", json={"cases": cases, "stand_in": True})
    assert saved.json() == {"saved": True, "cases": 3}
    on_disk = yaml.safe_load((tmp_path / "flows" / f"{flow_id}.tests.yaml").read_text("utf-8"))
    assert on_disk["flow"] == f"{flow_id}.flow.yaml" and on_disk["stand_in"] is True
    read = client.get(f"/api/flows/{flow_id}/tests").json()
    assert read["exists"] and read["cases"] == cases and read["variables"] == ["word"]

    missing = client.post(f"/api/flows/{flow_id}/tests/run", json={})
    assert missing.status_code == 422 and missing.json()["variables"] == ["word"]
    ran = client.post(
        f"/api/flows/{flow_id}/tests/run", json={"variables": {"word": "four"}}
    ).json()
    assert (ran["passed"], ran["total"], ran["stand_in"]) == (2, 3, True)
    shouts, counts, judged = ran["results"]
    assert shouts["passed"] and shouts["output"] == {"loud": "HI", "size": 2}
    assert [c["name"] for c in shouts["checks"]] == ["status", "output.loud", "max_seconds"]
    assert counts["failures"] == ["output.size: expected at least 10, got 4"]
    assert judged["passed"] and judged["skipped"] == [
        "judge: skipped: the case ran with the stand-in AI; the judge grades real models' answers"
    ]
    assert set(shouts["metrics"]) == {"seconds", "tokens", "cost", "tools"}

    one = client.post(
        f"/api/flows/{flow_id}/tests/run", json={"cases": ["Shouts"], "spec": {**ECHO, "steps": [
            input_step("text"),
            code("shout", "    return {'loud': data['text'], 'size': 0}"),
            output_step("loud", "size"),
        ]}},
    ).json()  # fmt: skip
    assert one["results"][0]["failures"] == ["output.loud: expected 'HI', got 'hi'"], (
        "the flow as it is on the canvas"
    )
    assert (
        client.post(f"/api/flows/{flow_id}/tests/run", json={"cases": ["Nope"]}).status_code == 404
    )
    audit = {e["action"] for e in client.get("/api/audit").json()["entries"]}
    assert {"flow.tests_save", "flow.test_run"} <= audit


def test_answers_and_scripts_run_from_the_app(client: TestClient):
    flow_id = client.post("/api/flows", json={"spec": APPROVAL}).json()["id"]
    cases = [
        {
            "name": "Approved",
            "inputs": {"draft": "hello"},
            "answers": [{"action": "approve"}],
            "expect": {"output.sent": "HELLO", "route": {"check": "Approved"}},
        },
        {"name": "Never asked twice", "inputs": {"draft": "x"}, "answers": [{"action": "approve"}] * 2},
    ]  # fmt: skip
    client.put(f"/api/flows/{flow_id}/tests", json={"cases": cases})
    ran = client.post(f"/api/flows/{flow_id}/tests/run", json={}).json()
    assert ran["results"][0]["passed"], ran["results"][0]["failures"]
    assert ran["results"][1]["failures"] == [
        "answers: 1 answer(s) weren't used; the run didn't ask for them"
    ]


def test_the_command_line_says_which_checks_were_skipped(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    from easychain.cli import main
    from easychain.spec import dumps_spec, parse_spec

    (tmp_path / "echo.flow.yaml").write_text(dumps_spec(parse_spec(ECHO)), encoding="utf-8")
    (tmp_path / "echo.tests.yaml").write_text(
        yaml.safe_dump(
            {
                "flow": "echo.flow.yaml",
                "cases": [
                    {"name": "Judged", "inputs": {"text": "a"}, "expect": {"judge": "Loud."}}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert main(["test", str(tmp_path / "echo.tests.yaml")]) == 0
    out = capsys.readouterr().out
    assert "– judge: skipped: no AI model has a key" in out
    assert "1/1 passed (1 with checks skipped)" in out
