"""Migrations for the run database (Alembic).

Easy Chain upgrades its database when it starts (the API server, each worker, the CLI), so
there is nothing to run by hand; ``easychain db status`` and ``easychain db upgrade`` are there
for operators who want to look first or upgrade before rolling out new workers.

- A new database is built by running every migration from the start.
- A database made before migrations existed (Easy Chain 0.4.0 and earlier, with the tables
  ``create_all`` made) is recognised and stamped at the baseline, then upgraded.
- A database already ahead of this Easy Chain (a newer one upgraded it) is refused, with a
  message, rather than used.
- The caller holds a lock while this runs, so processes starting together don't race.

To change the schema: change ``server/db.py``, then add a migration in ``versions/``. From
``python/``, with ``EASYCHAIN_DATABASE_URL`` pointing at an up-to-date database::

    uv run alembic -c src/easychain/server/migrations/alembic.ini \
        revision --autogenerate --rev-id 0002 -m "what changed"

Check the ``upgrade()`` it wrote, then run ``tests/test_migrations.py``: it checks that the
migrations build exactly the tables ``db.py`` describes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError

HERE = Path(__file__).parent
BASELINE = "0001"


class NewerDatabase(RuntimeError):
    """The database was upgraded by a newer Easy Chain."""


def config(connection: Any = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(HERE))
    cfg.attributes["connection"] = connection
    return cfg


def head() -> str:
    return ScriptDirectory.from_config(config()).get_current_head() or BASELINE


def current(connection: Any) -> str | None:
    """The revision the database is at (None: not set up, or made before migrations)."""
    return MigrationContext.configure(connection).get_current_revision()


def _made_by_create_all(connection: Any) -> bool:
    """Tables from Easy Chain 0.4.0 or earlier, which made them with create_all."""
    names = set(sa.inspect(connection).get_table_names())
    return {"easychain_meta", "runs", "jobs"} <= names


def pending(connection: Any) -> list[str]:
    """The migrations upgrade() would run, oldest first."""
    script = ScriptDirectory.from_config(config())
    at = current(connection)
    if at is None and _made_by_create_all(connection):
        at = BASELINE
    try:
        steps = list(script.iterate_revisions("head", at))
    except CommandError as exc:
        raise NewerDatabase(_newer(at)) from exc
    return [f"{rev.revision} {rev.doc}" for rev in reversed(steps)]


def _newer(revision: str | None) -> str:
    return (
        f"The database was upgraded by a newer Easy Chain (it is at migration {revision}, which "
        "this version doesn't know). Upgrade Easy Chain, or point it at another database."
    )


def upgrade(connection: Any) -> list[str]:
    """Bring the database up to date, in the caller's transaction. Returns what ran."""
    cfg = config(connection)
    script = ScriptDirectory.from_config(cfg)
    at = current(connection)
    if at is not None:
        try:
            script.get_revision(at)
        except CommandError as exc:
            raise NewerDatabase(_newer(at)) from exc
    todo = pending(connection)
    if at is None and _made_by_create_all(connection):
        command.stamp(cfg, BASELINE)
    if todo:
        command.upgrade(cfg, "head")
    return todo
