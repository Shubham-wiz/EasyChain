"""Alembic environment: runs the migrations on the connection Easy Chain hands it."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context

from easychain.server.db import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    # From the alembic command line (making a new revision): use EASYCHAIN_DATABASE_URL or a
    # throwaway SQLite database, just to compare against.
    import os

    url = os.environ.get("EASYCHAIN_DATABASE_URL", "sqlite:///:memory:")
    connection = sa.create_engine(url.replace("+aiosqlite", "")).connect()

context.configure(
    connection=connection,
    target_metadata=metadata,
    # SQLite can't ALTER most things; batch mode copies the table instead.
    render_as_batch=True,
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
