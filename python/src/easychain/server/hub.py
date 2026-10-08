"""Starting, resuming and watching runs: the part the API, the worker and triggers share.

A run is a database record plus jobs in the queue. Starting a run saves the exact flow
version (with its Sub-flows), creates the run and queues a job; a worker does the work
and writes events, which anyone can tail (SSE or WebSocket) from any process.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from ..runtime.loader import load_graph
from ..runtime.resources import Resources
from ..runtime.runner import compile_cached
from ..spec import FlowSpec, parse_spec, spec_json
from ..steps.flow_control import subflow_ids
from .db import FINISHED, Database
from .store import FlowNotFound, FlowStore

TERMINAL_EVENTS = {"run_finished"}
# How long a watcher waits for run_finished once a run's status is final (notifications are
# sent in between: a webhook can take 10 s, an email 15 s).
FINAL_WAIT = 60.0


class Busy(Exception):
    """The conversation is still busy and the flow rejects new messages until it's done."""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


@dataclass
class EventBus:
    """Wakes up watchers in this process when a run has new events or a job is queued."""

    _runs: dict[str, asyncio.Event] = field(default_factory=dict)
    jobs: asyncio.Event = field(default_factory=asyncio.Event)
    # Runs a worker in this process is running: run id -> its stop signal.
    running: dict[str, asyncio.Event] = field(default_factory=dict)

    def notify(self, run_id: str) -> None:
        ev = self._runs.get(run_id)
        if ev is not None:
            ev.set()

    async def wait(self, run_id: str, timeout: float) -> None:
        ev = self._runs.setdefault(run_id, asyncio.Event())
        try:
            await asyncio.wait_for(ev.wait(), timeout)
        except TimeoutError:
            pass
        finally:
            ev.clear()

    def job_added(self) -> None:
        self.jobs.set()

    def stop(self, run_id: str) -> None:
        """Stop a run straight away if a worker in this process is running it."""
        ev = self.running.get(run_id)
        if ev is not None:
            ev.set()


