"""The Agent step: tools from other steps, Add-ons, approvals in the Inbox, structured answers."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from easychain.compiler import compile_flow
from easychain.compiler.validate import validate
from easychain.runtime import RunOptions, run_flow
from easychain.runtime.resources import open_resources
from easychain.runtime.standin import Script
from easychain.testing.fake_openai import FakeOpenAI

from .conftest import input_step, make_spec, output_step


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def agent_flow(base: str, *, agent: dict | None = None, extra_steps: list[dict] | None = None):
    settings = {
        "input": "question",
        "tools": ["read_page", "send_note"],
        **(agent or {}),
    }
    return make_spec(
        [
            input_step("question", {"name": "user_id", "required": False}),
            {"id": "helper", "type": "agent", "name": "Helper", "settings": settings},
            {
                "id": "read_page",
                "type": "http_request",
                "description": "Reads a page about a topic.",
                "settings": {"url": base + "/pages/{page_name}", "save_as": "page"},
            },
            {
                "id": "send_note",
                "type": "http_request",
                "description": "Sends a note to the team.",
                "settings": {
                    "method": "POST",
                    "url": base + "/effects",
                    "body": '{"note": "{note}"}',
                    "response": "json",
                    "save_as": "receipt",
                },
            },
            *(extra_steps or []),
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output")],
        data=[{"name": "page_name", "description": "Short name of the page, like bees."}],
    )


@pytest.fixture
def shop():
    with FakeOpenAI() as fake:
        httpx.post(f"{fake.url}/effects/reset")
        yield fake


async def run(spec, inputs=None, **opts: Any):
    opts.setdefault("stand_in", True)
    return await run_flow(spec, inputs, RunOptions(**opts))


async def test_agent_calls_a_step_as_a_tool_and_the_trace_shows_it(shop):
    final, events = await run(
        agent_flow(shop.url), {"question": "Tell me about bees. page_name: bees"}
    )
    assert final["status"] == "ok", final
    started = of(events, "tool_started")
    assert [(e["step"], e["tool"], e["args"]) for e in started] == [
        ("helper", "read_page", {"page_name": "bees"})
    ]
    done = of(events, "tool_finished")[0]
    assert done["status"] == "success" and "Honey bees" in done["result"]
    assert done["call_id"] == started[0]["call_id"]
    assert "colonies" in final["output"]["answer"]
    tokens = [e for e in events if e["type"] == "token" and e["step"] == "helper"]
    assert tokens, "the agent's reply streams as tokens"
    finished = [e for e in of(events, "step_finished") if e["step"] == "helper"][0]
    assert finished["usage"]["input_tokens"] > 0  # both model calls, counted on the Agent step


async def test_tool_approval_waits_in_the_inbox_shape_and_runs_once_approved(shop):
    spec = agent_flow(shop.url, agent={"addons": {"approve_tools": ["send_note"]}})
    opts = RunOptions(
        stand_in=True,
        thread_id="approve-1",
        script=Script([{"call": "send_note", "args": {"note": "hi"}}, "Sent it."]),
    )
    final, events = await run_flow(spec, {"question": "Please send a note"}, opts)
    assert final["status"] == "paused" and final["reason"] == "ask_human"
    waiting = final["interrupts"][0]
    assert waiting["step"] == "helper"
    assert waiting["request"] == {
        "kind": "approve_tool",
        "question": "The agent wants to use send_note. Do you approve?",
        "actions": [{"tool": "send_note", "args": {"note": "hi"}}],
        "allowed": ["approve", "edit", "reject"],
    }
    assert of(events, "step_paused")[0]["request"]["kind"] == "approve_tool"
    assert httpx.get(f"{shop.url}/effects").json()["applied"] == []

    opts.action, opts.resume = "resume", {waiting["id"]: {"action": "approve"}}
    final, events = await run_flow(spec, None, opts)
    assert final["status"] == "ok", final
    assert final["output"]["answer"] == "Sent it."
    applied = httpx.get(f"{shop.url}/effects").json()["applied"]
    assert [a["body"] for a in applied] == [{"note": "hi"}]


async def test_rejected_and_edited_tool_calls(shop):
    spec = agent_flow(shop.url, agent={"addons": {"approve_tools": ["send_note"]}})
    for thread, answer, expected in (
        ("reject-1", {"action": "reject", "comment": "Not now"}, []),
        ("edit-1", {"action": "approve", "value": {"note": "better"}}, [{"note": "better"}]),
    ):
        httpx.post(f"{shop.url}/effects/reset")
        script = Script([{"call": "send_note", "args": {"note": "hi"}}, "Done."])
        opts = RunOptions(stand_in=True, thread_id=thread, script=script)
        final, _ = await run_flow(spec, {"question": "send a note"}, opts)
        opts.action, opts.resume = "resume", answer
        final, events = await run_flow(spec, None, opts)
        assert final["status"] == "ok", final
        assert [a["body"] for a in httpx.get(f"{shop.url}/effects").json()["applied"]] == expected
        if thread == "reject-1":
            result = of(events, "tool_finished")
            # A rejected call never runs; the agent is told "Not now" instead.
            assert not result or "Not now" in result[0]["result"]


async def test_structured_answer_is_spread_into_flow_data(shop):
    spec = agent_flow(
        shop.url,
        agent={
            "output": {
                "spread": True,
                "fields": [
                    {"name": "reply", "description": "The answer"},
                    {"name": "topic", "type": "choice", "options": ["animals", "food"]},
                ],
            }
        },
    )
    spec.steps[-1].settings.fields = ["answer", "reply", "topic"]
    final, _ = await run(spec, {"question": "What do bees eat? page_name: bees"})
    assert final["status"] == "ok", final
    out = final["output"]
    assert set(out["answer"]) == {"reply", "topic"}
    assert out["topic"] == "animals" or out["topic"] == "food"
    assert out["reply"] == out["answer"]["reply"]


async def test_memory_add_on_remembers_across_conversations(shop, tmp_path):
    spec = agent_flow(shop.url, agent={"tools": [], "addons": {"memory": True}})
    spec.steps = [s for s in spec.steps if s.id not in ("read_page", "send_note")]
    res = await open_resources(f"sqlite:///{tmp_path / 'mem.db'}")
    try:
        remember = Script([{"call": "remember", "args": {"fact": "Likes short answers"}}, "Noted."])
        final, events = await run(
            spec,
            {"question": "I like short answers", "user_id": "u1"},
            thread_id="first",
            resources=res,
            script=remember,
        )
        assert final["status"] == "ok", final
        recall = Script(
            [{"call": "recall", "args": {"query": "answers"}}, "You like short answers."]
        )
        final, events = await run(
            spec,
            {"question": "What do I like?", "user_id": "u1"},
            thread_id="second",
            resources=res,
            script=recall,
        )
        assert "Likes short answers" in of(events, "tool_finished")[0]["result"]
        other = Script([{"call": "recall", "args": {}}])
        _, events = await run(
            spec,
            {"question": "What do I like?", "user_id": "u2"},
            thread_id="third",
            resources=res,
            script=other,
        )
        assert of(events, "tool_finished")[0]["result"] == "Nothing remembered yet."
    finally:
        await res.aclose()


async def test_failing_tool_is_reported_to_the_agent_not_the_run(shop):
    spec = agent_flow(shop.url)
    script = Script(
        [{"call": "read_page", "args": {"page_name": "missing"}}, "I couldn't read it."]
    )
    final, events = await run(spec, {"question": "read it"}, script=script)
    assert final["status"] == "ok", final
    failed = of(events, "tool_finished")[0]
    assert failed["status"] == "error" and "404" in failed["result"]
    assert final["output"]["answer"] == "I couldn't read it."

    stop = agent_flow(shop.url, agent={"addons": {"tool_errors": "stop"}})
    script = Script([{"call": "read_page", "args": {"page_name": "missing"}}])
    final, _ = await run(stop, {"question": "read it"}, script=script)
    assert final["status"] == "error" and final["step"] == "helper"


async def test_model_call_limit_ends_the_agent(shop):
    spec = agent_flow(shop.url, agent={"addons": {"max_model_calls": 1}})
    script = Script([{"call": "read_page", "args": {"page_name": "bees"}}, "never said"])
    final, events = await run(spec, {"question": "bees"}, script=script)
    assert final["status"] == "ok", final
    assert "never said" not in str(final["output"])


async def test_a_tool_keeps_its_own_retries(shop):
    spec = agent_flow(shop.url)
    send = spec.step("send_note")
    send.settings.url = shop.url + "/effects?fail=2"  # the first two tries get a 500
    send.run.retries, send.run.retry_wait = 3, 0.01
    script = Script([{"call": "send_note", "args": {"note": "hi"}}, "Sent."])
    final, events = await run(spec, {"question": "send a note"}, script=script)
    assert final["status"] == "ok", final
    [done] = of(events, "tool_finished")
    assert done["status"] == "success", done
    log = httpx.get(f"{shop.url}/effects").json()
    assert len(log["calls"]) == 3  # retried
    assert len({c["key"] for c in log["calls"]}) == 1  # with the same idempotency key
    assert len(log["applied"]) == 1  # and sent once


async def test_a_tool_keeps_its_own_time_limit(shop):
    import time

    slow = {
        "id": "slow",
        "type": "code",
        "description": "Thinks for a long time.",
        "run": {"timeout": 0.3},
        "settings": {
            "code": "import time\n\ndef run(data):\n    time.sleep(3)\n    return {'y': 1}\n"
        },
    }
    spec = make_spec(
        [
            input_step("question"),
            {"id": "helper", "type": "agent", "settings": {"input": "question", "tools": ["slow"]}},
            slow,
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output")],
    )
    script = Script([{"call": "slow", "args": {}}, "It was too slow."])
    began = time.perf_counter()
    final, events = await run(spec, {"question": "think"}, script=script)
    assert time.perf_counter() - began < 2.5
    assert final["status"] == "ok", final
    [done] = of(events, "tool_finished")
    assert done["status"] == "error"
    assert "longer than its time limit (0.3 s)" in done["result"]
    assert final["output"]["answer"] == "It was too slow."


async def test_with_run_policy_retries_like_langgraph():
    from langgraph.errors import GraphInterrupt
    from langgraph.types import RetryPolicy

    from easychain.compiler.helpers import HELPERS

    namespace: dict[str, Any] = {}
    helper = HELPERS["with_run_policy"]
    head = "\n".join(f"import {m}" for m in helper.imports)
    head += "".join(f"\nfrom {m} import {n}" for m, n in helper.from_imports)
    exec(head + "\n" + helper.code, namespace)
    with_run_policy = namespace["with_run_policy"]
    retry = RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False)
    calls: list[str] = []

    def flaky(data: dict) -> dict:
        calls.append("flaky")
        if len(calls) < 3:
            raise ConnectionError("dropped")
        return {"ok": data["n"]}

    assert await with_run_policy(flaky, {"n": 1}, retry=retry) == {"ok": 1}
    assert len(calls) == 3

    def wrong(data: dict) -> dict:
        calls.append("wrong")
        raise ValueError("bad input")  # retrying won't help, as in LangGraph

    with pytest.raises(ValueError):
        await with_run_policy(wrong, {}, retry=retry)
    assert calls.count("wrong") == 1

    async def pauses(data: dict) -> dict:
        calls.append("pauses")
        raise GraphInterrupt(())

    with pytest.raises(GraphInterrupt):
        await with_run_policy(pauses, {}, retry=retry)
    assert calls.count("pauses") == 1


def test_caching_a_tool_is_flagged(shop):
    spec = agent_flow(shop.url)
    spec.step("read_page").run.cache = True
    found = [i for i in validate(spec) if i.code == "tool_run_policy_unused"]
    assert [(i.step, i.level) for i in found] == [("read_page", "warning")]
    assert "Reuse results" in found[0].message


async def test_sub_flow_tool_makes_the_agent_async(shop):
    from .test_compiler_golden import resolve

    spec = make_spec(
        [
            input_step("question"),
            {
                "id": "helper",
                "type": "agent",
                "settings": {"input": "question", "tools": ["shout"]},
            },
            {
                "id": "shout",
                "type": "subflow",
                "description": "Shouts a topic.",
                "settings": {
                    "flow": "shout_one",
                    "inputs": {"topic": "{topic}"},
                    "outputs": {"loud": "loud"},
                },
            },
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output")],
    )
    compiled = compile_flow(
        spec, resolve=lambda fid: _shout() if fid == "shout_one" else resolve(fid)
    )
    assert "def shout_tool" in compiled.source


def _shout():
    return make_spec(
        [
            input_step("topic"),
            {
                "id": "loud_code",
                "type": "code",
                "settings": {
                    "code": "def run(data):\n    return {'loud': data['topic'].upper()}\n"
                },
            },
            output_step("loud"),
        ],
        [("input", "loud_code"), ("loud_code", "output")],
        name="Shout one",
    )


def test_checks_for_agent_tools(shop):
    spec = make_spec(
        [
            input_step("question"),
            {
                "id": "helper",
                "type": "agent",
                "settings": {
                    "input": "question",
                    "tools": ["read_page", "decide", "ghost"],
                    "addons": {"approve_tools": ["nope"], "emulate_tools": True},
                },
            },
            {"id": "read_page", "type": "http_request", "settings": {"url": "https://x/{page}"}},
            {"id": "decide", "type": "decision", "settings": {}},
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output"), ("read_page", "output")],
    )
    codes = {(i.code, i.step) for i in validate(spec)}
    assert ("agent_tool_missing", "helper") in codes
    assert ("agent_tool_type", "decide") in codes
    assert ("agent_tool_connected", "read_page") in codes
    assert ("tool_no_description", "read_page") in codes
    assert ("approve_unknown_tool", "helper") in codes
    assert ("agent_emulated_tools", "helper") in codes
    # A step used as a tool isn't "not connected to Input", and its inputs come from the agent.
    assert ("unreachable", "read_page") not in codes
    assert ("missing_field", "read_page") not in codes

    bare = make_spec(
        [
            input_step("question"),
            {"id": "helper", "type": "agent", "settings": {}},
            output_step("answer"),
        ],
        [("input", "helper"), ("helper", "output")],
    )
    assert "agent_no_tools" in {i.code for i in validate(bare)}


def test_tool_arguments_are_what_flow_data_lacks(shop):
    spec = agent_flow(shop.url)
    spec.steps[2].settings.url = shop.url + "/pages/{page_name}?asked={question}"
    compiled = compile_flow(spec)
    # `question` is already in Flow Data, so only page_name is left for the agent to fill in.
    assert (
        'def run_tool(page_name: Annotated[str, "Short name of the page, like bees."])'
        in compiled.source
    )
    assert '"page_name": page_name' in compiled.source
