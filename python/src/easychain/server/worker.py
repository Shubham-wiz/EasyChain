"""The worker: takes jobs from the queue and runs them.

Run it inside the API process (``easychain dev`` does) or as its own process
(``easychain worker``), as many as you like against Postgres.

Each job is leased and the lease is renewed every few seconds. If a worker dies, its lease
runs out and another worker takes the job over, carrying on from the run's last Save Point:
steps that already finished don't run again, and side effects are remembered, so nothing is
sent twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import socket
import time
import uuid
from typing import Any

from ..runtime import RunOptions, stream_run
from ..runtime.loader import load_graph
from ..runtime.runner import compile_cached
from ..spec import FlowSpec
from . import notify
from .cron import CronError, next_fire
from .db import FINISHED
from .hub import Hub

log = logging.getLogger("easychain.worker")


def worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


class EventWriter:
    """Writes a run's events in small batches (tokens are grouped every 50 ms)."""

    def __init__(self, hub: Hub, run_id: str):
        self.hub = hub
        self.run_id = run_id
        self.buffer: list[dict[str, Any]] = []
        self._timer: asyncio.Task[None] | None = None

    async def add(self, event: dict[str, Any]) -> None:
        self.buffer.append(event)
        if event.get("type") != "token" or len(self.buffer) >= 50:
            await self.flush()
        elif self._timer is None:
            self._timer = asyncio.create_task(self._later())

    async def _later(self) -> None:
        await asyncio.sleep(0.05)
        self._timer = None
        await self.flush()

    async def flush(self) -> None:
        if self._timer is not None and self._timer is not asyncio.current_task():
            self._timer.cancel()
            self._timer = None
        if not self.buffer:
            return
        batch, self.buffer = self.buffer, []
        await self.hub.db.add_events(self.run_id, batch)
        self.hub.bus.notify(self.run_id)


