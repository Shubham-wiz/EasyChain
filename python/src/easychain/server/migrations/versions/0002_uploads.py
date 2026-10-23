"""Files added to a Knowledge Base, kept until a worker has read them

Revision 0002, after 0001. Knowledge Base documents are read by workers from the job queue
(Phase 5.8), so an uploaded file waits in the database rather than on the API server's disk.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(300)),
        sa.Column("content_type", sa.String(200), nullable=True),
        sa.Column("size", sa.Integer),
        sa.Column("data", sa.LargeBinary),
        sa.Column("created_at", sa.Float),
    )


def downgrade() -> None:
    op.drop_table("uploads")
