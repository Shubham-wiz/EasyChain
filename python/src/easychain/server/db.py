"""The run database: runs and their events, the job queue, the Inbox, triggers, flow
versions and settings.

SQLite for local use, Postgres for teams and production, through SQLAlchemy Core, so the
same code runs on both. Flows themselves stay as YAML files in the workspace (git-friendly);
each run records the exact version it ran.

The job queue is a table. A worker leases a job (``FOR UPDATE SKIP LOCKED`` and an
advisory lock per conversation on Postgres; one writer at a time on SQLite), keeps the lease
alive with heartbeats, and finishes it. A lease that isn't renewed runs out, so another
worker picks the job up and carries on from the run's last Save Point.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SCHEMA_VERSION = 1

metadata = sa.MetaData()

flow_versions = sa.Table(
    "flow_versions",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("flow_id", sa.String(100), index=True),
    sa.Column("name", sa.String(200)),
    sa.Column("hash", sa.String(64), index=True),
    # {"spec": {...}, "children": {flow_id: spec}} so Sub-flows run as they were too.
    sa.Column("bundle", sa.JSON),
    sa.Column("note", sa.String(200), default=""),
    sa.Column("created_at", sa.Float),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("flow_id", sa.String(100), index=True, nullable=True),
    sa.Column("flow_name", sa.String(200)),
    sa.Column("version_id", sa.String(40)),
    sa.Column("thread_id", sa.String(100), index=True),
    # queued, running, paused, ok, error, cancelled
    sa.Column("status", sa.String(20), index=True),
    sa.Column("trigger", sa.String(40), default="manual"),
    sa.Column("trigger_id", sa.String(40), nullable=True),
    sa.Column("parent_run_id", sa.String(40), nullable=True),
    sa.Column("inputs", sa.JSON),
    sa.Column("options", sa.JSON),
    sa.Column("output", sa.JSON, nullable=True),
    sa.Column("error", sa.JSON, nullable=True),
    sa.Column("usage", sa.JSON, nullable=True),
    sa.Column("cost", sa.Float, nullable=True),
    sa.Column("pending", sa.JSON, nullable=True),
    sa.Column("checkpoint_id", sa.String(80), nullable=True),
    sa.Column("created_at", sa.Float, index=True),
    sa.Column("started_at", sa.Float, nullable=True),
    sa.Column("finished_at", sa.Float, nullable=True),
    sa.Column("duration_ms", sa.Float, nullable=True),
)

run_events = sa.Table(
    "run_events",
    metadata,
    sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    sa.Column("run_id", sa.String(40), index=True),
    sa.Column("type", sa.String(30)),
    sa.Column("data", sa.JSON),
    sa.Column("ts", sa.Float),
)

jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    sa.Column("run_id", sa.String(40), index=True),
    # start, resume, continue, fork
    sa.Column("action", sa.String(20)),
    sa.Column("payload", sa.JSON),
    sa.Column("thread_id", sa.String(100), index=True),
    sa.Column("flow_id", sa.String(100), nullable=True),
    sa.Column("flow_limit", sa.Integer, nullable=True),
    # queued, leased, done, failed, cancelled
    sa.Column("status", sa.String(20), index=True),
    sa.Column("lease_owner", sa.String(80), nullable=True),
    sa.Column("lease_expires", sa.Float, nullable=True),
    sa.Column("attempts", sa.Integer, default=0),
    sa.Column("cancel_requested", sa.Boolean, default=False),
    sa.Column("available_at", sa.Float),
    sa.Column("created_at", sa.Float),
    sa.Column("finished_at", sa.Float, nullable=True),
)

inbox = sa.Table(
    "inbox",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("run_id", sa.String(40), index=True),
    sa.Column("thread_id", sa.String(100)),
    sa.Column("flow_id", sa.String(100), nullable=True),
    sa.Column("flow_name", sa.String(200)),
    sa.Column("step", sa.String(100)),
    sa.Column("path", sa.JSON),
    sa.Column("interrupt_id", sa.String(80)),
    sa.Column("request", sa.JSON),
    # open, answered, cancelled
    sa.Column("status", sa.String(20), index=True),
    sa.Column("answer", sa.JSON, nullable=True),
    sa.Column("answered_by", sa.String(200), nullable=True),
    sa.Column("created_at", sa.Float),
    sa.Column("answered_at", sa.Float, nullable=True),
)

triggers = sa.Table(
    "triggers",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("flow_id", sa.String(100), index=True),
    # webhook, schedule, upload, after_flow
    sa.Column("kind", sa.String(20)),
    sa.Column("name", sa.String(200), default=""),
    sa.Column("config", sa.JSON),
    sa.Column("enabled", sa.Boolean, default=True),
    sa.Column("token", sa.String(80)),
    sa.Column("next_fire_at", sa.Float, nullable=True),
    sa.Column("last_fired_at", sa.Float, nullable=True),
    sa.Column("last_run_id", sa.String(40), nullable=True),
    sa.Column("created_at", sa.Float),
)

settings = sa.Table(
    "settings",
    metadata,
    sa.Column("key", sa.String(100), primary_key=True),
    sa.Column("value", sa.JSON),
)

meta = sa.Table(
    "easychain_meta",
    metadata,
    sa.Column("key", sa.String(40), primary_key=True),
    sa.Column("value", sa.String(200)),
)

ACTIVE = ("queued", "running")
FINISHED = ("ok", "error", "cancelled")


def new_id() -> str:
    return uuid.uuid4().hex


def async_url(url: str) -> str:
    """The SQLAlchemy async driver URL for a sqlite:/// or postgresql:// URL."""
    if url.startswith("sqlite+aiosqlite://"):
        return url
    if url.startswith("sqlite://"):
        return "sqlite+aiosqlite://" + url[len("sqlite://") :]
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError(f"Unsupported database URL: {url}")


