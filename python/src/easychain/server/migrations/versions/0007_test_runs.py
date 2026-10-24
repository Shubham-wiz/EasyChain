"""Test Runs: a flow's Test Set run against one version, kept with its results

Revision 0007, after 0006. Phase 5.2.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("flow_id", sa.String(100)),
        sa.Column("version_id", sa.String(40)),
        sa.Column("status", sa.String(20)),
        sa.Column("stand_in", sa.Boolean),
        sa.Column("repeat", sa.Integer),
        sa.Column("judge_model", sa.String(200), nullable=True),
        sa.Column("cases", sa.JSON),
        sa.Column("done", sa.Integer),
        sa.Column("results", sa.JSON, nullable=True),
        sa.Column("summary", sa.JSON, nullable=True),
        sa.Column("baseline", sa.Boolean),
        sa.Column("created_by", sa.String(200), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.Float),
        sa.Column("started_at", sa.Float, nullable=True),
        sa.Column("finished_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_test_runs_workspace_id"), "test_runs", ["workspace_id"])
    op.create_index(op.f("ix_test_runs_flow_id"), "test_runs", ["flow_id"])


def downgrade() -> None:
    op.drop_table("test_runs")