class Worker:
    def __init__(
        self,
        hub: Hub,
        *,
        name: str | None = None,
        concurrency: int = 4,
        lease_seconds: float = 30,
        poll: float = 0.5,
        schedules: bool = True,
    ):
        self.hub = hub
        self.db = hub.db
        self.name = name or worker_name()
        self.concurrency = concurrency
        self.lease_seconds = lease_seconds
        self.poll = poll
        self.schedules = schedules
        self.running: dict[int, asyncio.Task[None]] = {}
        self._stops: dict[int, asyncio.Event] = {}
        self._releasing: set[int] = set()

    # ── the main loop ────────────────────────────────────────────────────────

    async def run(self, stop: asyncio.Event) -> None:
        log.info("Worker %s started", self.name)
        scheduler = asyncio.create_task(self._schedule_loop(stop)) if self.schedules else None
        try:
            while not stop.is_set():
                if len(self.running) >= self.concurrency:
                    await self._wait(stop, self.poll)
                    continue
                try:
                    job = await self.db.lease(self.name, self.lease_seconds)
                except Exception:
                    log.exception("Couldn't take a job from the queue")
                    await self._wait(stop, 2)
                    continue
                if job is None:
                    self.hub.bus.jobs.clear()
                    await self._wait(stop, self.poll, jobs=True)
                    continue
                task = asyncio.create_task(self._process(job))
                self.running[job["id"]] = task
                task.add_done_callback(lambda _t, jid=job["id"]: self.running.pop(jid, None))
        finally:
            if scheduler is not None:
                scheduler.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await scheduler
            await self.shutdown()
            log.info("Worker %s stopped", self.name)

    async def _wait(self, stop: asyncio.Event, seconds: float, jobs: bool = False) -> None:
        waiters = [asyncio.create_task(stop.wait())]
        if jobs:
            waiters.append(asyncio.create_task(self.hub.bus.jobs.wait()))
        try:
            await asyncio.wait(waiters, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waiters:
                w.cancel()

    async def shutdown(self, grace: float = 5) -> None:
        """Stop running jobs and hand them back to the queue for another worker."""
        for job_id, stop in list(self._stops.items()):
            self._releasing.add(job_id)
            stop.set()
        if self.running:
            await asyncio.wait(list(self.running.values()), timeout=grace)

    # ── one job ──────────────────────────────────────────────────────────────

    async def _process(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        stop = asyncio.Event()
        self._stops[job_id] = stop
        self.hub.bus.running[job["run_id"]] = stop
        lost = asyncio.Event()
        done = asyncio.Event()
        beat = asyncio.create_task(self._heartbeat(job_id, stop, lost, done))
        try:
            await self._run_job(job, stop, lost)
        except Exception:
            log.exception("Job %s failed", job_id)
            if not lost.is_set():
                await self.db.update_run(
                    job["run_id"],
                    status="error",
                    finished_at=time.time(),
                    error={
                        "kind": "worker_error",
                        "message": "The worker hit an unexpected problem.",
                    },
                )
                await self.db.finish_job(job_id, self.name, "failed")
        finally:
            # Let the heartbeat finish its current write rather than cutting it off.
            done.set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(beat, 10)
            self._stops.pop(job_id, None)
            if self.hub.bus.running.get(job["run_id"]) is stop:
                del self.hub.bus.running[job["run_id"]]
            self._releasing.discard(job_id)
            self.hub.bus.notify(job["run_id"])

    async def _heartbeat(
        self, job_id: int, stop: asyncio.Event, lost: asyncio.Event, done: asyncio.Event
    ) -> None:
        interval = max(0.2, min(self.lease_seconds / 3, 2.0))
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(done.wait(), interval)
            if done.is_set():
                return
            try:
                cancel = await self.db.heartbeat(job_id, self.name, self.lease_seconds)
            except Exception:
                log.exception("Heartbeat failed")
                continue
            if cancel is None:
                # Another worker owns the job now; stop without touching the run.
                lost.set()
                stop.set()
                return
            if cancel:
                stop.set()

    async def _run_job(self, job: dict[str, Any], stop: asyncio.Event, lost: asyncio.Event) -> None:
        db, hub = self.db, self.hub
        run = await db.get_run(job["run_id"])
        if run is None or run["status"] in FINISHED:
            await db.finish_job(job["id"], self.name, "done")
            return
        if hasattr(hub.vault, "reload"):
            hub.vault.reload()
        spec, children = await hub.flow_from_version(run["version_id"])
        options = run.get("options") or {}
        action, payload = job["action"], dict(job.get("payload") or {})
        if job["attempts"] > 1:
            action, payload = await self._recover(run, spec, children, action, payload)
        checkpoint_id = payload.get("checkpoint_id")
        pause_after = list(options.get("pause_after") or [])
        if action == "continue" and payload.get("step"):
            # Step over: run what comes next, then pause again.
            graph = await self._graph(spec, children, run["flow_id"])
            snapshot = await graph.aget_state({"configurable": {"thread_id": run["thread_id"]}})
            pause_after += [n for n in snapshot.next if n not in pause_after]
        if action == "start" and payload.get("rollback"):
            checkpoint_id = await self._rollback_point(run, spec, children, payload["rollback"])
        from ..integrations.mcp import connections as mcp_connections

        mcp = mcp_connections(await self.db.get_setting("mcp", {}) or {})
        opts = RunOptions(
            mcp=mcp,
            thread_id=run["thread_id"],
            run_id=run["id"],
            stand_in=bool(options.get("stand_in")),
            redact=hub.vault.values() if hub.vault is not None else [],
            action=action,  # type: ignore[arg-type]
            resume=payload.get("resume"),
            checkpoint_id=checkpoint_id,
            update=payload.get("update"),
            pause_before=list(options.get("pause_before") or []),
            pause_after=pause_after,
            resources=hub.resources,
            resolve=children.get,
            flow_id=run["flow_id"],
            cancel=stop,
            finish_if_done=action == "continue",
            metadata={"worker": self.name},
        )
        writer = EventWriter(hub, run["id"])
        final: dict[str, Any] | None = None
        first_start = run.get("started_at") is None
        inputs = run.get("inputs") if action == "start" else None
        async for ev in stream_run(spec, inputs, opts):
            if lost.is_set():
                return
            if ev["type"] == "run_finished":
                final = ev  # written once the run's status and Inbox items are saved
                continue
            await writer.add(ev)
            if ev["type"] == "run_started":
                values: dict[str, Any] = {"status": "running"}
                if first_start:
                    values["started_at"] = time.time()
                await db.update_run(run["id"], **values)
        await writer.flush()
        if lost.is_set() or final is None:
            return
        if final["status"] == "cancelled" and job["id"] in self._releasing:
            # This worker is shutting down: hand the job back so another one carries on.
            await self._release(job, run)
            return
        await self._finish(job, run, spec, final)
        # Anyone watching sees the end only after the run's status, the Inbox and any
        # notifications are in place.
        await writer.add(final)
        await writer.flush()

    async def _release(self, job: dict[str, Any], run: dict[str, Any]) -> None:
        await self.db.finish_job(job["id"], self.name, "released")
        await self.db.enqueue(run["id"], "continue", {})
        self.hub.bus.job_added()

    async def _finish(
        self, job: dict[str, Any], run: dict[str, Any], spec: FlowSpec, final: dict[str, Any]
    ) -> None:
        db = self.db
        status = final["status"]
        usage = _sum_usage(run.get("usage"), final.get("usage"))
        cost = _sum_cost(run.get("cost"), final.get("cost"), bool(run.get("usage")))
        values: dict[str, Any] = {
            "status": status,
            "usage": usage,
            "cost": cost,
            "checkpoint_id": final.get("checkpoint_id"),
            "output": final.get("output"),
            "error": final.get("error"),
            "pending": None,
        }
        if status in FINISHED:
            values["finished_at"] = time.time()
            values["duration_ms"] = round(
                (time.time() - (run.get("started_at") or run["created_at"])) * 1000, 1
            )
        if status == "paused":
            values["pending"] = {
                "reason": final.get("reason"),
                "interrupts": final.get("interrupts") or [],
                "next": final.get("next") or [],
            }
        asking = status == "paused" and final.get("reason") == "ask_human"
        items: list[str] = []
        if asking:
            # Inbox items exist before the run shows as paused.
            named = [{**i, "step_name": _step_name(spec, i)} for i in final.get("interrupts") or []]
            items = await db.open_inbox_items(run, named)
        await db.update_run(run["id"], **values)
        await db.finish_job(job["id"], self.name, "done")
        if asking:
            await self._notify(run, spec, items, final.get("interrupts") or [])
        if status in FINISHED and run.get("flow_id"):
            await self._after_flow(run, status, final.get("output") or {})

    # ── crash recovery ───────────────────────────────────────────────────────

    async def _graph(
        self, spec: FlowSpec, children: dict[str, FlowSpec], flow_id: str | None
    ) -> Any:
        compiled = compile_cached(spec, children.get, flow_id)
        _, graph = await asyncio.to_thread(
            load_graph, compiled.source, compiled.module_name, self.hub.resources
        )
        return graph

    async def _recover(
        self,
        run: dict[str, Any],
        spec: FlowSpec,
        children: dict[str, FlowSpec],
        action: str,
        payload: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        """Work out how to carry on a job whose worker stopped part-way."""
        graph = await self._graph(spec, children, run["flow_id"])
        snapshot = await graph.aget_state({"configurable": {"thread_id": run["thread_id"]}})
        mine = bool(snapshot.created_at) and (snapshot.metadata or {}).get("run_id") == run["id"]
        if not mine:
            return action, payload  # it hadn't got going: start it again
        if action == "resume":
            waiting = {i.id for i in snapshot.interrupts}
            answers = payload.get("resume")
            if isinstance(answers, dict) and waiting & set(answers):
                return "resume", payload  # the answer wasn't used yet
        log.info("Carrying on run %s from its last Save Point", run["id"])
        return "continue", {}

    async def _rollback_point(
        self,
        run: dict[str, Any],
        spec: FlowSpec,
        children: dict[str, FlowSpec],
        rolled_back: list[str],
    ) -> str | None:
        """The Save Point just before the runs being rolled back (or a fresh start)."""
        graph = await self._graph(spec, children, run["flow_id"])
        config = {"configurable": {"thread_id": run["thread_id"]}}
        drop = set(rolled_back)
        async for snap in graph.aget_state_history(config):
            if (snap.metadata or {}).get("run_id") not in drop:
                return snap.config["configurable"]["checkpoint_id"]
        await self.hub.resources.checkpointer.adelete_thread(run["thread_id"])
        return None

    # ── the Inbox and notifications ──────────────────────────────────────────

    async def _notify(
        self,
        run: dict[str, Any],
        spec: FlowSpec,
        item_ids: list[str],
        interrupts: list[dict[str, Any]],
    ) -> None:
        config = await self.db.get_setting("notifications", {})
        writer = EventWriter(self.hub, run["id"])
        for item_id, intr in zip(item_ids, interrupts, strict=False):
            if not _wants_notify(spec, intr):
                continue
            item = await self.db.get_inbox(item_id)
            if item is None:
                continue
            results = await notify.send_all(config, item)
            if results:
                await writer.add(
                    {
                        "type": "notified",
                        "run_id": run["id"],
                        "inbox_id": item_id,
                        "results": results,
                    }
                )
        await writer.flush()

    # ── triggers ─────────────────────────────────────────────────────────────

    async def _after_flow(self, run: dict[str, Any], status: str, output: dict[str, Any]) -> None:
        for trig in await self.db.list_triggers(kind="after_flow"):
            cfg = trig.get("config") or {}
            if not trig["enabled"] or cfg.get("source_flow_id") != run["flow_id"]:
                continue
            if status not in (cfg.get("on") or ["ok"]):
                continue
            try:
                await fire(self.hub, trig, output, parent_run_id=run["id"])
            except Exception:
                log.exception("After-flow trigger %s failed", trig["id"])

    async def _poll_mailbox(self, trig: dict[str, Any]) -> list[str]:
        return await _poll_mailbox_for(self.hub, trig)

    async def _schedule_loop(self, stop: asyncio.Event) -> None:
        last_cleanup = 0.0
        while not stop.is_set():
            try:
                await self.check_schedules()
                if time.time() - last_cleanup > 300:
                    last_cleanup = time.time()
                    await self.db.drop_old_tokens(time.time() - 600)
            except Exception:
                log.exception("Checking schedules failed")
            await self._wait(stop, 5)

    async def check_schedules(self, now: float | None = None) -> list[str]:
        """Fire due schedules and poll due mailboxes; returns the runs started."""
        now = now or time.time()
        started = []
        for trig in await self.db.due_schedules(now):
            cfg = trig.get("config") or {}
            if trig["kind"] == "email":
                following = now + max(15, float(cfg.get("poll_seconds") or 60))
                if await self.db.claim_schedule(trig["id"], trig["next_fire_at"], following):
                    started += await self._poll_mailbox(trig)
                continue
            try:
                following = next_fire(cfg.get("cron", ""), cfg.get("timezone"), now)
            except CronError:
                following = None
            if not await self.db.claim_schedule(trig["id"], trig["next_fire_at"], following):
                continue  # another worker fired it
            try:
                started.append(await fire(self.hub, trig, cfg.get("inputs_values") or {}))
            except Exception:
                log.exception("Scheduled trigger %s failed", trig["id"])
        return started


async def _poll_mailbox_for(hub: Hub, trig: dict[str, Any]) -> list[str]:
    from . import mail

    cfg = trig.get("config") or {}
    try:
        messages = await asyncio.to_thread(mail.fetch_unseen, cfg)
    except Exception:
        log.exception("Couldn't read the mailbox for trigger %s", trig["id"])
        return []
    started, done = [], []
    for message in messages:
        try:
            started.append(await fire(hub, trig, {k: v for k, v in message.items() if k != "uid"}))
            done.append(message["uid"])
        except Exception:
            log.exception("Email trigger %s couldn't start a run", trig["id"])
    try:
        await asyncio.to_thread(mail.mark_seen, cfg, done)
    except Exception:
        log.exception("Couldn't mark messages as seen for trigger %s", trig["id"])
    return started


def _step_name(spec: FlowSpec, interrupt: dict[str, Any]) -> str:
    """The name of the waiting step as people see it on the canvas."""
    step_id = interrupt.get("step") or ""
    if not interrupt.get("path"):
        for step in spec.steps:
            if step.id == step_id:
                return step.name or step_id
    return step_id


def _wants_notify(spec: FlowSpec, interrupt: dict[str, Any]) -> bool:
    step_id = interrupt.get("step")
    for step in spec.steps:
        if step.id == step_id and step.type == "ask_human":
            return bool(step.settings.notify)
    return True  # inside a Sub-flow: notify by default


def _sum_usage(old: dict[str, Any] | None, new: dict[str, Any] | None) -> dict[str, Any]:
    out = {"input_tokens": 0, "output_tokens": 0}
    for part in (old, new):
        for key in out:
            out[key] += int((part or {}).get(key, 0) or 0)
    return out


def _sum_cost(old: float | None, new: float | None, had_old: bool) -> float | None:
    if new is None or (had_old and old is None):
        return None
    return (old or 0.0) + new


def map_inputs(config: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Trigger inputs: ``{"field": "{other}"}`` picks a value, other text is filled in.

    Without a mapping, the data is passed on as it is.
    """
    mapping = config.get("inputs") or {}
    if not mapping:
        return dict(data)
    out: dict[str, Any] = {}
    for name, template in mapping.items():
        text = str(template)
        whole = re.fullmatch(r"\{([A-Za-z_][A-Za-z0-9_.]*)\}", text.strip())
        if whole:
            out[name] = _lookup(data, whole.group(1))
        else:
            out[name] = re.sub(
                r"\{([A-Za-z_][A-Za-z0-9_.]*)\}",
                lambda m: (
                    "" if _lookup(data, m.group(1)) is None else str(_lookup(data, m.group(1)))
                ),
                text,
            )
    return out


def _lookup(data: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        if not isinstance(data, dict):
            return None
        data = data.get(part)
    return data


async def fire(hub: Hub, trig: dict[str, Any], data: dict[str, Any], **extra: Any) -> str:
    """Start a run of the trigger's flow."""
    spec = hub.flows.get(trig["flow_id"])
    cfg = trig.get("config") or {}
    run_id = await hub.start_run(
        spec,
        flow_id=trig["flow_id"],
        inputs=map_inputs(cfg, data),
        stand_in=bool(cfg.get("stand_in")),
        trigger=trig["kind"],
        trigger_id=trig["id"],
        **extra,
    )
    await hub.db.update_trigger(trig["id"], last_fired_at=time.time(), last_run_id=run_id)
    return run_id
