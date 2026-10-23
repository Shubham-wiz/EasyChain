"""The audit log: who changed what, and when; append-only

Revision 0004, after 0003. Phase 5.6c. Database triggers refuse to change or remove entries,
on SQLite and on Postgres (where TRUNCATE is refused too).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

REFUSED = "The audit log is append-only: entries can't be changed or removed."
QUOTED = REFUSED.replace("'", "''")  # as an SQL string


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer, "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("at", sa.Float),
        sa.Column("workspace_id", sa.String(40)),
        sa.Column("actor_id", sa.String(40), nullable=True),
        sa.Column("actor", sa.String(320)),
        sa.Column("via", sa.String(20)),
        sa.Column("action", sa.String(60)),
        sa.Column("target", sa.String(320), nullable=True),
        sa.Column("detail", sa.JSON),
        sa.Column("address", sa.String(100), nullable=True),
    )
    for column in ("at", "actor_id", "action", "target"):
        op.create_index(op.f(f"ix_audit_log_{column}"), "audit_log", [column])
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION easychain_audit_append_only() RETURNS trigger AS $$ "
            f"BEGIN RAISE EXCEPTION '{QUOTED}'; END $$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
            "FOR EACH ROW EXECUTE FUNCTION easychain_audit_append_only()"
        )
        op.execute(
            "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
            "FOR EACH STATEMENT EXECUTE FUNCTION easychain_audit_append_only()"
        )
    else:
        for event in ("UPDATE", "DELETE"):
            op.execute(
                f"CREATE TRIGGER audit_log_no_{event.lower()} BEFORE {event} ON audit_log "
                f"BEGIN SELECT RAISE(ABORT, '{QUOTED}'); END"
            )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
        op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS easychain_audit_append_only()")
    op.drop_table("audit_log")
