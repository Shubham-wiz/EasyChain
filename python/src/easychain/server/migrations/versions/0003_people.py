"""People: accounts, workspaces and roles, signed-in sessions, API keys

Revision 0003, after 0002. Phase 5.6. Everyone already using Easy Chain is in the one
workspace this adds ("default"); sign-in is optional on a server that only this computer can
reach (server/auth.py).
"""

from __future__ import annotations

import time

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("email", sa.String(320)),
        sa.Column("name", sa.String(200)),
        sa.Column("password_hash", sa.String(300), nullable=True),
        sa.Column("disabled", sa.Boolean),
        sa.Column("created_at", sa.Float),
        sa.Column("last_login_at", sa.Float, nullable=True),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    workspaces = op.create_table(
        "workspaces",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(200)),
        sa.Column("created_at", sa.Float),
    )
    op.bulk_insert(workspaces, [{"id": "default", "name": "Workspace", "created_at": time.time()}])
    op.create_table(
        "memberships",
        sa.Column("workspace_id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), primary_key=True),
        sa.Column("role", sa.String(20)),
        sa.Column("created_at", sa.Float),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(40)),
        sa.Column("created_at", sa.Float),
        sa.Column("expires_at", sa.Float),
        sa.Column("last_seen_at", sa.Float),
    )
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column("user_id", sa.String(40)),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("name", sa.String(200)),
        sa.Column("role", sa.String(20)),
        sa.Column("secret_hash", sa.String(64)),
        sa.Column("created_at", sa.Float),
        sa.Column("last_used_at", sa.Float, nullable=True),
        sa.Column("revoked_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_api_keys_user_id"), "api_keys", ["user_id"])


def downgrade() -> None:
    for table in ("api_keys", "sessions", "memberships", "workspaces", "users"):
        op.drop_table(table)
