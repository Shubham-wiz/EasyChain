"""Migrations of the run database (Alembic): they build exactly the tables server/db.py
describes, a database Easy Chain 0.4.0 made is upgraded in place with its data, a database a
newer Easy Chain upgraded is refused, and `easychain db` reports and upgrades."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from easychain.cli import main
from easychain.server import migrations
from easychain.server.db import Database, meta, metadata

from . import pg

# The tables Easy Chain 0.4.0 made (the baseline, 0001).
TABLES_040 = {
    "flow_versions",
    "runs",
    "run_events",
    "jobs",
    "inbox",
    "triggers",
    "settings",
    "easychain_meta",
}
# Columns later migrations add to those tables.
ADDED_SINCE_040 = {
    (table, "workspace_id") for table in ("flow_versions", "runs", "inbox", "triggers")
} | {("runs", "environment"), ("runs", "started_by"), ("runs", "started_by_name")}


def _differences(url: str) -> list:
    engine = sa.create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            return compare_metadata(ctx, metadata)
    finally:
        engine.dispose()


async def test_the_migrations_build_exactly_the_tables_db_py_describes(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'fresh.db'}"
    db = await Database.connect(url)
    await db.close()
    assert db.migrated and db.migrated[0].startswith(migrations.BASELINE)
    assert _differences(url) == [], "a change to db.py needs a migration (see server/migrations)"


async def test_the_migrations_match_on_postgres_too():
    if not pg.available():
        pytest.skip("Postgres isn't available here")
    with pg.server() as base:
        url = pg.new_database(base)
        db = await Database.connect(url)
        await db.close()
        assert _differences(url) == []


async def test_a_database_made_before_migrations_is_upgraded_in_place(tmp_path: Path):
    # Easy Chain 0.4.0 made the tables with create_all and kept a schema number.
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = sa.create_engine(url)
    legacy = sa.MetaData()
    for table in metadata.sorted_tables:
        if table.name in TABLES_040:
            columns = [c for c in table.columns if (table.name, c.name) not in ADDED_SINCE_040]
            sa.Table(table.name, legacy, *[c._copy() for c in columns])
    legacy.create_all(engine)
    with engine.begin() as conn:
        conn.execute(meta.insert().values(key="schema", value="1"))
    engine.dispose()
    db = await Database.connect(url)
    run_id = await db.create_run(
        flow_id="f", flow_name="F", version_id="v", thread_id="t", inputs={"q": 1}, options={}
    )
    await db.close()
    # The baseline is stamped, not run again over the old tables; later migrations run.
    ran = [m.split()[0] for m in db.migrated]
    assert ran[0] == "0002" and ran[-1] == migrations.head()
    again = await Database.connect(url)
    assert (await again.get_run(run_id))["inputs"] == {"q": 1}
    await again.close()
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        assert migrations.current(conn) == migrations.head()
    engine.dispose()


async def test_a_database_a_newer_easy_chain_upgraded_is_refused(tmp_path: Path):
    url = f"sqlite:///{tmp_path / 'newer.db'}"
    await (await Database.connect(url)).close()
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '9999'"))
    engine.dispose()
    with pytest.raises(migrations.NewerDatabase, match="newer Easy Chain"):
        await Database.connect(url)


def test_every_migration_can_be_undone_and_redone(tmp_path: Path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'round.db'}")
    with engine.begin() as conn:
        migrations.upgrade(conn)
        command.downgrade(migrations.config(conn), "base")
        assert set(sa.inspect(conn).get_table_names()) <= {"alembic_version"}
        migrations.upgrade(conn)
        assert migrations.current(conn) == migrations.head()
    engine.dispose()


def test_the_db_command_reports_and_upgrades(tmp_path: Path, capsys: pytest.CaptureFixture):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    assert main(["db", "status", "--database-url", url]) == 0
    assert "no database here yet" in capsys.readouterr().out
    assert main(["db", "upgrade", "--database-url", url]) == 0
    out = capsys.readouterr().out
    assert "Upgraded:" in out and migrations.BASELINE in out
    assert main(["db", "status", "--database-url", url]) == 0
    out = capsys.readouterr().out
    assert f"At migration: {migrations.head()}" in out and "Up to date." in out
    assert main(["db", "upgrade", "--database-url", url]) == 0
    assert "Already up to date." in capsys.readouterr().out
