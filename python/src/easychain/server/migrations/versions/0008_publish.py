"""Publish: environments, what is published where, access keys and their rate limits

Revision 0008, after 0007. Phase 5.4. A run (and a Test Run) can belong to an environment
(dev, staging, production); a flow version is published to one; keys let other apps (and the
chat page) run it, each with a rate limit counted per minute.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("environment", sa.String(20), nullable=True))
    op.create_index(op.f("ix_runs_environment"), "runs", ["environment"])
    op.add_column("test_runs", sa.Column("environment", sa.String(20), nullable=True))
    op.create_table(
        "publications",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("flow_id", sa.String(100)),
        sa.Column("environment", sa.String(20)),
        sa.Column("version_id", sa.String(40)),
        sa.Column("test_run_id", sa.String(40), nullable=True),
        sa.Column("published_by", sa.String(200), nullable=True),
        sa.Column("published_at", sa.Float),
        sa.Column("retired_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_publications_flow_id"), "publications", ["flow_id"])
    op.create_index(op.f("ix_publications_workspace_id"), "publications", ["workspace_id"])
    op.create_table(
        "access_keys",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("flow_id", sa.String(100)),
        sa.Column("environment", sa.String(20)),
        sa.Column("name", sa.String(200)),
        # "secret": Authorization: Bearer epk_<id>_<secret>; "chat": a public chat link
        sa.Column("kind", sa.String(10)),
        sa.Column("secret_hash", sa.String(64), nullable=True),
        sa.Column("per_minute", sa.Integer),
        sa.Column("origins", sa.JSON),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("created_at", sa.Float),
        sa.Column("last_used_at", sa.Float, nullable=True),
        sa.Column("revoked_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_access_keys_flow_id"), "access_keys", ["flow_id"])
    op.create_table(
        "key_usage",
        sa.Column("key_id", sa.String(80), primary_key=True),
        sa.Column("minute", sa.Integer, primary_key=True),
        sa.Column("count", sa.Integer),
    )


def downgrade() -> None:
    op.drop_table("key_usage")
    op.drop_table("access_keys")
    op.drop_table("publications")
    with op.batch_alter_table("test_runs") as batch:
        batch.drop_column("environment")
    op.drop_index(op.f("ix_runs_environment"), "runs")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("environment")
