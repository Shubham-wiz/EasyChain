"""Single sign-on: which Easy Chain account each provider's person is

Revision 0005, after 0004. Phase 5.6e.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_identities",
        sa.Column("provider", sa.String(40), primary_key=True),
        sa.Column("subject", sa.String(255), primary_key=True),
        sa.Column("user_id", sa.String(40)),
        sa.Column("email", sa.String(320)),
        sa.Column("created_at", sa.Float),
        sa.Column("last_used_at", sa.Float, nullable=True),
    )
    op.create_index(op.f("ix_user_identities_user_id"), "user_identities", ["user_id"])


def downgrade() -> None:
    op.drop_table("user_identities")