class Hub:
    def __init__(self, db: Database, resources: Resources, flows: FlowStore, vault: Any = None):
        self.db = db
        self.resources = resources
        self.flows = flows
        self.vault = vault
        self.bus = EventBus()

    # ── flow versions ────────────────────────────────────────────────────────

    def bundle(self, spec: FlowSpec) -> dict[str, Any]:
        """The flow and every flow it uses as a Sub-flow (as they are now)."""
        children: dict[str, Any] = {}
        todo = list(subflow_ids(spec))
        while todo:
            flow_id = todo.pop()
            if flow_id in children:
                continue
            try:
                child = self.flows.get(flow_id)
            except (FlowNotFound, Exception):
                continue
            children[flow_id] = spec_json(child)
            todo.extend(subflow_ids(child))
        return {"spec": spec_json(spec), "children": children}

    async def flow_from_version(self, version_id: str) -> tuple[FlowSpec, dict[str, FlowSpec]]:
        version = await self.db.get_version(version_id)
        if version is None:
            raise NotFound("That flow version is gone.")
        bundle = version["bundle"]
        children = {k: parse_spec(v) for k, v in (bundle.get("children") or {}).items()}
        return parse_spec(bundle["spec"]), children

    # ── starting runs ────────────────────────────────────────────────────────

    async def start_run(
        self,
        spec: FlowSpec,
        *,
        flow_id: str | None,
        inputs: dict[str, Any] | None = None,
        thread_id: str | None = None,
        stand_in: bool = False,
        pause_before: list[str] | None = None,
        pause_after: list[str] | None = None,
        trigger: str = "manual",
        trigger_id: str | None = None,
        parent_run_id: str | None = None,
        action: str = "start",
        payload: dict[str, Any] | None = None,
    ) -> str:
        thread = thread_id or uuid.uuid4().hex
        payload = dict(payload or {})
        if thread_id and action == "start":
            payload.update(await self._double_text(spec, thread))
        version_id = await self.db.save_version(flow_id, self.bundle(spec))
        options = {
            "stand_in": stand_in,
            "pause_before": list(pause_before or []),
            "pause_after": list(pause_after or []),
            "flow_limit": spec.settings.max_concurrent_runs,
        }
        run_id = await self.db.create_run(
            flow_id=flow_id,
            flow_name=spec.name,
            version_id=version_id,
            thread_id=thread,
            inputs=inputs or {},
            options=options,
            action=action,
            payload=payload,
            trigger=trigger,
            trigger_id=trigger_id,
            parent_run_id=parent_run_id,
            flow_limit=spec.settings.max_concurrent_runs,
        )
        self.bus.job_added()
        return run_id

    async def _double_text(self, spec: FlowSpec, thread_id: str) -> dict[str, Any]:
        """Apply the flow's policy when a conversation is still busy with an earlier run."""
        active = {j["run_id"] for j in await self.db.active_jobs(thread_id=thread_id)}
        # A run still waiting for a person is replaced by the new message.
        for run in await self.db.list_runs(thread_id=thread_id, status="paused"):
            await self.cancel(run["id"])
        if not active:
            return {}
        policy = spec.settings.double_texting
        if policy == "reject":
            raise Busy("This conversation is still busy with the previous message.")
        if policy in ("interrupt", "rollback"):
            for run_id in active:
                await self.cancel(run_id)
            if policy == "rollback":
                return {"rollback": sorted(active)}
        return {}

    async def check_resumable(self, run_id: str) -> dict[str, Any]:
        """The run, if it is waiting for an answer and can take one; else Invalid."""
        run = await self._run(run_id)
        if run["status"] != "paused":
            raise Invalid("This run isn't waiting for an answer.")
        await self._check_newest(run)
        return run

    async def resume(self, run_id: str, answers: dict[str, Any]) -> None:
        """Answer one or more waiting Ask a Human steps ({interrupt_id: answer})."""
        await self.check_resumable(run_id)
        await self.db.enqueue(run_id, "resume", {"resume": answers})
        self.bus.job_added()

    async def continue_run(self, run_id: str, step: bool = False) -> None:
        """Carry on after a breakpoint, an error or a cancel (``step``: run the next step, then pause)."""
        run = await self._run(run_id)
        if run["status"] in ("queued", "running"):
            raise Invalid("This run is already going.")
        if run["status"] == "paused" and (run.get("pending") or {}).get("reason") == "ask_human":
            raise Invalid("This run is waiting for an answer in the Inbox.")
        await self._check_newest(run)
        await self.db.enqueue(run_id, "continue", {"step": step} if step else {})
        self.bus.job_added()

    async def _check_newest(self, run: dict[str, Any]) -> None:
        """Answers and "continue" act on the conversation's latest Save Point, which belongs to its
        newest run; an older run carrying on would work on another run's state."""
        newest = await self.db.list_runs(thread_id=run["thread_id"], limit=1)
        if newest and newest[0]["id"] != run["id"]:
            raise Invalid(
                "A newer run has happened in this conversation since this one, so this one can't "
                "carry on. Continue the newest run, or start again from one of this run's Save "
                "Points."
            )

    async def fork(
        self,
        run_id: str,
        checkpoint_id: str,
        update: dict[str, Any] | None = None,
        pause_before: list[str] | None = None,
        pause_after: list[str] | None = None,
    ) -> str:
        """Re-run from a Save Point of an earlier run (optionally with changed Flow Data)."""
        run = await self._run(run_id)
        spec, _ = await self.flow_from_version(run["version_id"])
        options = run.get("options") or {}
        return await self.start_run(
            spec,
            flow_id=run["flow_id"],
            thread_id=run["thread_id"],
            stand_in=bool(options.get("stand_in")),
            pause_before=pause_before,
            pause_after=pause_after,
            trigger="fork",
            parent_run_id=run_id,
            action="fork",
            payload={"checkpoint_id": checkpoint_id, "update": update or None},
        )

    async def answer(self, item_id: str, answer: Any, by: str | None = None) -> dict[str, Any]:
        found = await self.db.get_inbox(item_id)
        if found is None:
            raise NotFound("That Inbox item doesn't exist.")
        await self._check_newest(await self._run(found["run_id"]))
        item = await self.db.answer_inbox(item_id, answer, by)
        if item is None:
            raise Invalid("Someone already answered this, or the run was stopped.")
        run = await self._run(item["run_id"])
        if run["status"] == "paused":
            await self.db.enqueue(run["id"], "resume", {"resume": {item["interrupt_id"]: answer}})
            self.bus.job_added()
        return item

    async def cancel(self, run_id: str) -> str:
        await self._run(run_id)
        status = await self.db.request_cancel(run_id)
        self.bus.stop(run_id)
        self.bus.notify(run_id)
        return status

    async def _run(self, run_id: str) -> dict[str, Any]:
        run = await self.db.get_run(run_id)
        if run is None:
            raise NotFound("That run doesn't exist.")
        return run

    # ── watching runs ────────────────────────────────────────────────────────

    async def tail(
        self,
        run_id: str,
        after: int = 0,
        *,
        until: str = "segment",
        poll: float = 0.25,
        stop: asyncio.Event | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Events of a run from ``after`` on, as they arrive.

        ``until="segment"`` stops after the next run_finished event (done, failed, cancelled
        or paused); ``until="end"`` keeps going through pauses until the run is over.
        Either way it stops straight away for a run that has nothing more to say, or when
        ``stop`` is set.

        A worker saves the run's status, then sends notifications (which can take a while),
        then writes run_finished; so once the status is final this waits for that event, up
        to ``FINAL_WAIT`` seconds.
        """
        last = after
        final_since: float | None = None
        ends = (*FINISHED, "paused") if until == "segment" else FINISHED
        while stop is None or not stop.is_set():
            events = await self.db.events_after(run_id, last)
            for ev in events:
                last = ev["event_id"]
                yield ev
                if ev.get("type") in TERMINAL_EVENTS and ev.get("status") in ends:
                    return
            if events:
                continue
            run = await self.db.get_run(run_id)
            if run is None:
                return
            if run["status"] in ends:
                end = await self.db.end_event_id(run_id, run["status"])
                if end is not None and end <= last:
                    return  # the watcher is already past the end (it reconnected after it)
                if end is not None:
                    continue  # written just now: read it
                final_since = final_since or time.monotonic()
                if time.monotonic() - final_since > FINAL_WAIT:
                    return  # the end was never written (a worker died before it could)
            else:
                final_since = None
            await self.bus.wait(run_id, poll)

    # ── Save Points ──────────────────────────────────────────────────────────

    async def _graph(self, run: dict[str, Any]) -> Any:
        spec, children = await self.flow_from_version(run["version_id"])
        compiled = compile_cached(spec, children.get, run["flow_id"])
        _, graph = await asyncio.to_thread(
            load_graph, compiled.source, compiled.module_name, self.resources
        )
        return graph

    async def save_points(self, run_id: str) -> list[dict[str, Any]]:
        """Every Save Point in the run's conversation, newest first, marked with its run.

        Secret values are hidden, as in the run's events.
        """
        from ..runtime import to_jsonable
        from ..runtime.runner import Redactor

        run = await self._run(run_id)
        graph = await self._graph(run)
        if hasattr(self.vault, "reload"):
            self.vault.reload()  # secrets saved by another process
        redact = Redactor(self.vault.values() if self.vault is not None else [])
        out = []
        async for snap in graph.aget_state_history(
            {"configurable": {"thread_id": run["thread_id"]}}
        ):
            md = snap.metadata or {}
            out.append(
                {
                    "checkpoint_id": snap.config["configurable"]["checkpoint_id"],
                    "parent_id": (snap.parent_config or {})
                    .get("configurable", {})
                    .get("checkpoint_id"),
                    "run_id": md.get("run_id"),
                    "step_number": md.get("step"),
                    "source": md.get("source"),
                    "next": list(snap.next),
                    "created_at": snap.created_at,
                    "values": redact(to_jsonable(snap.values)),
                    "waiting": [redact(to_jsonable(i.value)) for i in snap.interrupts],
                }
            )
        return out
