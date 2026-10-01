"""The runtime: event stream, stand-in AI, gateway, error explanations."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from easychain.compiler import compile_flow
from easychain.runtime import MissingAPIKey, RunOptions, key_status, run_flow, to_jsonable
from easychain.runtime.errors import explain
from easychain.runtime.gateway import (
    RunSettings,
    gateway_init_chat_model,
    reset_settings,
    use_settings,
)
from easychain.runtime.loader import load_graph, load_module
from easychain.runtime.standin import StandInChatModel, stand_in_reply
from easychain.spec import load_spec

from .conftest import TEMPLATES, input_step, make_spec, output_step


def summarise_spec():
    return load_spec(TEMPLATES / "summarise-url.flow.yaml")


async def test_event_sequence_for_a_successful_run(fake_openai):
    final, events = await run_flow(
        summarise_spec(),
        {"url": fake_openai.url + "/pages/langchain"},
        RunOptions(run_id="r1", thread_id="t1"),
    )
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    started = [e["step"] for e in events if e["type"] == "step_started"]
    assert started == ["input", "fetch_page", "write_prompt", "summarise", "output"]
    tokens = [e for e in events if e["type"] == "token"]
    assert tokens and all(t["step"] == "summarise" for t in tokens)
    assert "".join(t["text"] for t in tokens) == final["output"]["summary"]
    assert all(e["run_id"] == "r1" for e in events)
    assert final["thread_id"] == "t1"
    assert final["usage"]["output_tokens"] > 0
    assert final["cost"] > 0
    summarise = next(e for e in events if e["type"] == "step_finished" and e["step"] == "summarise")
    assert summarise["model"] == "gpt-4o-mini"
    assert set(summarise["usage"]) == {"input_tokens", "output_tokens"}
    fetch_start = next(
        e for e in events if e["type"] == "step_started" and e["step"] == "fetch_page"
    )
    assert fetch_start["input"] == {"url": fake_openai.url + "/pages/langchain"}
    json.dumps(events)  # everything is JSON-ready


async def test_missing_key_offers_two_fixes():
    spec = make_spec(
        [
            input_step("q"),
            {"id": "ask", "type": "ai_model", "settings": {"prompt": "q"}},
            output_step("answer"),
        ],
        [("input", "ask"), ("ask", "output")],
    )
    final, events = await run_flow(spec, {"q": "hi"}, RunOptions())
    assert final["error"]["kind"] == "missing_key"
    assert [f["kind"] for f in final["error"]["fixes"]] == ["add_key", "use_stand_in"]
    assert final["step"] == "ask"
    assert any(e["type"] == "step_failed" and e["step"] == "ask" for e in events)


async def test_bad_key_is_explained(fake_openai, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-bad")
    spec = make_spec(
        [
            input_step("q"),
            {"id": "ask", "type": "ai_model", "settings": {"prompt": "q", "max_retries": 0}},
            output_step("answer"),
        ],
        [("input", "ask"), ("ask", "output")],
    )
    final, _ = await run_flow(spec, {"q": "hi"}, RunOptions())
    assert final["error"]["kind"] == "bad_key"
    assert "sk-bad" not in json.dumps(final)


async def test_secrets_are_redacted_from_events(fake_openai, monkeypatch):
    spec = make_spec(
        [
            input_step("q"),
            {
                "id": "echo",
                "type": "code",
                "settings": {
                    "code": "import os\n\n\ndef run(data):\n    return {'out': os.environ['OPENAI_API_KEY']}\n"
                },
            },
            output_step("out"),
        ],
        [("input", "echo"), ("echo", "output")],
    )
    final, events = await run_flow(spec, {"q": "x"}, RunOptions(redact=["another-secret"]))
    assert final["output"]["out"] == "••••••"
    assert "sk-test-key-123456" not in json.dumps(events)


def test_stand_in_replies():
    bullets = stand_in_reply(
        [HumanMessage("Summarise this.\n\nFirst point here. Second point here. Third one. Fourth.")]
    )
    assert bullets.splitlines()[1:] == [
        "- First point here.",
        "- Second point here.",
        "- Third one.",
    ]
    assert "You said" in stand_in_reply([HumanMessage("hi")])
    assert "Hello" in stand_in_reply([SystemMessage("x")])
    system = "Reply with the exit name only.\n\nExits:\n- Billing: invoices and payments\n- Tech: errors and crashes\n- Other: none of the above"
    assert (
        stand_in_reply([SystemMessage(system), HumanMessage("My invoices are wrong")]) == "Billing"
    )
    assert stand_in_reply([SystemMessage(system), HumanMessage("Nothing matches")]) == "Other"
    long = stand_in_reply([HumanMessage("x" * 400)])
    assert long.endswith("...”")


def test_stand_in_model_streams_and_reports_usage():
    model = StandInChatModel()
    chunks = list(model.stream("hello"))
    assert "".join(c.text for c in chunks).startswith("[Stand-in AI")
    assert model.invoke("hello").usage_metadata["output_tokens"] > 0


def test_gateway_modes(monkeypatch):
    token = use_settings(RunSettings(stand_in=True))
    try:
        assert isinstance(gateway_init_chat_model("openai:gpt-4o-mini"), StandInChatModel)
    finally:
        reset_settings(token)
    with pytest.raises(MissingAPIKey):
        gateway_init_chat_model("anthropic:claude-haiku-4-5")
    assert gateway_init_chat_model("ollama:llama3.2").__class__.__name__ == "ChatOllama"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert (
        gateway_init_chat_model("anthropic:claude-haiku-4-5").__class__.__name__ == "ChatAnthropic"
    )
    assert key_status() == {"openai": False, "anthropic": True, "ollama": True}


def test_loader_caches_and_swaps_init_chat_model():
    compiled = compile_flow(summarise_spec())
    module, graph = load_graph(compiled.source, compiled.module_name)
    again_module, again_graph = load_graph(compiled.source, compiled.module_name)
    assert module is again_module and graph is again_graph
    assert module.init_chat_model is gateway_init_chat_model
    fresh = load_module("x = 1\n")
    assert fresh.x == 1
    with pytest.raises(SyntaxError):
        load_module("def (:\n")


def test_to_jsonable():
    out = to_jsonable({"m": [HumanMessage("hi")], "s": {1, 2}, "o": object(), "t": "x" * 30000})
    assert out["m"] == [{"role": "user", "content": "hi"}]
    assert sorted(out["s"]) == [1, 2]
    assert out["o"].startswith("<object")
    assert "more characters" in out["t"]


class _Step:
    def __init__(self, model: str):
        self.settings = type("S", (), {"model": model})()


def _status_error(status: int, name: str):
    return type(name, (Exception,), {"status_code": status})("boom")


@pytest.mark.parametrize(
    "exc,step,kind",
    [
        (_status_error(429, "RateLimitError"), _Step("openai:gpt-4o"), "rate_limit"),
        (_status_error(404, "NotFoundError"), _Step("anthropic:claude-x"), "model_not_found"),
        (_status_error(503, "InternalServerError"), _Step("openai:gpt-4o"), "provider_down"),
        (httpx.ConnectError("refused"), _Step("ollama:llama3.2"), "ollama_down"),
        (
            httpx.ConnectTimeout("slow", request=httpx.Request("GET", "https://slow.example")),
            None,
            "timeout",
        ),
        (
            httpx.ConnectError("refused", request=httpx.Request("GET", "https://down.example")),
            None,
            "connect",
        ),
        (
            httpx.UnsupportedProtocol(
                "Request URL is missing an 'http://' or 'https://' protocol."
            ),
            None,
            "bad_url",
        ),
        (json.JSONDecodeError("Expecting value", "x", 0), None, "not_json"),
        (type("GraphRecursionError", (Exception,), {})("loop"), None, "too_many_steps"),
        (ValueError("something odd"), None, "error"),
    ],
)
def test_error_explanations(exc, step, kind):
    info = explain(exc, step)
    assert info["kind"] == kind
    assert info["message"]


@pytest.mark.parametrize(
    "status,hint",
    [
        (401, "login"),
        (403, "refused"),
        (404, "doesn't exist"),
        (429, "rate-limiting"),
        (500, "try again later"),
        (418, "Check the URL"),
    ],
)
def test_http_status_hints(status, hint):
    request = httpx.Request("GET", "https://api.example.com/x")
    response = httpx.Response(status, request=request)
    exc = httpx.HTTPStatusError("bad", request=request, response=response)
    info = explain(exc)
    assert info["kind"] == "http_status"
    assert "api.example.com" in info["message"]
    assert hint in info["hint"]


def test_proxy_block_is_explained():
    exc = httpx.ProxyError("403 Forbidden", request=httpx.Request("GET", "https://blocked.example/x"))
    info = explain(exc)
    assert info["kind"] == "blocked"
    assert "blocked.example" in info["message"]
