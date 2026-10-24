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
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from .. import current_workspace as context

log = logging.getLogger("easychain.db")

# Advisory lock ids (Postgres) that keep processes starting together from racing.
SCHEMA_LOCK = 0x45415359  # "EASY"
# Postgres NOTIFY channels: a run has new events (the payload is its id); a job was queued or
# finished. server/listen.py wakes the other processes with them.
EVENTS_CHANNEL = "easychain_events"
JOBS_CHANNEL = "easychain_jobs"

# The tables, as the latest migration (server/migrations/versions) leaves them. A change here
# needs a migration; tests/test_migrations.py checks that the two agree.

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
    # The workspace it belongs to (Phase 5.6d, migration 0006).
    sa.Column("workspace_id", sa.String(40), index=True, server_default="default"),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    # The workspace it belongs to (Phase 5.6d, migration 0006).
    sa.Column("workspace_id", sa.String(40), index=True, server_default="default"),
    sa.Column("flow_id", sa.String(100), index=True, nullable=True),
    sa.Column("flow_name", sa.String(200)),
    # dev, staging or production, for a published flow's runs (Phase 5.4, migration 0008)
    sa.Column("environment", sa.String(20), index=True, nullable=True),
    sa.Column("version_id", sa.String(40)),
    sa.Column("thread_id", sa.String(100), index=True),
    # Who started it: their user id (None with sign-in off, or a trigger) and how they show up
    # (an access key's runs show the key). Phase 5.5, migration 0009.
    sa.Column("started_by", sa.String(40), nullable=True),
    sa.Column("started_by_name", sa.String(200), nullable=True),
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
    # The workspace it belongs to (Phase 5.6d, migration 0006).
    sa.Column("workspace_id", sa.String(40), index=True, server_default="default"),
    sa.Column("run_id", sa.String(40), index=True),
    sa.Column("thread_id", sa.String(100)),
    sa.Column("flow_id", sa.String(100), nullable=True),
    sa.Column("flow_name", sa.String(200)),
    sa.Column("step", sa.String(100)),
    sa.Column("step_name", sa.String(200), default=""),
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
    # The workspace it belongs to (Phase 5.6d, migration 0006).
    sa.Column("workspace_id", sa.String(40), index=True, server_default="default"),
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

# Files added to a Knowledge Base, kept until a worker has read them (migration 0002). They
# live in the database so a worker needs no disk shared with the API server.
uploads = sa.Table(
    "uploads",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("name", sa.String(300)),
    sa.Column("content_type", sa.String(200), nullable=True),
    sa.Column("size", sa.Integer),
    sa.Column("data", sa.LargeBinary),
    sa.Column("created_at", sa.Float),
)

# ── people (Phase 5.6, migration 0003) ───────────────────────────────────────

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("email", sa.String(320)),  # stored in lower case
    sa.Column("name", sa.String(200)),
    # scrypt (server/auth.py); empty for people who only sign in with single sign-on
    sa.Column("password_hash", sa.String(300), nullable=True),
    sa.Column("disabled", sa.Boolean),
    sa.Column("created_at", sa.Float),
    sa.Column("last_login_at", sa.Float, nullable=True),
    sa.UniqueConstraint("email", name="uq_users_email"),
)

workspaces = sa.Table(
    "workspaces",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("name", sa.String(200)),
    sa.Column("created_at", sa.Float),
)

# What each person may do in a workspace: owner, editor, runner or viewer.
memberships = sa.Table(
    "memberships",
    metadata,
    sa.Column("workspace_id", sa.String(40), primary_key=True),
    sa.Column("user_id", sa.String(40), primary_key=True),
    sa.Column("role", sa.String(20)),
    sa.Column("created_at", sa.Float),
)

# Signed-in browsers. The cookie holds a random token; only its SHA-256 is kept.
sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.String(40), index=True),
    sa.Column("created_at", sa.Float),
    sa.Column("expires_at", sa.Float),
    sa.Column("last_seen_at", sa.Float),
)

# Keys for scripts and other apps (Authorization: Bearer eck_<id>_<secret>); only the secret's
# SHA-256 is kept. A key acts as its person, with at most the role it was made with.
api_keys = sa.Table(
    "api_keys",
    metadata,
    sa.Column("id", sa.String(16), primary_key=True),
    sa.Column("user_id", sa.String(40), index=True),
    sa.Column("workspace_id", sa.String(40)),
    sa.Column("name", sa.String(200)),
    sa.Column("role", sa.String(20)),
    sa.Column("secret_hash", sa.String(64)),
    sa.Column("created_at", sa.Float),
    sa.Column("last_used_at", sa.Float, nullable=True),
    sa.Column("revoked_at", sa.Float, nullable=True),
)

