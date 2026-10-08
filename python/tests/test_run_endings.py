"""How runs end: cancelling a finished run, a stop nobody was there to carry out, a worker
that dies between saving a run's outcome and saying so, and watchers waiting for the end."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from easychain.runtime.resources import open_resources
from easychain.server import hub as hub_module
from easychain.server.db import Database, checkpoint_url
from easychain.server.hub import Hub
from easychain.server.store import FlowStore
from easychain.server.worker import Worker
from easychain.spec import dumps_spec, parse_spec

from .conftest import input_step, output_step

QUICK = {
    "name": "Quick",
    "steps": [
        input_step("text", {"name": "marker", "required": False}),
        {
            "id": "shout",
            "type": "code",
            "settings": {
                "code": "def run(data):\n"
                "    if data.get('marker'):\n"
                "        open(data['marker'], 'a').close()\n"
                "    return {'loud': data['text'].upper()}\n"
            },
        },
        output_step("loud"),
    ],
    "connections": [{"from": "input", "to": "shout"}, {"from": "shout", "to": "output"}],
}

FOLLOW = {
    "name": "Follow",
    "steps": [input_step("loud"), output_step("loud")],
    "connections": [{"from": "input", "to": "output"}],
}


@pytest.fixture
async def hub(tmp_path: Path):
    folder = tmp_path / "flows"
    folder.mkdir()
    for flow_id, data in (("quick", QUICK), ("follow", FOLLOW)):
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


async def start(hub: Hub, **inputs: Any) -> str:
    return await hub.start_run(
        hub.flows.get("quick"), flow_id="quick", inputs={"text": "hi", **inputs}
    )


async def ends(hub: Hub, run_id: str) -> list[dict[str, Any]]:
    events = await hub.db.events_after(run_id, 0, limit=100000)
    return [e for e in events if e["type"] == "run_finished"]


async def test_cancelling_a_finished_run_changes_nothing(hub: Hub):
    run_id = await start(hub)
    await work(hub)
    assert await hub.cancel(run_id) == "ok"
    assert (await hub.db.get_run(run_id))["status"] == "ok"
    assert [e["status"] for e in await ends(hub, run_id)] == ["ok"]


async def test_a_run_stopped_while_its_worker_was_gone_is_not_run_again(hub: Hub, tmp_path: Path):
    marker = tmp_path / "ran.txt"
    run_id = await start(hub, marker=str(marker))
    # A worker takes the job and dies (its lease has run out); then someone stops the run.
    assert await hub.db.lease("gone-worker", -1)
    assert await hub.cancel(run_id) == "cancelling"
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "cancelled", run
    assert not marker.exists(), "the stopped run's steps ran"
    assert [e["status"] for e in await ends(hub, run_id)] == ["cancelled"]
    assert await hub.db.active_jobs(run_id=run_id) == []


async def test_a_worker_dying_after_saving_the_outcome_still_ends_the_run_once(hub: Hub):
    await hub.db.create_trigger("follow", "after_flow", {"source_flow_id": "quick"})
    run_id = await start(hub)
    # A worker runs the flow and saves its outcome, then dies before it closes the job, sends
    # notifications, fires after-flow triggers and writes the end.
    assert await hub.db.lease("gone-worker", -1)
    await hub.db.update_run(run_id, status="ok", output={"loud": "HI"}, finished_at=time.time())
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "ok" and run["output"] == {"loud": "HI"}
    [end] = await ends(hub, run_id)
    assert end["status"] == "ok" and end["output"] == {"loud": "HI"}
    assert await hub.db.active_jobs(run_id=run_id) == []
    # The after-flow trigger may have fired before the crash, so it isn't fired again.
    assert await hub.db.list_runs(flow_id="follow") == []


async def test_an_unexpected_problem_ends_the_run_with_an_error_event(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
):
    run_id = await start(hub)

    async def broken(version_id: str) -> Any:
        raise RuntimeError("the disk is full")

    monkeypatch.setattr(hub, "flow_from_version", broken)
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "error" and run["error"]["kind"] == "worker_error"
    [end] = await ends(hub, run_id)
    assert end["status"] == "error"
    assert end["error"]["message"] == "The worker hit an unexpected problem."


async def test_a_problem_after_the_outcome_is_saved_keeps_the_outcome(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
):
    async def broken(self: Worker, *args: Any) -> None:
        raise RuntimeError("the database went away for a moment")

    monkeypatch.setattr(Worker, "_after_flow", broken)
    run_id = await start(hub)
    await work(hub)
    run = await hub.db.get_run(run_id)
    assert run["status"] == "ok" and run["output"] == {"loud": "HI"}
    [end] = await ends(hub, run_id)
    assert end["status"] == "ok" and end["output"] == {"loud": "HI"}


async def test_watchers_wait_for_the_end_while_notifications_go_out(hub: Hub):
    run_id = await start(hub)
    # The worker has saved the outcome and is sending notifications (a slow webhook, say).
    await hub.db.update_run(run_id, status="ok")
    seen: list[str] = []

    async def watch() -> None:
        async for event in hub.tail(run_id, poll=0.05):
            seen.append(event["type"])

    watcher = asyncio.create_task(watch())
    await asyncio.sleep(1.5)
    assert not watcher.done(), "the stream closed before the run said it had finished"
    await hub.db.add_events(run_id, [{"type": "run_finished", "run_id": run_id, "status": "ok"}])
    hub.bus.notify(run_id)
    await asyncio.wait_for(watcher, 5)
    assert seen[-1] == "run_finished"

    # Following again from the end closes straight away.
    last = (await hub.db.events_after(run_id))[-1]["event_id"]
    began = time.monotonic()
    assert [e async for e in hub.tail(run_id, last, poll=0.05)] == []
    assert time.monotonic() - began < 1


async def test_watchers_get_an_end_written_while_they_look_at_the_run(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
):
    run_id = await start(hub)
    await hub.db.update_run(run_id, status="ok")
    get_run = hub.db.get_run

    async def end_written_meanwhile(rid: str) -> dict[str, Any] | None:
        run = await get_run(rid)
        if not await ends(hub, rid):
            await hub.db.add_events(rid, [{"type": "run_finished", "run_id": rid, "status": "ok"}])
        return run

    monkeypatch.setattr(hub.db, "get_run", end_written_meanwhile)

    async def follow() -> list[dict[str, Any]]:
        return [e async for e in hub.tail(run_id, poll=0.05)]

    events = await asyncio.wait_for(follow(), 5)
    assert [e["type"] for e in events] == ["run_queued", "run_finished"]


async def test_watchers_stop_waiting_if_the_end_never_comes(
    hub: Hub, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(hub_module, "FINAL_WAIT", 0.3)
    run_id = await start(hub)
    await hub.db.update_run(run_id, status="error")

    async def follow() -> list[dict[str, Any]]:
        return [e async for e in hub.tail(run_id, poll=0.05)]

    events = await asyncio.wait_for(follow(), 5)
    assert [e["type"] for e in events] == ["run_queued"]
