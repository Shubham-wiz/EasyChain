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

from .. import egress, environments
from ..runtime import RunOptions, stream_run
from ..runtime.loader import load_graph
from ..runtime.runner import compile_cached
from ..runtime.sandbox import SandboxSettings
from ..spec import FlowSpec
from . import notify
from .cron import CronError, next_fire
from .db import FINISHED
from .gateway import policy_for
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
            # The job is its workspace's: its flows, secrets, skills and settings (spaces.py),
            # and its environment's, if it has one (environments.py).
            workspace, environment = await self._workspace_of(job)
            with self.hub.spaces.enter(workspace, environment):
                try:
                    await self._run_job(job, stop, lost)
                except Exception:
                    log.exception("Job %s failed", job_id)
                    if not lost.is_set():
                        await self._failed(job)
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

    async def _workspace_of(self, job: dict[str, Any]) -> tuple[str, Any]:
        """The job's workspace, and its environment (None for drafts and documents)."""
        if job["action"] == "ingest":
            return (job.get("payload") or {}).get("workspace") or "default", None
        if job["action"] == "test":
            row = await self.db.get_test_run(job["run_id"]) or {}
            workspace = (job.get("payload") or {}).get("workspace") or "default"
        else:
            row = await self.db.get_run(job["run_id"]) or {}
            workspace = row.get("workspace_id") or "default"
        name = row.get("environment")
        if not name:
            return workspace, None
        key = self.hub.spaces.get(workspace).setting(f"environment:{name}")
        return workspace, environments.from_settings(name, await self.db.get_setting(key, {}))

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

    async def _egress(self) -> egress.EgressPolicy:
        """What runs and Knowledge Base pages may reach (Settings → Network)."""
        from .auth import auth_mode

        return egress.policy_from(
            await self.db.get_setting("network", {}), signed_in=auth_mode() == "on"
        )

    async def _ingest(self, job: dict[str, Any], lost: asyncio.Event) -> None:
        """Read one Knowledge Base document (queued by the API). The job's ``run_id`` is the
        document's id. Run again after a crash, it replaces what the first try wrote."""
        from ..knowledge.ingest import read_document
        from ..knowledge.store import KnowledgeStore

        source = dict(job.get("payload") or {})
        upload_id = source.get("upload_id")
        data = None
        if upload_id:
            upload = await self.db.get_upload(upload_id)
            if upload is not None:
                data = upload["data"]
                source.update(name=upload["name"], content_type=upload["content_type"])
        with egress.using(await self._egress()):
            status = await asyncio.to_thread(
                lambda: read_document(KnowledgeStore().setup(), job["run_id"], source, data)
            )
        if lost.is_set():
            return  # another worker took the job over; it finishes it
        log.info("Read document %s: %s", job["run_id"], status)
        await self.db.finish_job(job["id"], self.name, "done")
        if upload_id:
            await self.db.delete_upload(upload_id)

    async def _test(self, job: dict[str, Any], lost: asyncio.Event) -> None:
        """Run a kept Test Run (queued by the API): each case ``repeat`` times, against the
        flow version it was started with. Run again after a crash, it starts the cases over."""
        from ..integrations.mcp import connections as mcp_connections
        from ..testruns import case_summary, summary
        from ..testsets import run_case
        from .integrations_api import mcp_setting

        db, hub = self.db, self.hub
        row = await db.get_test_run(job["run_id"])
        if row is None or row["status"] in ("done", "error"):
            await db.finish_job(job["id"], self.name, "done")
            return
        await db.update_test_run(row["id"], status="running", started_at=time.time(), done=0)
        spec, children = await hub.flow_from_version(row["version_id"])
        options = {
            "mcp": mcp_connections(await mcp_setting(db, hub.space.setting("mcp"))),
            "egress": await self._egress(),
            "resolve": children.get,
            "flow_id": row["flow_id"],
            "redact": hub.space.vault.values() if hub.space.vault is not None else [],
            "gateway": await policy_for(
                hub,
                kind="test_run",
                run_id=row["id"],
                flow_id=row["flow_id"],
                flow_name=spec.name,
                environment=row.get("environment"),
                by=(None, row["created_by"]) if row.get("created_by") else None,
            ),
        }
        sandbox = sandbox_settings(await db.get_setting("sandbox", {}))
        results, done = [], 0
        for case in row.get("cases") or []:
            runs = []
            for _ in range(int(row["repeat"] or 1)):
                result = await run_case(
                    spec,
                    case,
                    bool(row["stand_in"]),
                    sandbox,
                    judge_model=row.get("judge_model"),
                    options=options,
                )
                runs.append(result.to_dict())
                done += 1
                if lost.is_set():
                    return  # another worker took the job over; it runs it again
                await db.update_test_run(row["id"], done=done)
            results.append(case_summary(case.get("name") or "case", runs))
        await db.update_test_run(
            row["id"],
            status="done",
            results=results,
            summary=summary(results),
            finished_at=time.time(),
        )
        await db.finish_job(job["id"], self.name, "done")

    async def _failed(self, job: dict[str, Any]) -> None:
        """The job hit an unexpected problem: the run ends with an error, and says so."""
        if job["action"] == "test":
            # A Test Run says it didn't finish; a problem that repeats would repeat for ever.
            with contextlib.suppress(Exception):
                await self.db.update_test_run(
                    job["run_id"],
                    status="error",
                    error="The Test Run hit an unexpected problem. Start it again.",
                    finished_at=time.time(),
                )
            await self.db.finish_job(job["id"], self.name, "failed")
            return
        if job["action"] == "ingest":
            # Not taken up again: a problem that repeats would repeat for ever. The document
            # says what happened.
            from ..knowledge.store import KnowledgeStore

            message = "Reading this hit an unexpected problem. Remove it and add it again."
            with contextlib.suppress(Exception):
                await asyncio.to_thread(
                    lambda: (
                        KnowledgeStore()
                        .setup()
                        .set_document(job["run_id"], status="error", error=message)
                    )
                )
            await self.db.finish_job(job["id"], self.name, "failed")
            return
        run = await self.db.get_run(job["run_id"])
        if run is None:
            return
        if run["status"] in (*FINISHED, "paused"):
            # Its outcome was saved before the problem (sending notifications, say): keep it.
            await self.db.finish_job(job["id"], self.name, "done")
            await self._write_missing_end(run)
            return
        error = {
            "kind": "worker_error",
            "message": "The worker hit an unexpected problem.",
            "hint": "Try the run again. If it keeps happening, the worker's log says what went "
            "wrong.",
            "fixes": [],
        }
        await self.db.update_run(run["id"], status="error", finished_at=time.time(), error=error)
        await self.db.finish_job(job["id"], self.name, "failed")
        await self._write_end(run, {"status": "error", "error": error})

    async def _write_missing_end(self, run: dict[str, Any]) -> None:
        """Write the run_finished event for the run's saved outcome, if it isn't there yet (its
        worker stopped between saving the outcome and writing the event)."""
        if await self.db.end_event_id(run["id"], run["status"]) is not None:
            return
        end: dict[str, Any] = {
            "status": run["status"],
            "output": run.get("output"),
            "usage": run.get("usage"),
            "cost": run.get("cost"),
            "checkpoint_id": run.get("checkpoint_id"),
        }
        if run.get("error"):
            end["error"] = run["error"]
        if run["status"] == "paused":
            end.update(run.get("pending") or {})
        await self._write_end(run, end)

    async def _write_end(self, run: dict[str, Any], end: dict[str, Any]) -> None:
        event = {
            "type": "run_finished",
            "run_id": run["id"],
            "ts": time.time() * 1000,
            "thread_id": run["thread_id"],
            **end,
        }
        await self.db.add_events(run["id"], [event])
        self.hub.bus.notify(run["id"])

    async def _run_job(self, job: dict[str, Any], stop: asyncio.Event, lost: asyncio.Event) -> None:
        if job["action"] == "ingest":
            await self._ingest(job, lost)
            return
        if job["action"] == "test":
            await self._test(job, lost)
            return
        db, hub = self.db, self.hub
        run = await db.get_run(job["run_id"])
        if run is None:
            await db.finish_job(job["id"], self.name, "done")
            return
        if run["status"] in FINISHED:
            # A worker finished the run but stopped before closing the job. Its after-flow
            # triggers may have fired already, so they don't fire again; the end is written if
            # it's missing, for anyone waiting on it.
            await db.finish_job(job["id"], self.name, "done")
            await self._write_missing_end(run)
            return
        spec, children = await hub.flow_from_version(run["version_id"])
        if job.get("cancel_requested"):
            # Someone stopped the run while the worker that had it was gone: don't run it again.
            # It ends as a run stopped by its own worker does (it can be continued later).
            cancelled = {
                "status": "cancelled",
                "output": run.get("output"),
                "usage": {},
                "cost": 0.0,
                "checkpoint_id": run.get("checkpoint_id"),
            }
            await self._finish(job, run, spec, cancelled)
            await self._write_end(run, cancelled)
            return
        options = run.get("options") or {}
        action, payload = job["action"], dict(job.get("payload") or {})
        handed_back = bool(payload.pop("handed_back", False))
        if job["attempts"] > 1 or handed_back:
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
        from .integrations_api import mcp_setting

        mcp = mcp_connections(await mcp_setting(self.db, hub.space.setting("mcp")))
        opts = RunOptions(
            mcp=mcp,
            sandbox=sandbox_settings(await self.db.get_setting("sandbox", {})),
            egress=await self._egress(),
            thread_id=run["thread_id"],
            run_id=run["id"],
            stand_in=bool(options.get("stand_in")),
            redact=hub.space.vault.values() if hub.space.vault is not None else [],
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
            gateway=await policy_for(
                hub,
                kind="run",
                run_id=run["id"],
                flow_id=run["flow_id"],
                flow_name=run["flow_name"],
                environment=run.get("environment"),
                by=(run.get("started_by"), run["started_by_name"])
                if run.get("started_by_name")
                else None,
            ),
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
        # Hand the job back as it was (a fork, a new chat message, an answer…). The next worker
        # works out how far it got, as after a crash (_recover): it carries on from this run's
        # last Save Point, or does the job from the start if it hadn't got going.
        await self.db.finish_job(job["id"], self.name, "released")
        payload = {**(job.get("payload") or {}), "handed_back": True}
        await self.db.enqueue(run["id"], job["action"], payload)
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
        given: dict[str, Any] = {}
        waiting = final.get("interrupts") or []
        if asking:
            # A question that is still listed may already have its answer:
            # - given while the run was busy (another For Each item's answer, say), so not sent
            #   with this job: send it now instead of asking again;
            # - sent with this job, but LangGraph keeps listing a For Each item until its
            #   siblings are answered too (it has stored the answer): don't ask again.
            # An agent's next approval has a new id. An Ask a Human "choose" that asks again keeps
            # its id, but its answer was sent, so it falls to the last case and is asked anew.
            sent = (job.get("payload") or {}).get("resume") or {}
            answered = await db.inbox_answers(run["id"], [i["id"] for i in waiting])
            unanswered = [i for i in waiting if i["id"] not in answered]
            if any(k not in sent for k in answered):
                given, waiting = answered, unanswered
            elif unanswered:
                waiting = unanswered
            # Otherwise every listed question was answered and sent, yet is back: ask again.
            # Inbox items exist before the run shows as paused.
            named = [{**i, "step_name": _step_name(spec, i)} for i in waiting]
            items = await db.open_inbox_items(run, named)
        await db.update_run(run["id"], **values)
        await db.finish_job(job["id"], self.name, "done")
        if given:
            await db.enqueue(run["id"], "resume", {"resume": given})
            self.hub.bus.job_added()
        if asking and items:
            await self._notify(run, spec, items, waiting)
        if status in FINISHED and run.get("flow_id"):
            await self._after_flow(run, status, final.get("output") or {})

    # ── crash recovery ───────────────────────────────────────────────────────

    async def _graph(
        self, spec: FlowSpec, children: dict[str, FlowSpec], flow_id: str | None
    ) -> Any:
        compiled = compile_cached(spec, children.get, flow_id)
        _, graph = await asyncio.to_thread(
            load_graph,
            compiled.source,
            compiled.module_name,
            self.hub.resources,
            compiled.code_functions,
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
        config = await self.db.get_setting(self.hub.space.setting("notifications"), {})
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
            try:
                self.hub.spaces.get(trig.get("workspace_id"))
            except KeyError:
                log.warning("Trigger %s is in a workspace this worker can't open", trig["id"])
                continue
            with self.hub.spaces.enter(trig.get("workspace_id")):
                started += await self._fire_due(trig, now)
        return started

    async def _fire_due(self, trig: dict[str, Any], now: float) -> list[str]:
        """Fire one due schedule, or poll one due mailbox (inside its workspace)."""
        started: list[str] = []
        cfg = trig.get("config") or {}
        if trig["kind"] == "email":
            following = now + max(15, float(cfg.get("poll_seconds") or 60))
            if await self.db.claim_schedule(trig["id"], trig["next_fire_at"], following):
                started += await self._poll_mailbox(trig)
            return started
        try:
            following = next_fire(cfg.get("cron", ""), cfg.get("timezone"), now)
        except CronError:
            following = None
        if not await self.db.claim_schedule(trig["id"], trig["next_fire_at"], following):
            return started  # another worker fired it
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
    spec = hub.space.flows.get(trig["flow_id"])
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


def sandbox_settings(saved: dict[str, Any] | None) -> SandboxSettings:
    """The sandbox as Settings say (EASYCHAIN_SANDBOX* fill in what isn't set there). Saved
    settings that no longer check out fall back to the environment's."""
    try:
        return SandboxSettings.from_dict(saved or {})
    except (TypeError, ValueError):
        return SandboxSettings.from_dict({})