# Single sign-on (Phase 5.6e, migration 0005): which account a provider's person is. The
# subject is the provider's own id for them, which stays the same if their email changes.
user_identities = sa.Table(
    "user_identities",
    metadata,
    sa.Column("provider", sa.String(40), primary_key=True),
    sa.Column("subject", sa.String(255), primary_key=True),
    sa.Column("user_id", sa.String(40), index=True),
    sa.Column("email", sa.String(320)),
    sa.Column("created_at", sa.Float),
    sa.Column("last_used_at", sa.Float, nullable=True),
)

# ── Test Runs (Phase 5.2, migration 0007) ────────────────────────────────────

# A Test Set run against one flow version on a worker (a "test" job), with its results; one
# per flow can be its baseline (testruns.py compares the others with it).
test_runs = sa.Table(
    "test_runs",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("workspace_id", sa.String(40), index=True),
    sa.Column("flow_id", sa.String(100), index=True),
    sa.Column("version_id", sa.String(40)),
    # queued, running, done, error
    sa.Column("status", sa.String(20)),
    sa.Column("stand_in", sa.Boolean),
    sa.Column("repeat", sa.Integer),
    sa.Column("judge_model", sa.String(200), nullable=True),
    # the cases as they were when it started
    sa.Column("cases", sa.JSON),
    # case runs finished so far
    sa.Column("done", sa.Integer),
    # one summary per case (testruns.case_summary), and the whole run's
    sa.Column("results", sa.JSON, nullable=True),
    sa.Column("summary", sa.JSON, nullable=True),
    sa.Column("baseline", sa.Boolean),
    sa.Column("created_by", sa.String(200), nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("created_at", sa.Float),
    sa.Column("started_at", sa.Float, nullable=True),
    sa.Column("finished_at", sa.Float, nullable=True),
    # run with an environment's secrets and swaps (Phase 5.4, migration 0008)
    sa.Column("environment", sa.String(20), nullable=True),
)

# ── publishing (Phase 5.4, migration 0008) ──────────────────────────────────

# Which flow version is published to which environment; the one in force has no retired_at.
publications = sa.Table(
    "publications",
    metadata,
    sa.Column("id", sa.String(40), primary_key=True),
    sa.Column("workspace_id", sa.String(40), index=True),
    sa.Column("flow_id", sa.String(100), index=True),
    sa.Column("environment", sa.String(20)),
    sa.Column("version_id", sa.String(40)),
    sa.Column("test_run_id", sa.String(40), nullable=True),
    sa.Column("published_by", sa.String(200), nullable=True),
    sa.Column("published_at", sa.Float),
    sa.Column("retired_at", sa.Float, nullable=True),
)

# Keys that let other apps run a published flow (epk_<id>_<secret>; only the secret's SHA-256
# is kept), and chat links for the hosted chat page and the widget (no secret).
access_keys = sa.Table(
    "access_keys",
    metadata,
    sa.Column("id", sa.String(16), primary_key=True),
    sa.Column("workspace_id", sa.String(40)),
    sa.Column("flow_id", sa.String(100), index=True),
    sa.Column("environment", sa.String(20)),
    sa.Column("name", sa.String(200)),
    # secret or chat
    sa.Column("kind", sa.String(10)),
    sa.Column("secret_hash", sa.String(64), nullable=True),
    sa.Column("per_minute", sa.Integer),
    # sites that may embed the chat page (chat links)
    sa.Column("origins", sa.JSON),
    sa.Column("created_by", sa.String(200), nullable=True),
    sa.Column("created_at", sa.Float),
    sa.Column("last_used_at", sa.Float, nullable=True),
    sa.Column("revoked_at", sa.Float, nullable=True),
)

# Requests per key (or key and address) and minute, shared by every API process.
key_usage = sa.Table(
    "key_usage",
    metadata,
    sa.Column("key_id", sa.String(80), primary_key=True),
    sa.Column("minute", sa.Integer, primary_key=True),
    sa.Column("count", sa.Integer),
)

# ── the model gateway (Phase 5.5, migration 0009) ───────────────────────────

# Every model call of a run, a Test Run, a Test Set run in the app or the copilot, with its
# tokens and cost (None: no price is known). Budgets, alerts and the spending pages add these
# up (server/gateway.py).
model_calls = sa.Table(
    "model_calls",
    metadata,
    sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    sa.Column("at", sa.Float),
    sa.Column("workspace_id", sa.String(40)),
    # run, test_run, test_set or copilot
    sa.Column("kind", sa.String(20)),
    sa.Column("run_id", sa.String(40), nullable=True, index=True),
    sa.Column("flow_id", sa.String(100), nullable=True),
    sa.Column("flow_name", sa.String(200), nullable=True),
    sa.Column("environment", sa.String(20), nullable=True),
    sa.Column("user_id", sa.String(40), nullable=True),
    sa.Column("user_name", sa.String(200), nullable=True),
    sa.Column("step", sa.String(100), nullable=True),
    sa.Column("provider", sa.String(40), nullable=True),
    sa.Column("model", sa.String(200), nullable=True),
    sa.Column("input_tokens", sa.Integer),
    sa.Column("output_tokens", sa.Integer),
    sa.Column("cost", sa.Float, nullable=True),
    sa.Column("cached", sa.Boolean),
    sa.Column("error", sa.String(300), nullable=True),
    sa.Column("duration_ms", sa.Float, nullable=True),
    sa.Index("ix_model_calls_spend", "workspace_id", "at"),
)

# One row per alert sent: a budget's level (80, 100) in a period ("2026-10" or "2026-10-24").
budget_alerts = sa.Table(
    "budget_alerts",
    metadata,
    sa.Column("workspace_id", sa.String(40), primary_key=True),
    sa.Column("budget", sa.String(20), primary_key=True),
    sa.Column("period", sa.String(10), primary_key=True),
    sa.Column("level", sa.Integer, primary_key=True),
    sa.Column("spent", sa.Float),
    sa.Column("amount", sa.Float),
    sa.Column("at", sa.Float),
)

# Answers kept by the gateway's cache (runtime/model_policy.py AnswerCache), by a hash of the
# workspace, the model with its settings, and the messages.
answer_cache = sa.Table(
    "answer_cache",
    metadata,
    sa.Column("key", sa.String(64), primary_key=True),
    sa.Column("workspace_id", sa.String(40)),
    sa.Column("value", sa.Text),
    sa.Column("created_at", sa.Float),
    sa.Column("expires_at", sa.Float, index=True),
)

# ── the audit log (Phase 5.6c, migration 0004) ──────────────────────────────

# What people changed, newest last. Append-only: database triggers refuse UPDATE and DELETE
# (server/audit.py records it; docs/audit.md).
audit_log = sa.Table(
    "audit_log",
    metadata,
    sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    sa.Column("at", sa.Float, index=True),
    sa.Column("workspace_id", sa.String(40)),
    sa.Column("actor_id", sa.String(40), nullable=True, index=True),
    # how the person shows (name or email), kept as it was then
    sa.Column("actor", sa.String(320)),
    # session, key, local (sign-in off), trigger (a webhook), cli
    sa.Column("via", sa.String(20)),
    sa.Column("action", sa.String(60), index=True),
    sa.Column("target", sa.String(320), nullable=True, index=True),
    sa.Column("detail", sa.JSON),
    sa.Column("address", sa.String(100), nullable=True),
)

# Easy Chain 0.4.0 and earlier kept a schema number here; migrations replaced it.
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
    """Where LangGraph keeps Save Points: a sibling SQLite file, or the same Postgres database.

    EASYCHAIN_SAVEPOINTS overrides it, e.g. ``python:my_package.backends:resources``.
    """
    import os

    if os.environ.get("EASYCHAIN_SAVEPOINTS"):
        return os.environ["EASYCHAIN_SAVEPOINTS"]
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
        # The migrations that ran when it connected.
        self.migrated: list[str] = []

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
        db.migrated = await db.create()
        return db

    async def create(self) -> list[str]:
        """Make or upgrade the tables (server/migrations). Processes that start together take
        turns: the first upgrades, the others find nothing left to do."""
        from . import migrations

        async with self.engine.begin() as conn:
            if self.dialect == "postgresql":
                await conn.execute(
                    sa.text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_LOCK}
                )
            ran = await conn.run_sync(migrations.upgrade)
        if ran:
            log.info("Upgraded the database: %s", "; ".join(ran))
        return ran

    async def close(self) -> None:
        await self.engine.dispose()

    def _tx(self) -> Any:
        return _Tx(self)

    async def _wake(self, conn: Any, channel: str, payload: str = "") -> None:
        """On Postgres, wake the other processes when this transaction commits (NOTIFY is
        delivered on commit, so they find what was written). SQLite has no such thing: there,
        other processes notice on their next poll."""
        if self.dialect == "postgresql":
            await conn.execute(
                sa.text("SELECT pg_notify(:channel, :payload)"),
                {"channel": channel, "payload": payload},
            )

    # ── flow versions ────────────────────────────────────────────────────────

    async def save_version(
        self, flow_id: str | None, bundle: dict[str, Any], note: str = ""
    ) -> str:
        workspace = context.current()
        digest = hashlib.sha256(
            json.dumps(bundle, sort_keys=True, default=str).encode()
        ).hexdigest()
        async with self._tx() as conn:
            query = sa.select(flow_versions.c.id).where(
                flow_versions.c.hash == digest, flow_versions.c.workspace_id == workspace
            )
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
                    workspace_id=workspace,
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
                .where(
                    flow_versions.c.flow_id == flow_id,
                    flow_versions.c.workspace_id == context.current(),
                )
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
        environment: str | None = None,
        by: tuple[str | None, str] | None = None,
    ) -> str:
        run_id = run_id or new_id()
        now = time.time()
        by = by or context.person.get()
        async with self._tx() as conn:
            await conn.execute(
                runs.insert().values(
                    id=run_id,
                    workspace_id=context.current(),
                    flow_id=flow_id,
                    flow_name=flow_name,
                    environment=environment,
                    version_id=version_id,
                    thread_id=thread_id,
                    started_by=by[0] if by else None,
                    started_by_name=by[1] if by else None,
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
        await self._wake(conn, JOBS_CHANNEL)
        await self._wake(conn, EVENTS_CHANNEL, run_id)
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
        query = (
            sa.select(runs)
            .where(runs.c.workspace_id == context.current())
            .order_by(runs.c.created_at.desc())
            .limit(limit)
        )
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
            .where(runs.c.workspace_id == context.current())
            .group_by(runs.c.thread_id, runs.c.flow_id)
            .order_by(last.desc())
            .limit(limit)
        )
        if flow_id:
            query = query.where(runs.c.flow_id == flow_id)
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def thread_elsewhere(self, thread_id: str) -> bool:
        """Whether a conversation with this id belongs to another workspace."""
        query = sa.select(runs.c.id).where(
            runs.c.thread_id == thread_id, runs.c.workspace_id != context.current()
        )
        async with self.reader.connect() as conn:
            return (await conn.execute(query.limit(1))).first() is not None

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
            await self._wake(conn, EVENTS_CHANNEL, run_id)

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

    async def end_event_id(self, run_id: str, status: str) -> int | None:
        """The id of the run_finished event that ends the run's latest part (since it was last
        queued) with this status, or None if it hasn't been written."""
        async with self.reader.connect() as conn:
            queued = (
                await conn.execute(
                    sa.select(sa.func.max(run_events.c.id)).where(
                        run_events.c.run_id == run_id, run_events.c.type == "run_queued"
                    )
                )
            ).scalar() or 0
            last = (
                await conn.execute(
                    sa.select(run_events.c.id, run_events.c.data)
                    .where(run_events.c.run_id == run_id, run_events.c.type == "run_finished")
                    .order_by(run_events.c.id.desc())
                    .limit(1)
                )
            ).first()
        if last is None or last.id < queued or (last.data or {}).get("status") != status:
            return None
        return int(last.id)

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
            # The conversation's next job may run now.
            await self._wake(conn, JOBS_CHANNEL)

    # ── Knowledge Base documents ─────────────────────────────────────────────

    async def enqueue_ingest(
        self,
        kb_id: str,
        doc_id: str,
        source: dict[str, Any],
        data: bytes | None = None,
        name: str = "",
        content_type: str | None = None,
    ) -> int:
        """Queue a document for a worker to read, split and embed. ``source`` says what to read
        (``{"kind": "url", "url": …}``, ``{"kind": "text", …}``, or a file, whose bytes are
        kept in ``uploads`` until it has been read). One Knowledge Base's documents are read
        one at a time (their jobs share a thread)."""
        now = time.time()
        payload = {"kb_id": kb_id, "workspace": context.current(), **source}
        async with self._tx() as conn:
            if data is not None:
                upload_id = new_id()
                await conn.execute(
                    uploads.insert().values(
                        id=upload_id,
                        name=name[:300],
                        content_type=content_type,
                        size=len(data),
                        data=data,
                        created_at=now,
                    )
                )
                payload["upload_id"] = upload_id
            return await self._enqueue(
                conn, doc_id, "ingest", payload, f"kb:{kb_id}", None, None, now
            )

    # ── Test Runs (Phase 5.2, migration 0007) ────────────────────────────────

    async def create_test_run(
        self,
        flow_id: str,
        version_id: str,
        cases: list[dict[str, Any]],
        *,
        stand_in: bool,
        repeat: int,
        judge_model: str | None = None,
        created_by: str | None = None,
        environment: str | None = None,
    ) -> str:
        """Keep a Test Run and queue it for a worker (a ``test`` job)."""
        run_id = new_id()
        now = time.time()
        workspace = context.current()
        async with self._tx() as conn:
            await conn.execute(
                test_runs.insert().values(
                    id=run_id,
                    workspace_id=workspace,
                    flow_id=flow_id,
                    version_id=version_id,
                    status="queued",
                    stand_in=stand_in,
                    repeat=repeat,
                    judge_model=judge_model,
                    cases=cases,
                    done=0,
                    baseline=False,
                    created_by=created_by,
                    created_at=now,
                    environment=environment,
                )
            )
            await self._enqueue(
                conn,
                run_id,
                "test",
                {"workspace": workspace},
                f"test:{run_id}",
                None,
                None,
                now,
            )
        return run_id

    async def get_test_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            row = (await conn.execute(sa.select(test_runs).where(test_runs.c.id == run_id))).first()
            return _row(row)

    async def list_test_runs(self, flow_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """The workspace's Test Runs of a flow, newest first (without their results)."""
        cols = [c for c in test_runs.c if c.name != "results"]
        query = (
            sa.select(*cols)
            .where(test_runs.c.flow_id == flow_id, test_runs.c.workspace_id == context.current())
            .order_by(test_runs.c.created_at.desc())
            .limit(limit)
        )
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def update_test_run(self, run_id: str, **values: Any) -> None:
        async with self._tx() as conn:
            await conn.execute(test_runs.update().where(test_runs.c.id == run_id).values(**values))

    async def set_baseline(self, run_id: str, flow_id: str) -> None:
        """Make this the flow's baseline (in its workspace); the one before stops being it."""
        async with self._tx() as conn:
            await conn.execute(
                test_runs.update()
                .where(
                    test_runs.c.flow_id == flow_id,
                    test_runs.c.workspace_id == context.current(),
                    test_runs.c.baseline.is_(True),
                )
                .values(baseline=False)
            )
            await conn.execute(
                test_runs.update().where(test_runs.c.id == run_id).values(baseline=True)
            )

    async def baseline_of(self, flow_id: str) -> dict[str, Any] | None:
        query = sa.select(test_runs).where(
            test_runs.c.flow_id == flow_id,
            test_runs.c.workspace_id == context.current(),
            test_runs.c.baseline.is_(True),
        )
        async with self.reader.connect() as conn:
            return _row((await conn.execute(query.limit(1))).first())

    # ── publishing (Phase 5.4, migration 0008) ───────────────────────────────

    async def publish(
        self,
        flow_id: str,
        environment: str,
        version_id: str,
        *,
        test_run_id: str | None = None,
        published_by: str | None = None,
    ) -> dict[str, Any]:
        """Pin a version to an environment; what was published there before is retired."""
        now = time.time()
        workspace = context.current()
        pub_id = new_id()
        async with self._tx() as conn:
            await conn.execute(
                publications.update()
                .where(
                    publications.c.workspace_id == workspace,
                    publications.c.flow_id == flow_id,
                    publications.c.environment == environment,
                    publications.c.retired_at.is_(None),
                )
                .values(retired_at=now)
            )
            await conn.execute(
                publications.insert().values(
                    id=pub_id,
                    workspace_id=workspace,
                    flow_id=flow_id,
                    environment=environment,
                    version_id=version_id,
                    test_run_id=test_run_id,
                    published_by=published_by,
                    published_at=now,
                )
            )
        return await self.publication(flow_id, environment) or {}

    async def publication(
        self, flow_id: str, environment: str, workspace: str | None = None
    ) -> dict[str, Any] | None:
        """What is published in an environment now (None: nothing)."""
        query = sa.select(publications).where(
            publications.c.workspace_id == (workspace or context.current()),
            publications.c.flow_id == flow_id,
            publications.c.environment == environment,
            publications.c.retired_at.is_(None),
        )
        async with self.reader.connect() as conn:
            return _row((await conn.execute(query.limit(1))).first())

    async def publications_of(self, flow_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Every time the flow was published, newest first."""
        query = (
            sa.select(publications)
            .where(
                publications.c.workspace_id == context.current(),
                publications.c.flow_id == flow_id,
            )
            .order_by(publications.c.published_at.desc())
            .limit(limit)
        )
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def unpublish(self, flow_id: str, environment: str) -> bool:
        async with self._tx() as conn:
            done = await conn.execute(
                publications.update()
                .where(
                    publications.c.workspace_id == context.current(),
                    publications.c.flow_id == flow_id,
                    publications.c.environment == environment,
                    publications.c.retired_at.is_(None),
                )
                .values(retired_at=time.time())
            )
        return bool(done.rowcount)

    async def create_access_key(self, **values: Any) -> None:
        async with self._tx() as conn:
            await conn.execute(
                access_keys.insert().values(
                    workspace_id=context.current(), created_at=time.time(), **values
                )
            )

    async def access_key(self, key_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            row = (
                await conn.execute(sa.select(access_keys).where(access_keys.c.id == key_id))
            ).first()
        return _row(row)

    async def access_keys_of(self, flow_id: str) -> list[dict[str, Any]]:
        cols = [c for c in access_keys.c if c.name != "secret_hash"]
        query = (
            sa.select(*cols)
            .where(
                access_keys.c.workspace_id == context.current(),
                access_keys.c.flow_id == flow_id,
                access_keys.c.revoked_at.is_(None),
            )
            .order_by(access_keys.c.created_at)
        )
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def revoke_access_key(self, key_id: str) -> bool:
        async with self._tx() as conn:
            done = await conn.execute(
                access_keys.update()
                .where(
                    access_keys.c.id == key_id,
                    access_keys.c.workspace_id == context.current(),
                    access_keys.c.revoked_at.is_(None),
                )
                .values(revoked_at=time.time())
            )
        return bool(done.rowcount)

    async def touch_access_key(self, key_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute(
                access_keys.update()
                .where(access_keys.c.id == key_id)
                .values(last_used_at=time.time())
            )

    async def count_use(self, key: str, now: float | None = None) -> int:
        """Count one request against ``key`` in this minute; the count so far (any process)."""
        minute = int((now or time.time()) // 60)
        sql = sa.text(
            "INSERT INTO key_usage (key_id, minute, count) VALUES (:key, :minute, 1) "
            "ON CONFLICT (key_id, minute) DO UPDATE SET count = key_usage.count + 1 "
            "RETURNING count"
        )
        async with self._tx() as conn:
            count = (await conn.execute(sql, {"key": key[:80], "minute": minute})).scalar()
            # Old minutes are of no use any more.
            await conn.execute(key_usage.delete().where(key_usage.c.minute < minute - 5))
        return int(count or 0)

    # ── the model gateway (Phase 5.5) ────────────────────────────────────────

    async def add_model_call(self, row: dict[str, Any]) -> None:
        async with self._tx() as conn:
            await conn.execute(model_calls.insert().values(**row))

    @staticmethod
    def _scope(query: Any, scope: str) -> Any:
        """Model calls of a budget's scope: all of the workspace's, drafts' (no environment),
        or one environment's."""
        if scope == "drafts":
            return query.where(model_calls.c.environment.is_(None))
        if scope != "workspace":
            return query.where(model_calls.c.environment == scope)
        return query

    async def spent(self, workspace_id: str, scope: str, since: float) -> float:
        """What the model calls of ``scope`` in the workspace cost since ``since``."""
        query = sa.select(sa.func.coalesce(sa.func.sum(model_calls.c.cost), 0.0)).where(
            model_calls.c.workspace_id == workspace_id, model_calls.c.at >= since
        )
        async with self.reader.connect() as conn:
            return float((await conn.execute(self._scope(query, scope))).scalar() or 0.0)

    async def spending(
        self,
        workspace_id: str,
        *,
        since: float,
        until: float | None = None,
        by: str = "flow",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Model calls added up per flow, person, environment, model or run, the costliest
        first."""
        c = model_calls.c
        keys = {
            "flow": [c.flow_id, c.flow_name, c.kind],
            "person": [c.user_id, c.user_name],
            "environment": [c.environment],
            "model": [c.provider, c.model],
            "run": [c.run_id, c.kind, c.flow_id, c.flow_name, c.environment, c.user_name],
            "total": [],
        }[by]
        cost = sa.func.coalesce(sa.func.sum(c.cost), 0.0)
        query = (
            sa.select(
                *keys,
                sa.func.count().label("calls"),
                sa.func.sum(sa.case((c.cached == sa.true(), 1), else_=0)).label("cached"),
                sa.func.sum(sa.case((c.error.is_not(None), 1), else_=0)).label("errors"),
                sa.func.sum(sa.case((c.cost.is_(None), 1), else_=0)).label("unpriced"),
                sa.func.coalesce(sa.func.sum(c.input_tokens), 0).label("input_tokens"),
                sa.func.coalesce(sa.func.sum(c.output_tokens), 0).label("output_tokens"),
                cost.label("cost"),
                sa.func.min(c.at).label("first"),
                sa.func.max(c.at).label("last"),
            )
            .where(c.workspace_id == workspace_id, c.at >= since)
            .group_by(*keys)
            .order_by(cost.desc(), sa.func.count().desc())
            .limit(limit)
        )
        if until is not None:
            query = query.where(c.at < until)
        async with self.reader.connect() as conn:
            rows = await conn.execute(query)
            return [dict(r._mapping) for r in rows]

    async def mark_alert(
        self, workspace_id: str, budget: str, period: str, level: int, spent: float, amount: float
    ) -> bool:
        """Note that an alert is sent; False if it was already (any process)."""
        sql = sa.text(
            "INSERT INTO budget_alerts (workspace_id, budget, period, level, spent, amount, at) "
            "VALUES (:ws, :budget, :period, :level, :spent, :amount, :at) ON CONFLICT DO NOTHING"
        )
        values = {
            "ws": workspace_id,
            "budget": budget,
            "period": period,
            "level": level,
            "spent": spent,
            "amount": amount,
            "at": time.time(),
        }
        async with self._tx() as conn:
            result = await conn.execute(sql, values)
            return result.rowcount == 1

    async def alerts(self, workspace_id: str, limit: int = 20) -> list[dict[str, Any]]:
        async with self.reader.connect() as conn:
            rows = await conn.execute(
                sa.select(budget_alerts)
                .where(budget_alerts.c.workspace_id == workspace_id)
                .order_by(budget_alerts.c.at.desc())
                .limit(limit)
            )
            return [dict(r._mapping) for r in rows]

    async def cache_get(self, key: str, now: float | None = None) -> str | None:
        query = sa.select(answer_cache.c.value).where(
            answer_cache.c.key == key, answer_cache.c.expires_at > (now or time.time())
        )
        async with self.reader.connect() as conn:
            return (await conn.execute(query)).scalar()

    async def cache_put(self, key: str, workspace_id: str, value: str, expires_at: float) -> None:
        now = time.time()
        sql = sa.text(
            'INSERT INTO answer_cache ("key", workspace_id, value, created_at, expires_at) '
            'VALUES (:key, :ws, :value, :now, :expires) ON CONFLICT ("key") DO UPDATE SET '
            "value = excluded.value, created_at = excluded.created_at, "
            "expires_at = excluded.expires_at"
        )
        values = {"key": key, "ws": workspace_id, "value": value, "now": now}
        async with self._tx() as conn:
            await conn.execute(sql, {**values, "expires": expires_at})
            # Answers past their time are of no use any more.
            await conn.execute(answer_cache.delete().where(answer_cache.c.expires_at < now))

    async def clear_cache(self, workspace_id: str) -> int:
        async with self._tx() as conn:
            result = await conn.execute(
                answer_cache.delete().where(answer_cache.c.workspace_id == workspace_id)
            )
            return int(result.rowcount or 0)

    async def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        async with self.reader.connect() as conn:
            row = (await conn.execute(sa.select(uploads).where(uploads.c.id == upload_id))).first()
            return _row(row)

    async def delete_upload(self, upload_id: str) -> None:
        async with self._tx() as conn:
            await conn.execute(uploads.delete().where(uploads.c.id == upload_id))

    async def ingesting(self) -> set[str]:
        """The documents that have a reading job waiting or running."""
        async with self.reader.connect() as conn:
            rows = await conn.execute(
                sa.select(jobs.c.run_id).where(
                    jobs.c.action == "ingest", jobs.c.status.in_(("queued", "leased"))
                )
            )
            return {r[0] for r in rows}

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
        A run that has already finished is left as it is, and its status is returned.
        """
        now = time.time()
        async with self._tx() as conn:
            current = (
                await conn.execute(sa.select(runs.c.status).where(runs.c.id == run_id))
            ).scalar()
            if current is None or current in FINISHED:
                return str(current or "cancelled")
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
            stopped = (
                await conn.execute(
                    runs.update()
                    .where(runs.c.id == run_id, runs.c.status.notin_(FINISHED))
                    .values(status="cancelled", finished_at=now, pending=None)
                    .returning(runs.c.id)
                )
            ).first()
            if stopped is None:
                # It finished meanwhile (another process): its own end stays as it is.
                return str(
                    (
                        await conn.execute(sa.select(runs.c.status).where(runs.c.id == run_id))
                    ).scalar()
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
            await self._wake(conn, EVENTS_CHANNEL, run_id)
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
                        workspace_id=run.get("workspace_id") or context.current(),
                        run_id=run["id"],
                        thread_id=run["thread_id"],
                        flow_id=run["flow_id"],
                        flow_name=run["flow_name"],
                        step=intr.get("step") or "",
                        # Step names have no length limit; the column (on Postgres) does.
                        step_name=(intr.get("step_name") or intr.get("step") or "")[:200],
                        path=intr.get("path") or [],
                        interrupt_id=intr["id"],
                        request=intr.get("request") or {},
                        status="open",
                        created_at=now,
                    )
                )
                ids.append(item_id)
        return ids

    async def inbox_answers(self, run_id: str, interrupt_ids: list[str]) -> dict[str, Any]:
        """Answers already given for these waiting questions of a run ({interrupt_id: answer})."""
        if not interrupt_ids:
            return {}
        query = sa.select(inbox.c.interrupt_id, inbox.c.answer).where(
            inbox.c.run_id == run_id,
            inbox.c.status == "answered",
            inbox.c.interrupt_id.in_(interrupt_ids),
        )
        async with self.reader.connect() as conn:
            return {r.interrupt_id: r.answer for r in await conn.execute(query)}

    async def list_inbox(
        self, status: str | None = "open", limit: int = 200
    ) -> list[dict[str, Any]]:
        query = (
            sa.select(inbox)
            .where(inbox.c.workspace_id == context.current())
            .order_by(inbox.c.created_at.desc())
            .limit(limit)
        )
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
                    workspace_id=context.current(),
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
        query = (
            sa.select(triggers)
            .where(triggers.c.workspace_id == context.current())
            .order_by(triggers.c.created_at)
        )
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
                    triggers.c.kind.in_(("schedule", "email")),
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

    # ── the audit log ───────────────────────────────────────────────────────

    async def add_audit(self, **entry: Any) -> int:
        async with self._tx() as conn:
            done = await conn.execute(
                audit_log.insert().values(at=entry.pop("at", None) or time.time(), **entry)
            )
            return int(done.inserted_primary_key[0])

    async def list_audit(
        self,
        *,
        actor_id: str | None = None,
        action: str | None = None,
        target: str | None = None,
        since: float | None = None,
        before: int | None = None,
        limit: int = 100,
        workspace: str | None = None,
    ) -> list[dict[str, Any]]:
        """Newest first, of the workspace in hand (or ``workspace``). ``action`` may end in "."
        for a kind (``flow.``); ``before`` is an id."""
        query = sa.select(audit_log).where(
            audit_log.c.workspace_id == (workspace or context.current())
        )
        if actor_id:
            query = query.where(audit_log.c.actor_id == actor_id)
        if action:
            query = query.where(
                audit_log.c.action.startswith(action, autoescape=True)
                if action.endswith(".")
                else audit_log.c.action == action
            )
        if target:
            query = query.where(audit_log.c.target == target)
        if since is not None:
            query = query.where(audit_log.c.at >= since)
        if before is not None:
            query = query.where(audit_log.c.id < before)
        query = query.order_by(audit_log.c.id.desc()).limit(max(1, min(limit, 1000)))
        async with self.reader.connect() as conn:
            return [dict(r._mapping) for r in await conn.execute(query)]

    async def settings_starting(self, prefix: str) -> dict[str, Any]:
        async with self.reader.connect() as conn:
            rows = await conn.execute(
                sa.select(settings.c.key, settings.c.value).where(
                    settings.c.key.startswith(prefix, autoescape=True)
                )
            )
            return {r.key: r.value for r in rows}

    async def delete_setting(self, key: str) -> None:
        async with self._tx() as conn:
            await conn.execute(settings.delete().where(settings.c.key == key))

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
