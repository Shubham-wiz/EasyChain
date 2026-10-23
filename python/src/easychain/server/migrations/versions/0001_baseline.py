"""The run database as Easy Chain 0.4.0 made it

Revision 0001, the first. Databases made before migrations existed are stamped here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _bigint_id() -> sa.Column:
    return sa.Column(
        "id",
        sa.BigInteger().with_variant(sa.Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )


def upgrade() -> None:
    op.create_table(
        "flow_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("flow_id", sa.String(100)),
        sa.Column("name", sa.String(200)),
        sa.Column("hash", sa.String(64)),
        sa.Column("bundle", sa.JSON),
        sa.Column("note", sa.String(200)),
        sa.Column("created_at", sa.Float),
    )
    op.create_index(op.f("ix_flow_versions_flow_id"), "flow_versions", ["flow_id"])
    op.create_index(op.f("ix_flow_versions_hash"), "flow_versions", ["hash"])

    op.create_table(
        "runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("flow_id", sa.String(100), nullable=True),
        sa.Column("flow_name", sa.String(200)),
        sa.Column("version_id", sa.String(40)),
        sa.Column("thread_id", sa.String(100)),
        sa.Column("status", sa.String(20)),
        sa.Column("trigger", sa.String(40)),
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
        sa.Column("created_at", sa.Float),
        sa.Column("started_at", sa.Float, nullable=True),
        sa.Column("finished_at", sa.Float, nullable=True),
        sa.Column("duration_ms", sa.Float, nullable=True),
    )
    for column in ("flow_id", "thread_id", "status", "created_at"):
        op.create_index(op.f(f"ix_runs_{column}"), "runs", [column])

    op.create_table(
        "run_events",
        _bigint_id(),
        sa.Column("run_id", sa.String(40)),
        sa.Column("type", sa.String(30)),
        sa.Column("data", sa.JSON),
        sa.Column("ts", sa.Float),
    )
    op.create_index(op.f("ix_run_events_run_id"), "run_events", ["run_id"])

    op.create_table(
        "jobs",
        _bigint_id(),
        sa.Column("run_id", sa.String(40)),
        sa.Column("action", sa.String(20)),
        sa.Column("payload", sa.JSON),
        sa.Column("thread_id", sa.String(100)),
        sa.Column("flow_id", sa.String(100), nullable=True),
        sa.Column("flow_limit", sa.Integer, nullable=True),
        sa.Column("status", sa.String(20)),
        sa.Column("lease_owner", sa.String(80), nullable=True),
        sa.Column("lease_expires", sa.Float, nullable=True),
        sa.Column("attempts", sa.Integer),
        sa.Column("cancel_requested", sa.Boolean),
        sa.Column("available_at", sa.Float),
        sa.Column("created_at", sa.Float),
        sa.Column("finished_at", sa.Float, nullable=True),
    )
    for column in ("run_id", "thread_id", "status"):
        op.create_index(op.f(f"ix_jobs_{column}"), "jobs", [column])

    op.create_table(
        "inbox",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40)),
        sa.Column("thread_id", sa.String(100)),
        sa.Column("flow_id", sa.String(100), nullable=True),
        sa.Column("flow_name", sa.String(200)),
        sa.Column("step", sa.String(100)),
        sa.Column("step_name", sa.String(200)),
        sa.Column("path", sa.JSON),
        sa.Column("interrupt_id", sa.String(80)),
        sa.Column("request", sa.JSON),
        sa.Column("status", sa.String(20)),
        sa.Column("answer", sa.JSON, nullable=True),
        sa.Column("answered_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.Float),
        sa.Column("answered_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_inbox_run_id"), "inbox", ["run_id"])
    op.create_index(op.f("ix_inbox_status"), "inbox", ["status"])

    op.create_table(
        "triggers",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("flow_id", sa.String(100)),
        sa.Column("kind", sa.String(20)),
        sa.Column("name", sa.String(200)),
        sa.Column("config", sa.JSON),
        sa.Column("enabled", sa.Boolean),
        sa.Column("token", sa.String(80)),
        sa.Column("next_fire_at", sa.Float, nullable=True),
        sa.Column("last_fired_at", sa.Float, nullable=True),
        sa.Column("last_run_id", sa.String(40), nullable=True),
        sa.Column("created_at", sa.Float),
    )
    op.create_index(op.f("ix_triggers_flow_id"), "triggers", ["flow_id"])

    op.create_table(
        "settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.JSON),
    )
    op.create_table(
        "easychain_meta",
        sa.Column("key", sa.String(40), primary_key=True),
        sa.Column("value", sa.String(200)),
    )


def downgrade() -> None:
    for table in (
        "easychain_meta",
        "settings",
        "triggers",
        "inbox",
        "jobs",
        "run_events",
        "runs",
        "flow_versions",
    ):
        op.drop_table(table)
