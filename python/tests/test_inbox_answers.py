"""Inbox answers reach the question they were given for, and none is lost (review #7, #8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from easychain.runtime.resources import open_resources
from easychain.server.db import Database, checkpoint_url
from easychain.server.hub import Hub, Invalid
from easychain.server.store import FlowStore
from easychain.server.worker import Worker
from easychain.spec import dumps_spec, parse_spec

ASK_EACH = {
    "name": "Ask about each name",
    "steps": [
        {
            "id": "start",
            "type": "input",
            "settings": {"fields": [{"name": "names", "type": "list"}]},
        },
        {
            "id": "each",
            "type": "for_each",
            "settings": {"items": "names", "item_name": "name", "save_as": "replies"},
        },
        {
            "id": "ask",
            "type": "ask_human",
            "settings": {"kind": "answer", "question": "Hi {name}?"},
        },
        {"id": "finish", "type": "output", "settings": {"fields": ["replies"]}},
    ],
    "connections": [
        {"from": "start", "to": "each"},
        {"from": "each", "to": "ask", "exit": "Each item"},
        {"from": "each", "to": "finish", "exit": "When done"},
    ],
}

APPROVAL = {
    "name": "Approval",
    "steps": [
        {"id": "start", "type": "input", "settings": {"fields": [{"name": "draft"}]}},
        {
            "id": "approve",
            "type": "ask_human",
            "settings": {"kind": "approve", "question": "Send “{draft}”?", "show": ["draft"]},
        },
        {"id": "finish", "type": "output", "settings": {"fields": ["human_answer"]}},
    ],
    "connections": [
        {"from": "start", "to": "approve"},
        {"from": "approve", "to": "finish", "exit": "Approved"},
        {"from": "approve", "to": "finish", "exit": "Rejected"},
    ],
}


@pytest.fixture
async def hub(tmp_path: Path):
    folder = tmp_path / "flows"
    folder.mkdir()
    for flow_id, data in (("ask-each", ASK_EACH), ("approval", APPROVAL)):
        (folder / f"{flow_id}.flow.yaml").write_text(dumps_spec(parse_spec(data)), encoding="utf-8")
    url = f"sqlite:///{tmp_path / 'runs.db'}"
    db = await Database.connect(url)
    resources = await open_resources(checkpoint_url(url))
    yield Hub(db, resources, FlowStore(folder))
    await resources.aclose()
    await db.close()


async def work(hub: Hub) -> None:
    """Do every queued job, one at a time, as a worker would."""
    worker = Worker(hub, name="test-worker")
    while job := await hub.db.lease(worker.name, 30):
        await worker._process(job)


async def open_items(hub: Hub, run_id: str) -> list[dict[str, Any]]:
    return [i for i in await hub.db.list_inbox(status="open") if i["run_id"] == run_id]


async def test_answers_to_several_items_given_at_once_are_all_used(hub: Hub):
    run_id = await hub.start_run(
        hub.flows.get("ask-each"), flow_id="ask-each", inputs={"names": ["Ada", "Bo"]}
    )
    await work(hub)
    first, second = sorted(await open_items(hub, run_id), key=lambda i: i["request"]["question"])
    # Both answered before any worker picks the run up again.
    await hub.answer(first["id"], {"value": "hello Ada"})
    await hub.answer(second["id"], {"value": "hello Bo"})
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "ok", run
    replies = str(run["output"]["replies"])
    assert "hello Ada" in replies and "hello Bo" in replies
    assert await open_items(hub, run_id) == [], "nobody is asked again"


async def test_answering_items_one_by_one_asks_nobody_twice(hub: Hub):
    run_id = await hub.start_run(
        hub.flows.get("ask-each"), flow_id="ask-each", inputs={"names": ["Ada", "Bo"]}
    )
    await work(hub)
    first, second = sorted(await open_items(hub, run_id), key=lambda i: i["request"]["question"])
    await hub.answer(first["id"], {"value": "hello Ada"})
    await work(hub)
    assert (await hub.db.get_run(run_id))["status"] == "paused"
    assert [i["id"] for i in await open_items(hub, run_id)] == [second["id"]]
    await hub.answer(second["id"], {"value": "hello Bo"})
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "ok", run
    assert "hello Ada" in str(run["output"]) and "hello Bo" in str(run["output"])
    assert await open_items(hub, run_id) == []


async def test_an_older_run_cant_take_answers_once_a_newer_one_ran(hub: Hub):
    spec = hub.flows.get("approval")
    old = await hub.start_run(spec, flow_id="approval", inputs={"draft": "v1"}, thread_id="t1")
    await work(hub)
    [item] = await open_items(hub, old)
    # Someone forks the run, so the conversation's latest Save Point is the fork's.
    point = next(p for p in await hub.save_points(old) if p["next"] == ["approve"])
    newer = await hub.fork(old, point["checkpoint_id"], update={"draft": "v2"})
    await work(hub)
    with pytest.raises(Invalid, match="newer run"):
        await hub.answer(item["id"], {"action": "reject"})
    assert (await hub.db.get_inbox(item["id"]))["status"] == "open", "a refused answer is kept open"
    assert (await hub.db.get_run(newer))["status"] == "paused"


async def test_a_tool_approval_without_a_clear_yes_is_a_no():
    from easychain.runtime.runner import _tool_decisions

    asking = {"action_requests": [{"name": "send_email", "args": {"to": "x"}}]}
    assert _tool_decisions(asking, {"action": "approve"}) == {"decisions": [{"type": "approve"}]}
    for unclear in ({}, {"comment": "hmm"}, "no", None):
        assert _tool_decisions(asking, unclear)["decisions"][0]["type"] == "reject"


async def test_a_very_long_step_name_fits_the_inbox(hub: Hub):
    run_id = await hub.start_run(hub.flows.get("approval"), flow_id="approval", inputs={})
    run = await hub.db.get_run(run_id)
    interrupt = {"id": "a" * 32, "step": "approve", "step_name": "Check " * 60, "request": {}}
    [item_id] = await hub.db.open_inbox_items(run, [interrupt])
    item = await hub.db.get_inbox(item_id)
    assert len(item["step_name"]) <= 200  # the column's size on Postgres
