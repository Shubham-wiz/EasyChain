"""Phase 2 behaviour: For Each, Ask a Human, Jump, round limits, Sub-flows, run policies,
side effects, breakpoints, time travel and cancelling."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from easychain.compiler import compile_flow
from easychain.compiler.helpers import HELPERS
from easychain.runtime import RunOptions, run_flow, stream_run
from easychain.runtime.resources import Resources
from easychain.spec import load_spec
from easychain.spec.models import FlowSpec

from .conftest import input_step, make_spec, output_step

CASES = Path(__file__).parent / "golden" / "cases"


def resolve(flow_id: str) -> FlowSpec | None:
    path = CASES / f"{flow_id}.flow.yaml"
    return load_spec(path) if path.exists() else None


def case(name: str) -> FlowSpec:
    return load_spec(CASES / f"{name}.flow.yaml")


async def run(spec: FlowSpec, inputs: dict | None = None, **opts: Any) -> tuple[dict, list[dict]]:
    opts.setdefault("resolve", resolve)
    return await run_flow(spec, inputs or {}, RunOptions(**opts))


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def routes(events: list[dict]) -> list[tuple[str, str]]:
    return [(e["step"], e["exit"]) for e in of(events, "route") if "path" not in e]


# ── For Each ─────────────────────────────────────────────────────────────────


async def test_for_each_runs_every_item_and_keeps_their_order():
    final, events = await run(case("for_each"), {"topics": ["a", "b", "c", "d"]})
    assert final["status"] == "ok", final
    assert final["output"] == {"shouted": ["A", "B", "C", "D"]}
    body = of(events, "step_finished")
    items = sorted(e["item"] for e in body if e["step"] == "shout")
    assert items == [0, 1, 2, 3]
    progress = [(e["done"], e["total"]) for e in of(events, "progress")]
    assert progress[0] == (0, 4) and progress[-1] == (4, 4)
    assert routes(events) == [("each", "Each item"), ("each", "When done")]
    each_done = [e for e in body if e["step"] == "each"]
    assert len(each_done) == 1 and each_done[0]["output"] == {"shouted": ["A", "B", "C", "D"]}


async def test_for_each_with_an_empty_list_goes_straight_on():
    final, events = await run(case("for_each"), {"topics": []})
    assert final["status"] == "ok", final
    assert final["output"] == {"shouted": []}
    assert not [e for e in of(events, "step_started") if e["step"] == "shout"]


async def test_for_each_concurrency_limit_is_respected():
    spec = make_spec(
        [
            input_step({"name": "items", "type": "list"}),
            {
                "id": "each",
                "type": "for_each",
                "settings": {"items": "items", "concurrency": 2, "save_as": "out"},
            },
            {
                "id": "work",
                "type": "code",
                "settings": {
                    "code": "import time\n\ndef run(data):\n    time.sleep(0.2)\n"
                    "    return {'done': data['item'] * 2}\n"
                },
            },
            output_step("out"),
        ],
        [("input", "each"), ("each", "Each item", "work"), ("each", "When done", "output")],
    )
    began = time.perf_counter()
    final, _ = await run(spec, {"items": [1, 2, 3, 4]})
    elapsed = time.perf_counter() - began
    assert final["output"] == {"out": [2, 4, 6, 8]}
    assert elapsed >= 0.38, "four 0.2 s items, two at a time, take at least 0.4 s"


# ── Ask a Human ──────────────────────────────────────────────────────────────


async def test_ask_human_pauses_and_an_edit_is_applied_on_approve():
    spec = case("ask_human_edit")
    final, events = await run(spec, {"draft": "hello"}, thread_id="ask-1")
    assert final["status"] == "paused", final
    assert final["reason"] == "ask_human"
    [waiting] = final["interrupts"]
    assert waiting["step"] == "review"
    assert waiting["request"]["question"] == "Send this reply? hello"
    assert waiting["request"]["value"] == "hello"
    assert of(events, "step_paused")[0]["step"] == "review"

    answer = {"action": "approve", "value": "hello there", "comment": "nicer"}
    final, events = await run(spec, action="resume", resume=answer, thread_id="ask-1")
    assert final["status"] == "ok", final
    assert final["output"]["sent"] == "SENT: hello there"
    assert final["output"]["human_answer"] == "Approved"
    assert final["output"]["human_answer_comment"] == "nicer"
    assert ("review", "Approved") in routes(events)


async def test_ask_human_reject_takes_the_rejected_exit():
    spec = case("ask_human_edit")
    await run(spec, {"draft": "hello"}, thread_id="ask-2")
    final, events = await run(
        spec, action="resume", resume={"action": "reject", "comment": "no"}, thread_id="ask-2"
    )
    assert final["status"] == "ok"
    assert "sent" not in final["output"]
    assert final["output"]["human_answer"] == "Rejected"
    assert ("review", "Rejected") in routes(events)


async def test_choose_and_answer_pause_one_after_the_other():
    spec = case("ask_human_choose")
    final, _ = await run(spec, {"draft": "Thanks."}, thread_id="choose")
    assert final["interrupts"][0]["request"]["options"] == ["Friendly", "Formal"]
    final, events = await run(spec, action="resume", resume={"value": "formal"}, thread_id="choose")
    assert final["status"] == "paused"
    assert final["interrupts"][0]["step"] == "note"
    assert ("tone", "Formal") in routes(events)
    final, _ = await run(spec, action="resume", resume="Have a good day", thread_id="choose")
    assert final["status"] == "ok"
    assert final["output"] == {
        "reply": "Dear customer, Thanks.",
        "tone_choice": "Formal",
        "extra": "Have a good day",
    }


async def test_resuming_a_run_that_isnt_waiting_is_a_clear_error():
    final, _ = await run(case("for_each"), {"topics": ["x"]}, thread_id="done-run")
    assert final["status"] == "ok"
    final, _ = await run(case("for_each"), action="resume", resume="x", thread_id="done-run")
    assert final["status"] == "error"
    assert final["error"]["kind"] == "not_waiting"


# ── Jump and round limits ────────────────────────────────────────────────────


async def test_jump_sets_data_in_order_and_the_round_limit_ends_the_loop():
    final, events = await run(case("jump_and_round_limit"), {"limit": 10})
    assert final["status"] == "ok", final
    assert final["output"] == {"count": 2, "note": "round 2"}
    assert routes(events) == [
        ("bump", "Again"),
        ("check", "Loop"),
        ("bump", "Again"),
        ("check", "Stop"),
    ]


async def test_jump_takes_its_otherwise_exit_to_the_end():
    final, events = await run(case("jump_and_round_limit"), {"limit": 1})
    assert final["output"] == {"count": 1, "note": "round 1"}
    assert routes(events) == [("bump", "Done")]


async def test_a_loop_without_a_limit_stops_with_a_plain_message():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "work",
                "type": "code",
                "settings": {"code": "def run(data):\n    return {'y': 1}\n"},
            },
            {
                "id": "again",
                "type": "decision",
                "settings": {
                    "exits": [{"label": "Again", "when": {"field": "y", "op": "is_not_empty"}}]
                },
            },
            output_step("y"),
        ],
        [
            ("input", "work"),
            ("work", "again"),
            ("again", "Again", "work"),
            ("again", "Otherwise", "output"),
        ],
        settings={"max_steps": 6},
    )
    final, _ = await run(spec, {"x": "go"})
    assert final["status"] == "error"
    assert final["error"]["kind"] == "too_many_steps"
    assert "6 rounds" in final["error"]["message"]


# ── Sub-flows ────────────────────────────────────────────────────────────────


async def test_sub_flows_run_with_separate_and_shared_data_and_per_item():
    final, events = await run(
        case("subflows"), {"text": " Hello ", "words": ["Ab", "cD"]}, flow_id="subflows"
    )
    assert final["status"] == "ok", final
    assert final["output"] == {"clean": "HELLO", "tidied": "hello", "all_tidy": ["AB", "CD"]}
    inner = [e for e in of(events, "step_finished") if e.get("path")]
    assert {tuple(e["path"]) for e in inner} == {("separate",), ("shared",), ("per_word",)}
    assert {e["step"] for e in inner} == {"tidy"}


async def test_ask_human_inside_a_sub_flow_pauses_the_parent():
    child = make_spec(
        [
            input_step("text"),
            {
                "id": "ok",
                "type": "ask_human",
                "settings": {"kind": "answer", "question": "Say {text}"},
            },
            output_step("human_answer"),
        ],
        [("input", "ok"), ("ok", "output")],
        name="Ask inside",
    )
    parent = make_spec(
        [
            input_step("text"),
            {
                "id": "inner",
                "type": "subflow",
                "settings": {"flow": "ask_inside", "outputs": {"said": "human_answer"}},
            },
            output_step("said"),
        ],
        [("input", "inner"), ("inner", "output")],
        name="Outer",
    )
    flows = {"ask_inside": child}
    final, events = await run(parent, {"text": "hi"}, resolve=flows.get, thread_id="nested")
    assert final["status"] == "paused", final
    assert final["interrupts"][0]["step"] == "ok"
    assert final["interrupts"][0]["path"] == ["inner"]
    assert final["interrupts"][0]["request"]["question"] == "Say hi"
    final, _ = await run(
        parent, action="resume", resume="hello back", resolve=flows.get, thread_id="nested"
    )
    assert final["status"] == "ok", final
    assert final["output"] == {"said": "hello back"}


async def test_sub_flow_usage_counts_toward_the_sub_flow_step():
    child = make_spec(
        [
            input_step("question"),
            {
                "id": "answer_it",
                "type": "ai_model",
                "settings": {"prompt": "question", "save_as": "answer"},
            },
            output_step("answer"),
        ],
        [("input", "answer_it"), ("answer_it", "output")],
        name="Answer",
    )
    parent = make_spec(
        [
            input_step("question"),
            {"id": "ask", "type": "subflow", "settings": {"flow": "answer"}},
            output_step("answer"),
        ],
        [("input", "ask"), ("ask", "output")],
    )
    final, events = await run(
        parent, {"question": "Why?"}, resolve={"answer": child}.get, stand_in=True
    )
    assert final["status"] == "ok", final
    [outer] = [e for e in of(events, "step_finished") if e["step"] == "ask"]
    assert outer["usage"]["output_tokens"] > 0
    assert final["usage"]["output_tokens"] == outer["usage"]["output_tokens"]


# ── Run policies and side effects ────────────────────────────────────────────


async def test_run_policies_parallel_join_custom_rule_and_idempotent_post(fake_server):
    httpx.post(f"{fake_server.url}/effects/reset")
    spec = case("run_policies")
    spec.step("notify").settings.url = f"{fake_server.url}/effects"
    final, events = await run(spec, {"text": "hi"})
    assert final["status"] == "ok", final
    tags = final["output"]["tags"]
    assert sorted(tags) == ["left", "right", "shared"]
    assert final["output"]["summary"] == ", ".join(tags)
    assert [e["step"] for e in of(events, "step_finished")].count("join") == 1
    calls = httpx.get(f"{fake_server.url}/effects").json()["calls"]
    assert len(calls) == 1 and calls[0]["key"]


async def test_retries_send_the_same_idempotency_key(fake_server):
    spec = make_spec(
        [
            input_step("text"),
            {
                "id": "send",
                "type": "http_request",
                "run": {"retries": 3, "retry_wait": 0.01},
                "settings": {
                    "method": "POST",
                    "url": f"{fake_server.url}/effects?fail=2",
                    "body": '{"text": "{text}"}',
                    "response": "json",
                    "save_as": "receipt",
                },
            },
            output_step("receipt"),
        ],
        [("input", "send"), ("send", "output")],
    )
    httpx.post(f"{fake_server.url}/effects/reset")
    final, _ = await run(spec, {"text": "hello"})
    assert final["status"] == "ok", final
    log = httpx.get(f"{fake_server.url}/effects").json()
    assert len(log["calls"]) == 3
    assert len({c["key"] for c in log["calls"]}) == 1
    assert len(log["applied"]) == 1


def test_run_once_does_a_side_effect_once_per_key():
    from langgraph.store.memory import InMemoryStore

    namespace: dict[str, Any] = {"Any": Any}
    exec("from collections.abc import Callable\n" + HELPERS["run_once"].code, namespace)
    store = InMemoryStore()
    namespace["get_store"] = lambda: store
    calls: list[int] = []

    def action() -> dict:
        calls.append(1)
        return {"sent": len(calls)}

    assert namespace["run_once"]("k1", action) == {"sent": 1}
    assert namespace["run_once"]("k1", action) == {"sent": 1}
    assert namespace["run_once"]("k2", action) == {"sent": 2}
    assert len(calls) == 2


async def test_a_timeout_stops_a_slow_step():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "slow",
                "type": "code",
                "run": {"timeout": 0.3},
                "settings": {
                    "code": "import time\n\ndef run(data):\n    time.sleep(2)\n    return {'y': 1}\n"
                },
            },
            output_step("y"),
        ],
        [("input", "slow"), ("slow", "output")],
    )
    compiled = compile_flow(spec)
    assert compiled.is_async
    began = time.perf_counter()
    final, events = await run(spec, {"x": "go"})
    assert final["status"] == "error"
    assert final["step"] == "slow"
    assert time.perf_counter() - began < 1.5


async def test_cached_step_reuses_its_result():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "stamp",
                "type": "code",
                "run": {"cache": True},
                "settings": {
                    "code": "import time\n\ndef run(data):\n    return {'at': time.perf_counter()}\n"
                },
            },
            output_step("at"),
        ],
        [("input", "stamp"), ("stamp", "output")],
    )
    first, _ = await run(spec, {"x": "same"})
    second, _ = await run(spec, {"x": "same"})
    third, _ = await run(spec, {"x": "different"})
    assert first["output"]["at"] == second["output"]["at"]
    assert third["output"]["at"] != first["output"]["at"]


# ── Breakpoints, time travel and cancelling ──────────────────────────────────


async def test_breakpoint_pauses_before_a_step_and_continue_finishes():
    spec = case("jump_and_round_limit")
    final, events = await run(spec, {"limit": 1}, pause_before=["bump"], thread_id="bp")
    assert final["status"] == "paused"
    assert final["reason"] == "breakpoint"
    assert final["next"] == ["bump"]
    assert not of(events, "step_finished")[1:]  # only Input
    final, _ = await run(spec, action="continue", thread_id="bp")
    assert final["status"] == "ok"
    assert final["output"] == {"count": 1, "note": "round 1"}


async def test_fork_from_a_save_point_with_changed_data():
    spec = case("ask_human_edit")
    final, events = await run(spec, {"draft": "first"}, thread_id="tt")
    save_points = [e["checkpoint_id"] for e in of(events, "save_point") if e["next"] == ["review"]]
    assert save_points
    await run(spec, action="resume", resume={"action": "approve"}, thread_id="tt")
    final, events = await run(
        spec,
        action="fork",
        checkpoint_id=save_points[0],
        update={"draft": "second"},
        thread_id="tt",
    )
    assert final["status"] == "paused"
    assert final["interrupts"][0]["request"]["question"] == "Send this reply? second"
    final, _ = await run(spec, action="resume", resume={"action": "approve"}, thread_id="tt")
    assert final["output"]["sent"] == "SENT: second"


async def test_fork_from_a_missing_save_point_is_a_clear_error():
    final, _ = await run(
        case("for_each"), action="fork", checkpoint_id="nope", thread_id="nothing-here"
    )
    assert final["status"] == "error"
    assert final["error"]["kind"] == "no_save_point"


async def test_cancel_stops_a_run_quickly():
    spec = make_spec(
        [
            input_step("x"),
            {
                "id": "slow",
                "type": "code",
                "settings": {
                    "code": "import time\n\ndef run(data):\n    time.sleep(3)\n    return {'y': 1}\n"
                },
            },
            output_step("y"),
        ],
        [("input", "slow"), ("slow", "output")],
    )
    cancel = asyncio.Event()
    events: list[dict] = []
    began = time.perf_counter()
    async for ev in stream_run(spec, {"x": "go"}, RunOptions(cancel=cancel)):
        events.append(ev)
        if ev["type"] == "step_started" and ev["step"] == "slow":
            cancel.set()
    assert events[-1]["status"] == "cancelled"
    assert time.perf_counter() - began < 2


async def test_resources_are_used_for_save_points():
    from langgraph.cache.memory import InMemoryCache
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    res = Resources(InMemorySaver(), InMemoryStore(), InMemoryCache())
    final, _ = await run(case("ask_human_edit"), {"draft": "x"}, thread_id="own", resources=res)
    assert final["status"] == "paused"
    assert list(res.checkpointer.list({"configurable": {"thread_id": "own"}}))
    # The shared in-memory resources don't know this thread.
    final, _ = await run(case("ask_human_edit"), action="resume", resume={}, thread_id="own")
    assert final["status"] == "error"


@pytest.mark.parametrize("name", ["for_each", "ask_human_edit", "subflows", "run_policies"])
def test_exported_code_needs_no_easychain(name: str):
    compiled = compile_flow(case(name), resolve=resolve, flow_id=name)
    assert "easychain" not in compiled.source.split('"""', 2)[2]
