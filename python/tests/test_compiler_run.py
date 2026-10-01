"""Compiled flows behave as their diagrams say (run through the real runtime)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from easychain.runtime import RunOptions, run_flow
from easychain.spec import load_spec

from .conftest import TEMPLATES, input_step, make_spec, output_step

CASES = Path(__file__).parent / "golden" / "cases"


async def run(spec, inputs=None, **opts) -> tuple[dict, list[dict]]:
    return await run_flow(spec, inputs or {}, RunOptions(**opts))


def routes(events: list[dict]) -> dict[str, str]:
    return {e["step"]: e["exit"] for e in events if e["type"] == "route"}


def ran(events: list[dict]) -> list[str]:
    return [e["step"] for e in events if e["type"] == "step_finished"]


@pytest.mark.parametrize(
    "inputs,expected",
    [
        ({"subject": "Refund please", "priority": 1}, "Refund"),
        ({"subject": "hello", "priority": 1}, "Exact"),
        ({"subject": "Anything", "priority": 1}, "Not hello"),
    ],
)
async def test_rules_decision_routes(inputs, expected):
    final, events = await run(load_spec(CASES / "decision_rules.flow.yaml"), inputs)
    assert final["status"] == "ok", final
    assert routes(events) == {"route": expected}
    if expected == "Refund":
        assert final["output"] == {"handled": True}
        assert "handle" in ran(events)
    else:
        assert "handle" not in ran(events)


async def test_update_rules_and_guarded_loop():
    final, events = await run(load_spec(CASES / "update_rules.flow.yaml"), {})
    assert final["status"] == "ok", final
    out = final["output"]
    assert out["notes"] == ["round 1", "round 2", "round 3"]
    assert out["total"] == 3
    assert out["meta"] == {"r0": 0, "r1": 1, "r2": 2}
    assert out["log"] == "..."
    assert ran(events).count("tally") == 3
    assert [e["exit"] for e in events if e["type"] == "route"] == ["Again", "Again", "Done"]


async def test_input_defaults_and_types_are_applied():
    final, _ = await run(load_spec(CASES / "update_rules.flow.yaml"), {"rounds": "2"})
    assert final["output"]["total"] == 2


async def test_chat_keeps_history_per_thread_and_ai_decision_routes():
    spec = load_spec(CASES / "chat_with_fields.flow.yaml")
    final, events = await run(
        spec, {"message": "Can you help with maths homework?"}, stand_in=True, thread_id="t1"
    )
    assert final["status"] == "ok", final
    assert routes(events) == {"classify": "On topic"}
    assert final["reply"].startswith("[Stand-in AI")
    final2, events2 = await run(
        spec, {"message": "Tell me about football"}, stand_in=True, thread_id="t1"
    )
    assert routes(events2) == {"classify": "Off topic"}
    assert final2["reply"] == "Let's stick to maths!"
    assert len(final2["output"]["messages"]) == 4
    other, _ = await run(spec, {"message": "maths again"}, stand_in=True, thread_id="t2")
    assert len(other["output"]["messages"]) == 2


async def test_instructions_keep_literal_braces_and_examples():
    spec = load_spec(CASES / "instructions_examples.flow.yaml")
    final, events = await run(spec, {"text": "Ada is 36."}, stand_in=True)
    assert final["status"] == "ok", final
    prompt = next(e for e in events if e["type"] == "step_finished" and e["step"] == "prompt")
    messages = prompt["output"]["messages_for_ai"]
    assert '{"name": "Ada", "age": 36}' in messages[0]["content"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[-1]["content"] == "Ada is 36."
    assert final["output"] == {"person": {}}


class _Recorder(BaseHTTPRequestHandler):
    seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Recorder.seen.append(
            {"path": self.path, "headers": dict(self.headers), "body": json.loads(body)}
        )
        self._reply(json.dumps({"hits": [{"title": "LangGraph"}]}), "application/json")

    def do_GET(self) -> None:
        self._reply("User-agent: *\nDisallow: /private\n" * 50, "text/plain")

    def _reply(self, text: str, ctype: str) -> None:
        data = text.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def api_server():
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Recorder.seen.clear()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


async def test_http_request_fills_templates_secrets_and_parses_json(api_server, monkeypatch):
    monkeypatch.setenv("SEARCH_API_KEY", "secret-token-123")
    spec = load_spec(CASES / "http_post_json.flow.yaml")
    data = spec.model_dump(by_alias=True)
    data["steps"][1]["settings"]["url"] = api_server + "/search?limit={limit}"
    data["steps"][2]["settings"]["url"] = api_server + "/robots.txt"
    from easychain.spec import parse_spec

    final, events = await run_flow(
        parse_spec(data), {"question": 'Say "hi"'}, RunOptions(redact=["secret-token-123"])
    )
    assert final["status"] == "ok", final
    req = _Recorder.seen[0]
    assert req["path"] == "/search?limit=5"
    assert req["headers"]["Authorization"] == "Bearer secret-token-123"
    assert req["headers"]["Content-Type"] == "application/json"
    assert req["body"]["filters"] == {"lang": "en"}
    assert final["output"]["results"] == {"hits": [{"title": "LangGraph"}]}
    assert len(final["output"]["robots"]) == 500
    assert "secret-token-123" not in json.dumps(events)


async def test_url_values_are_encoded_and_json_bodies_stay_valid(api_server, monkeypatch):
    from easychain.spec import parse_spec

    monkeypatch.setenv("SEARCH_API_KEY", "k")

    spec = load_spec(CASES / "http_post_json.flow.yaml")
    data = spec.model_dump(by_alias=True)
    data["steps"][1]["settings"]["url"] = api_server + "/search?q={question}&limit={limit}"
    data["steps"][1]["settings"]["body"] = (
        '{"query": "{question}", "note": "Asked: {question}!", "limit": {limit}}'
    )
    data["steps"][2]["settings"]["url"] = api_server + "/robots.txt"
    question = 'a&b "quoted"\nnew line'
    final, _ = await run_flow(parse_spec(data), {"question": question, "limit": 7}, RunOptions())
    assert final["status"] == "ok", final
    req = _Recorder.seen[0]
    assert req["path"] == "/search?q=a%26b%20%22quoted%22%0Anew%20line&limit=7"
    assert req["body"] == {"query": question, "note": f"Asked: {question}!", "limit": 7}


async def test_smart_summary_template_against_fake_pages(fake_openai):
    spec = load_spec(TEMPLATES / "smart-summary.flow.yaml")
    final, events = await run(spec, {"url": fake_openai.url + "/pages/langchain"})
    assert final["status"] == "ok", final
    assert routes(events) == {"is_long": "Short"}
    assert 40 < final["output"]["word_count"] < 200
    assert final["output"]["summary"].startswith("[fake gpt-4o-mini]")
    usage = next(e for e in events if e["type"] == "step_finished" and e["step"] == "summarise")
    assert usage["usage"]["input_tokens"] > 0
    assert usage["cost"] is not None and usage["cost"] > 0


@pytest.mark.parametrize(
    "feedback,kind",
    [
        ("The app keeps crashing and I want a refund, this is a problem", "Complaint"),
        ("Thanks, I love the product and I'm so happy", "Praise"),
        ("How do I export my data?", "Question"),
    ],
)
async def test_reply_to_feedback_routes_with_stand_in(feedback, kind):
    spec = load_spec(TEMPLATES / "reply-to-feedback.flow.yaml")
    final, events = await run(spec, {"feedback": feedback}, stand_in=True)
    assert final["status"] == "ok", final
    assert final["output"]["kind"] == kind
    assert routes(events) == {"what_kind": kind}


async def test_parallel_branches_run_both():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "make_a",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'a': data['x'] + 'a'}\n"},
            },
            {
                "id": "make_b",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'b': data['x'] + 'b'}\n"},
            },
            output_step("a", "b"),
        ],
        [("input", "make_a"), ("input", "make_b"), ("make_a", "output"), ("make_b", "output")],
    )
    final, events = await run(spec, {"x": "1"})
    assert final["output"] == {"a": "1a", "b": "1b"}


async def test_code_error_is_pinned_to_the_step():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "boom",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'y': 1 / 0}\n"},
            },
            output_step("y"),
        ],
        [("input", "boom"), ("boom", "output")],
    )
    final, events = await run(spec, {"x": "1"})
    assert final["status"] == "error"
    failed = next(e for e in events if e["type"] == "step_failed")
    assert failed["step"] == "boom"
    assert "ZeroDivisionError" in failed["error"]["detail"]


async def test_missing_field_key_error_is_explained():
    spec = make_spec(
        [
            input_step({"name": "x", "required": False}),
            {
                "id": "use",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'y': data['x']}\n"},
            },
            output_step("y"),
        ],
        [("input", "use"), ("use", "output")],
    )
    final, _ = await run(spec, {})
    assert final["error"]["kind"] == "missing_field"
    assert "`x`" in final["error"]["message"]


async def test_endless_loop_is_stopped_with_a_clear_message():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "tick",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'n': 1}\n"},
            },
            {
                "id": "again",
                "type": "decision",
                "settings": {
                    "exits": [{"label": "Again", "when": {"field": "n", "op": "is_not_empty"}}]
                },
            },
            output_step("n"),
        ],
        [
            ("input", "tick"),
            ("tick", "again"),
            ("again", "Again", "tick"),
            ("again", "Otherwise", "output"),
        ],
    )
    final, _ = await run(spec, {"x": "1"})
    assert final["status"] == "error"
    assert final["error"]["kind"] == "too_many_steps"


async def test_run_with_invalid_flow_returns_issues():
    spec = make_spec([output_step()], [])
    final, events = await run(spec, {})
    assert final["error"]["kind"] == "invalid_flow"
    assert any(i["code"] == "no_input" for i in final["issues"])


async def test_input_type_errors_are_reported():
    spec = make_spec(
        [
            input_step(
                {"name": "n", "type": "number"},
                {"name": "flag", "type": "yes_no"},
                {"name": "obj", "type": "object"},
            ),
            {
                "id": "c",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'out': 1}\n"},
            },
            output_step("out"),
        ],
        [("input", "c"), ("c", "output")],
    )
    final, _ = await run(spec, {"n": "abc", "flag": "maybe", "obj": "{bad"})
    fields = {p["field"] for p in final["error"]["problems"]}
    assert fields == {"n", "flag", "obj"}
    ok, _ = await run(spec, {"n": "2.5", "flag": "yes", "obj": '{"a": 1}'})
    assert ok["status"] == "ok"


async def test_missing_secret_in_header_is_explained(api_server):
    from easychain.spec import parse_spec

    data = load_spec(CASES / "http_post_json.flow.yaml").model_dump(by_alias=True)
    data["steps"][1]["settings"]["url"] = api_server + "/search"
    final, _ = await run_flow(parse_spec(data), {"question": "q"}, RunOptions())
    assert final["error"]["kind"] == "bad_header"
    assert "secret" in final["error"]["hint"]
