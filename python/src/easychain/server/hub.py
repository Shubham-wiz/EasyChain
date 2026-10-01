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

    async def resume(self, run_id: str, answers: dict[str, Any]) -> None:
        """Answer one or more waiting Ask a Human steps ({interrupt_id: answer})."""
        run = await self._run(run_id)
        if run["status"] != "paused":
            raise Invalid("This run isn't waiting for an answer.")
        await self.db.enqueue(run_id, "resume", {"resume": answers})
        self.bus.job_added()

    async def continue_run(self, run_id: str, step: bool = False) -> None:
        """Carry on after a breakpoint, an error or a cancel (``step``: run the next step, then pause)."""
        run = await self._run(run_id)
        if run["status"] in ("queued", "running"):
            raise Invalid("This run is already going.")
        if run["status"] == "paused" and (run.get("pending") or {}).get("reason") == "ask_human":
            raise Invalid("This run is waiting for an answer in the Inbox.")
        await self.db.enqueue(run_id, "continue", {"step": step} if step else {})
        self.bus.job_added()

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
        item = await self.db.answer_inbox(item_id, answer, by)
        if item is None:
            found = await self.db.get_inbox(item_id)
            if found is None:
                raise NotFound("That Inbox item doesn't exist.")
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
        """
        last = after
        idle_since = time.monotonic()
        ends = (*FINISHED, "paused") if until == "segment" else FINISHED
        while stop is None or not stop.is_set():
            events = await self.db.events_after(run_id, last)
            for ev in events:
                last = ev["event_id"]
                yield ev
                if ev.get("type") in TERMINAL_EVENTS and ev.get("status") in ends:
                    return
            if events:
                idle_since = time.monotonic()
                continue
            run = await self.db.get_run(run_id)
            if run is None:
                return
            if run["status"] in ends and time.monotonic() - idle_since > 1:
                return
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
        """Every Save Point in the run's conversation, newest first, marked with its run."""
        from ..runtime import to_jsonable

        run = await self._run(run_id)
        graph = await self._graph(run)
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
                    "values": to_jsonable(snap.values),
                    "waiting": [to_jsonable(i.value) for i in snap.interrupts],
                }
            )
        return out