def checkpoint_url(url: str) -> str:
    """Where LangGraph keeps Save Points: a sibling SQLite file, or the same Postgres database."""
    if url.startswith("sqlite"):
        path = url.split(":///", 1)[1] if ":///" in url else url.split("://", 1)[1]
        file = Path(path)
        return "sqlite:///" + str(
            file.with_name(file.stem + ".savepoints" + (file.suffix or ".db"))
        )
    return url


def _row(row: Any) -> dict[str, Any] | None:
    return dict(row._mapping) if row is not None else None


class Database:
    def __init__(self, url: str, engine: AsyncEngine):
        self.url = url
        self.engine = engine
        self.dialect = engine.dialect.name
        # Reads don't need SQLite's write lock.
        self.reader = engine.execution_options(easychain_read=True)
        # SQLite has one writer at a time; this keeps this process's writers in line.
        self._write_lock = asyncio.Lock() if self.dialect == "sqlite" else None

    # ── setup ────────────────────────────────────────────────────────────────

    @classmethod
    async def connect(cls, url: str) -> Database:
        if url.startswith("sqlite"):
            path = url.split(":///", 1)[1] if ":///" in url else ""
            if path and path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(async_url(url), pool_pre_ping=True)
        if engine.dialect.name == "sqlite":
            _sqlite_setup(engine)
        db = cls(url, engine)
        await db.create()
        return db

    async def create(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(metadata.create_all)
            found = (
                await conn.execute(sa.select(meta.c.value).where(meta.c.key == "schema"))
            ).scalar()
            if found is None:
                await conn.execute(meta.insert().values(key="schema", value=str(SCHEMA_VERSION)))
            elif int(found) > SCHEMA_VERSION:
                raise RuntimeError(
                    f"The database was made by a newer Easy Chain (schema {found}); upgrade Easy Chain."
                )

    async def close(self) -> None:
        await self.engine.dispose()

    def _tx(self) -> Any:
        return _Tx(self)

    # ── flow versions ────────────────────────────────────────────────────────

    async def save_version(
        self, flow_id: str | None, bundle: dict[str, Any], note: str = ""
    ) -> str:
        digest = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, default=str).encode()
        ).hexdigest()
        async with self._tx() as conn:
            query = sa.select(flow_versions.c.id).where(flow_versions.c.hash == digest)
            query = query.where(
                flow_versions.c.flow_id == flow_id if flow_id else flow_versions.c.flow_id.is_(None)
            )
            existing = (await conn.execute(query.limit(1))).scalar()
            if existing:
                return str(existing)
            version_id = new_id()
            await conn.execute(
                flow_versions.insert().values(
                    id=version_id,
                    flow_id=flow_id,
                    name=bundle.get("spec", {}).get("name", ""),
                    hash=digest,
                    bundle=bundle,
                    note=note,
                    created_at=time.time(),
                )
            )
            return version_id

    async def get_version(self, version_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            row = (
                await conn.execute(sa.select(flow_versions).where(flow_versions.c.id == version_id))
            ).first()
            return _row(row)

    async def list_versions(self, flow_id: str, limit: int = 50) -> list[dict[str, Any]]:
        cols = [c for c in flow_versions.c if c.name != "bundle"]
        async with self.reader.connect() as conn:
            rows = await conn.execute(
                sa.select(*cols)
                .where(flow_versions.c.flow_id == flow_id)
                .order_by(flow_versions.c.created_at.desc())
                .limit(limit)
            )
            return [dict(r._mapping) for r in rows]

    # ── runs and events ──────────────────────────────────────────────────────

    async def create_run(
        self,
        *,
        flow_id: str | None,
        flow_name: str,
        version_id: str,
        thread_id: str,
        inputs: dict[str, Any],
        options: dict[str, Any],
        action: str = "start",
        payload: dict[str, Any] | None = None,
        trigger: str = "manual",
        trigger_id: str | None = None,
        parent_run_id: str | None = None,
        flow_limit: int | None = None,
        run_id: str | None = None,
    ) -> str:
        run_id = run_id or new_id()
        now = time.time()
        async with self._tx() as conn:
            await conn.execute(
                runs.insert().values(
                    id=run_id,
                    flow_id=flow_id,
                    flow_name=flow_name,
                    version_id=version_id,
                    thread_id=thread_id,
                    status="queued",
                    trigger=trigger,
                    trigger_id=trigger_id,
                    parent_run_id=parent_run_id,
                    inputs=inputs,
                    options=options,
                    created_at=now,
                )
            )
            await conn.execute(
                run_events.insert().values(
                    run_id=run_id,
                    type="run_queued",
                    data={
                        "type": "run_queued",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "ts": now * 1000,
                    },
                    ts=now,
                )
            )
            await self._enqueue(
                conn, run_id, action, payload or {}, thread_id, flow_id, flow_limit, now
            )
        return run_id

    async def enqueue(
        self,
        run_id: str,
        action: str,
        payload: dict[str, Any] | None = None,
        available_at: float | None = None,
    ) -> int:
        run = await self.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        now = time.time()
        async with self._tx() as conn:
            await conn.execute(
                runs.update().where(runs.c.id == run_id).values(status="queued", pending=None)
            )
            await conn.execute(
                run_events.insert().values(
                    run_id=run_id,
                    type="run_queued",
                    data={
                        "type": "run_queued",
                        "run_id": run_id,
                        "action": action,
                        "ts": now * 1000,
                    },
                    ts=now,
                )
            )
            options = run.get("options") or {}
            return await self._enqueue(
                conn,
                run_id,
                action,
                payload or {},
                run["thread_id"],
                run["flow_id"],
                options.get("flow_limit"),
                available_at or now,
            )

    async def _enqueue(
        self,
        conn: Any,
        run_id: str,
        action: str,
        payload: dict[str, Any],
        thread_id: str,
        flow_id: str | None,
        flow_limit: int | None,
        available_at: float,
    ) -> int:
        result = await conn.execute(
            jobs.insert()
            .values(
                run_id=run_id,
                action=action,
                payload=payload,
                thread_id=thread_id,
                flow_id=flow_id,
                flow_limit=flow_limit,
                status="queued",
                attempts=0,
                cancel_requested=False,
                available_at=available_at,
                created_at=time.time(),
            )
            .returning(jobs.c.id)
        )
        return int(result.scalar_one())

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            return _row((await conn.execute(sa.select(runs).where(runs.c.id == run_id))).first())

    async def list_runs(
        self,
        *,
        flow_id: str | None = None,
        thread_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = sa.select(runs).order_by(runs.c.created_at.desc()).limit(limit)
        if flow_id:
            query = query.where(runs.c.flow_id == flow_id)
        if thread_id:
            query = query.where(runs.c.thread_id == thread_id)
        if status:
            query = query.where(runs.c.status.in_(status.split(",")))
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def list_threads(
        self, flow_id: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        last = sa.func.max(runs.c.created_at).label("last_at")
        query = (
            sa.select(runs.c.thread_id, runs.c.flow_id, sa.func.count().label("runs"), last)
            .group_by(runs.c.thread_id, runs.c.flow_id)
            .order_by(last.desc())
            .limit(limit)
        )
        if flow_id:
            query = query.where(runs.c.flow_id == flow_id)
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def update_run(self, run_id: str, **values: Any) -> None:
        async with self._tx() as conn:
            await conn.execute(runs.update().where(runs.c.id == run_id).values(**values))

    async def add_events(self, run_id: str, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        now = time.time()
        rows = [{"run_id": run_id, "type": e.get("type", ""), "data": e, "ts": now} for e in events]
        async with self._tx() as conn:
            await conn.execute(run_events.insert(), rows)

    async def events_after(
        self, run_id: str, after: int = 0, limit: int = 1000, include_tokens: bool = True
    ) -> list[dict[str, Any]]:
        query = (
            sa.select(run_events.c.id, run_events.c.data)
            .where(run_events.c.run_id == run_id, run_events.c.id > after)
            .order_by(run_events.c.id)
            .limit(limit)
        )
        if not include_tokens:
            query = query.where(run_events.c.type != "token")
        async with self.reader.connect() as conn:
            return [{**r.data, "event_id": r.id} for r in await conn.execute(query)]

    async def drop_old_tokens(self, finished_before: float) -> None:
        """Token events are only for watching live; step_finished keeps the full text.

        They are dropped a while after a run ends, so late watchers still see them.
        """
        done = sa.select(runs.c.id).where(
            runs.c.finished_at.is_not(None), runs.c.finished_at < finished_before
        )
        async with self._tx() as conn:
            await conn.execute(
                run_events.delete().where(
                    run_events.c.type == "token", run_events.c.run_id.in_(done)
                )
            )

    # ── the job queue ────────────────────────────────────────────────────────

    async def lease(self, owner: str, lease_seconds: float = 30) -> dict[str, Any] | None:
        """Take the next job that may run now, or None.

        A job may run when its conversation (thread) has no other job running or waiting
        ahead of it, and its flow is under its limit of runs at once. Jobs whose worker
        stopped renewing the lease are taken over.
        """
        now = time.time()
        params = {"owner": owner, "now": now, "expires": now + lease_seconds}
        pick = """
            SELECT j.id FROM jobs j
            WHERE ((j.status = 'queued' AND j.available_at <= :now)
                   OR (j.status = 'leased' AND j.lease_expires < :now))
              AND NOT EXISTS (
                SELECT 1 FROM jobs b WHERE b.thread_id = j.thread_id AND b.id <> j.id
                  AND b.status = 'leased' AND b.lease_expires >= :now)
              AND NOT EXISTS (
                SELECT 1 FROM jobs e WHERE e.thread_id = j.thread_id AND e.id < j.id
                  AND e.status IN ('queued', 'leased'))
              AND (j.flow_limit IS NULL OR (
                SELECT count(*) FROM jobs c WHERE c.flow_id = j.flow_id
                  AND c.status = 'leased' AND c.lease_expires >= :now) < j.flow_limit)
              {lock}
            ORDER BY j.id
            LIMIT 1
            {skip}
        """
        if self.dialect == "postgresql":
            pick = pick.format(
                lock="AND pg_try_advisory_xact_lock(hashtext(j.thread_id))",
                skip="FOR UPDATE SKIP LOCKED",
            )
        else:
            pick = pick.format(lock="", skip="")
        sql = sa.text(
            f"""
            UPDATE jobs SET status = 'leased', lease_owner = :owner, lease_expires = :expires,
                attempts = attempts + 1
            WHERE id = ({pick})
            RETURNING id, run_id, action, payload, thread_id, flow_id, attempts, cancel_requested
            """
        )
        async with self._tx() as conn:
            row = (await conn.execute(sql, params)).first()
        if row is None:
            return None
        job = dict(row._mapping)
        if isinstance(job.get("payload"), str):
            job["payload"] = json.loads(job["payload"])
        return job

    async def heartbeat(self, job_id: int, owner: str, lease_seconds: float = 30) -> bool | None:
        """Renew a lease. Returns whether a cancel was asked for, or None if the lease was lost."""
        async with self._tx() as conn:
            row = (
                await conn.execute(
                    jobs.update()
                    .where(
                        jobs.c.id == job_id, jobs.c.lease_owner == owner, jobs.c.status == "leased"
                    )
                    .values(lease_expires=time.time() + lease_seconds)
                    .returning(jobs.c.cancel_requested)
                )
            ).first()
        return None if row is None else bool(row[0])

    async def finish_job(self, job_id: int, owner: str, status: str = "done") -> None:
        async with self._tx() as conn:
            await conn.execute(
                jobs.update()
                .where(jobs.c.id == job_id, jobs.c.lease_owner == owner)
                .values(status=status, finished_at=time.time(), lease_expires=None)
            )

    async def active_jobs(
        self, *, thread_id: str | None = None, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = sa.select(jobs).where(jobs.c.status.in_(("queued", "leased")))
        if thread_id:
            query = query.where(jobs.c.thread_id == thread_id)
        if run_id:
            query = query.where(jobs.c.run_id == run_id)
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query.order_by(jobs.c.id))]

    async def request_cancel(self, run_id: str) -> str:
        """Stop a run: queued jobs are dropped, a running one is told to stop.

        Returns the run's new status: cancelled now, or cancelling (a worker will stop it).
        """
        now = time.time()
        async with self._tx() as conn:
            await conn.execute(
                jobs.update()
                .where(jobs.c.run_id == run_id, jobs.c.status == "queued")
                .values(status="cancelled", finished_at=now)
            )
            leased = (
                await conn.execute(
                    jobs.update()
                    .where(jobs.c.run_id == run_id, jobs.c.status == "leased")
                    .values(cancel_requested=True)
                    .returning(jobs.c.id)
                )
            ).first()
            await conn.execute(
                inbox.update()
                .where(inbox.c.run_id == run_id, inbox.c.status == "open")
                .values(status="cancelled", answered_at=now)
            )
            if leased is not None:
                return "cancelling"
            await conn.execute(
                runs.update()
                .where(runs.c.id == run_id, runs.c.status.notin_(FINISHED))
                .values(status="cancelled", finished_at=now, pending=None)
            )
            await conn.execute(
                run_events.insert().values(
                    run_id=run_id,
                    type="run_finished",
                    data={
                        "type": "run_finished",
                        "run_id": run_id,
                        "status": "cancelled",
                        "ts": now * 1000,
                    },
                    ts=now,
                )
            )
            return "cancelled"

    # ── the Inbox ────────────────────────────────────────────────────────────

    async def open_inbox_items(
        self, run: dict[str, Any], interrupts: list[dict[str, Any]]
    ) -> list[str]:
        ids = []
        now = time.time()
        async with self._tx() as conn:
            for intr in interrupts:
                existing = (
                    await conn.execute(
                        sa.select(inbox.c.id).where(
                            inbox.c.run_id == run["id"],
                            inbox.c.interrupt_id == intr["id"],
                            inbox.c.status == "open",
                        )
                    )
                ).scalar()
                if existing:
                    ids.append(str(existing))
                    continue
                item_id = new_id()
                await conn.execute(
                    inbox.insert().values(
                        id=item_id,
                        run_id=run["id"],
                        thread_id=run["thread_id"],
                        flow_id=run["flow_id"],
                        flow_name=run["flow_name"],
                        step=intr.get("step") or "",
                        path=intr.get("path") or [],
                        interrupt_id=intr["id"],
                        request=intr.get("request") or {},
                        status="open",
                        created_at=now,
                    )
                )
                ids.append(item_id)
        return ids

    async def list_inbox(
        self, status: str | None = "open", limit: int = 200
    ) -> list[dict[str, Any]]:
        query = sa.select(inbox).order_by(inbox.c.created_at.desc()).limit(limit)
        if status:
            query = query.where(inbox.c.status == status)
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def get_inbox(self, item_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            return _row((await conn.execute(sa.select(inbox).where(inbox.c.id == item_id))).first())

    async def answer_inbox(
        self, item_id: str, answer: Any, by: str | None = None
    ) -> dict[str, Any] | None:
        """Record an answer once; returns the item, or None if it was already closed."""
        async with self._tx() as conn:
            row = (
                await conn.execute(
                    inbox.update()
                    .where(inbox.c.id == item_id, inbox.c.status == "open")
                    .values(
                        status="answered", answer=answer, answered_by=by, answered_at=time.time()
                    )
                    .returning(*inbox.c)
                )
            ).first()
        return _row(row)

    # ── triggers ─────────────────────────────────────────────────────────────

    async def create_trigger(
        self,
        flow_id: str,
        kind: str,
        config: dict[str, Any],
        name: str = "",
        next_fire_at: float | None = None,
    ) -> dict[str, Any]:
        trigger_id = new_id()
        token = uuid.uuid4().hex + uuid.uuid4().hex[:8]
        async with self._tx() as conn:
            await conn.execute(
                triggers.insert().values(
                    id=trigger_id,
                    flow_id=flow_id,
                    kind=kind,
                    name=name,
                    config=config,
                    enabled=True,
                    token=token,
                    next_fire_at=next_fire_at,
                    created_at=time.time(),
                )
            )
        return await self.get_trigger(trigger_id)  # type: ignore[return-value]

    async def get_trigger(self, trigger_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            return _row(
                (await conn.execute(sa.select(triggers).where(triggers.c.id == trigger_id))).first()
            )

    async def list_triggers(
        self, flow_id: str | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        query = sa.select(triggers).order_by(triggers.c.created_at)
        if flow_id:
            query = query.where(triggers.c.flow_id == flow_id)
        if kind:
            query = query.where(triggers.c.kind == kind)
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def update_trigger(self, trigger_id: str, **values: Any) -> None:
        async with self._tx() as conn:
            await conn.execute(
                triggers.update().where(triggers.c.id == trigger_id).values(**values)
            )

    async def delete_trigger(self, trigger_id: str) -> bool:
        async with self._tx() as conn:
            result = await conn.execute(triggers.delete().where(triggers.c.id == trigger_id))
            return bool(result.rowcount)

    async def due_schedules(self, now: float) -> list[dict[str, Any]]:
        async with self.reader.connect() as conn:
            rows = await conn.execute(
                sa.select(triggers).where(
                    triggers.c.kind == "schedule",
                    triggers.c.enabled.is_(True),
                    triggers.c.next_fire_at.is_not(None),
                    triggers.c.next_fire_at <= now,
                )
            )
            return [dict(r._mapping) for r in rows]

    async def claim_schedule(
        self, trigger_id: str, expected: float, next_fire_at: float | None
    ) -> bool:
        """Move a schedule on; only one worker wins, so a schedule fires once."""
        async with self._tx() as conn:
            result = await conn.execute(
                triggers.update()
                .where(triggers.c.id == trigger_id, triggers.c.next_fire_at == expected)
                .values(next_fire_at=next_fire_at, last_fired_at=time.time())
            )
            return bool(result.rowcount)

    # ── settings ─────────────────────────────────────────────────────────────

    async def get_setting(self, key: str, default: Any = None) -> Any:
        async with self.reader.connect() as conn:
            value = (
                await conn.execute(sa.select(settings.c.value).where(settings.c.key == key))
            ).scalar()
        return default if value is None else value

    async def set_setting(self, key: str, value: Any) -> None:
        async with self._tx() as conn:
            updated = await conn.execute(
                settings.update().where(settings.c.key == key).values(value=value)
            )
            if not updated.rowcount:
                await conn.execute(settings.insert().values(key=key, value=value))


class _Tx:
    """A write transaction (serialised within the process on SQLite)."""

    def __init__(self, db: Database):
        self.db = db
        self._cm: Any = None

    async def __aenter__(self) -> Any:
        if self.db._write_lock is not None:
            await self.db._write_lock.acquire()
        try:
            self._cm = self.db.engine.begin()
            return await self._cm.__aenter__()
        except BaseException:
            if self.db._write_lock is not None:
                self.db._write_lock.release()
            raise

    async def __aexit__(self, *exc: Any) -> None:
        try:
            await self._cm.__aexit__(*exc)
        finally:
            if self.db._write_lock is not None:
                self.db._write_lock.release()


def _sqlite_setup(engine: AsyncEngine) -> None:
    """WAL, a busy timeout, and BEGIN IMMEDIATE so writers from other processes queue up."""

    @event.listens_for(engine.sync_engine, "connect")
    def _connect(dbapi_connection: Any, record: Any) -> None:
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def _begin(conn: Any) -> None:
        read = conn.get_execution_options().get("easychain_read")
        conn.exec_driver_sql("BEGIN" if read else "BEGIN IMMEDIATE")
