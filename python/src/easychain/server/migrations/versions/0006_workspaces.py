"""Workspaces: which one each flow version, run, Inbox item and trigger belongs to

Revision 0006, after 0005. Phase 5.6d. Everything already there belongs to "default", which
is now called "Default workspace" (it was "Workspace") unless someone renamed it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("flow_versions", "runs", "inbox", "triggers")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("workspace_id", sa.String(40), server_default="default"))
        op.create_index(op.f(f"ix_{table}_workspace_id"), table, ["workspace_id"])
    op.execute(
        "UPDATE workspaces SET name = 'Default workspace' WHERE id = 'default' AND name = 'Workspace'"
    )


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(op.f(f"ix_{table}_workspace_id"), table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("workspace_id")
