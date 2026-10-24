"""The model gateway: who started each run, every model call, budget alerts and kept answers

Revision 0009, after 0008. Phase 5.5. A run remembers who started it; each model call a run
(or a Test Run, a Test Set run in the app, the copilot) makes is recorded with its tokens and
cost, which budgets and the spending pages add up; an alert is sent once per budget, period
and level; cached answers wait in ``answer_cache`` until they expire.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("started_by", sa.String(40), nullable=True))
    op.add_column("runs", sa.Column("started_by_name", sa.String(200), nullable=True))
    op.create_table(
        "model_calls",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("at", sa.Float),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("kind", sa.String(20)),
        sa.Column("run_id", sa.String(40), nullable=True),
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
    )
    op.create_index("ix_model_calls_spend", "model_calls", ["workspace_id", "at"])
    op.create_index(op.f("ix_model_calls_run_id"), "model_calls", ["run_id"])
    op.create_table(
        "budget_alerts",
        sa.Column("workspace_id", sa.String(40), primary_key=True),
        sa.Column("budget", sa.String(20), primary_key=True),
        sa.Column("period", sa.String(10), primary_key=True),
        sa.Column("level", sa.Integer, primary_key=True),
        sa.Column("spent", sa.Float),
        sa.Column("amount", sa.Float),
        sa.Column("at", sa.Float),
    )
    op.create_table(
        "answer_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("value", sa.Text),
        sa.Column("created_at", sa.Float),
        sa.Column("expires_at", sa.Float),
    )
    op.create_index(op.f("ix_answer_cache_expires_at"), "answer_cache", ["expires_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_answer_cache_expires_at"), "answer_cache")
    op.drop_table("answer_cache")
    op.drop_table("budget_alerts")
    op.drop_index(op.f("ix_model_calls_run_id"), "model_calls")
    op.drop_index("ix_model_calls_spend", "model_calls")
    op.drop_table("model_calls")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("started_by_name")
        batch.drop_column("started_by")
