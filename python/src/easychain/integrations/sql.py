"""Database queries for the Database query step (and agents that use it as a tool).

Like ``knowledge.search``, these functions are copied into generated code, so they
may only use the standard library and SQLAlchemy.
"""

from __future__ import annotations

import datetime
import decimal
import os
import re
from typing import Any

import sqlalchemy as sa

SQL_ENGINES: dict[str, Any] = {}


def sql_engine(connection: str) -> sa.Engine:
    """A database from its URL. {secret:NAME} comes from the environment and {home} is the
    Easy Chain folder (~/.easychain, or EASYCHAIN_HOME)."""
    url = re.sub(
        r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), connection
    )
    home = os.environ.get("EASYCHAIN_HOME") or os.path.expanduser("~/.easychain")
    url = url.replace("{home}", home)
    if url not in SQL_ENGINES:
        scheme, _, rest = url.partition("://")
        driver = f"postgresql+psycopg://{rest}" if scheme in ("postgresql", "postgres") else url
        SQL_ENGINES[url] = sa.create_engine(driver, pool_pre_ping=True)
    return SQL_ENGINES[url]


def sql_value(value: Any) -> Any:
    """A database value as plain JSON (dates and decimals become text or numbers)."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return value


def run_sql(
    connection: str,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    read_only: bool = True,
    max_rows: int = 100,
) -> list[dict[str, Any]]:
    """Run one SQL statement; rows come back as a list of {column: value}.

    Read-only queries run in a read-only transaction, so they can't change anything even if
    the SQL tries to.
    """
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise ValueError("There is no SQL to run.")
    if ";" in statement:
        raise ValueError("Run one SQL statement at a time.")
    if read_only and not re.match(
        r"(?is)^(select|with|explain|show|describe|pragma|values)\b", statement
    ):
        raise ValueError(
            "This query is read-only, so it can only read data (SELECT). "
            "Turn off read-only to change data."
        )
    engine = sql_engine(connection)
    with engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        try:
            if read_only and sqlite:
                conn.exec_driver_sql("PRAGMA query_only = ON")
            elif read_only and conn.dialect.name == "postgresql":
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            if params:
                result = conn.execute(sa.text(statement), params)
            else:
                result = conn.exec_driver_sql(statement)
            if not result.returns_rows:
                changed = result.rowcount
                conn.commit()
                return [{"rows_changed": changed}]
            rows = result.mappings().fetchmany(max_rows)
            return [{k: sql_value(v) for k, v in row.items()} for row in rows]
        finally:
            if read_only and sqlite:
                conn.rollback()
                conn.exec_driver_sql("PRAGMA query_only = OFF")


def describe_database(connection: str) -> str:
    """The tables and their columns (with types, keys and row counts), for an AI to write SQL."""
    engine = sql_engine(connection)
    inspector = sa.inspect(engine)
    lines = []
    with engine.connect() as conn:
        for table in inspector.get_table_names():
            columns = inspector.get_columns(table)
            keys = set(inspector.get_pk_constraint(table).get("constrained_columns") or [])
            parts = []
            for column in columns:
                note = " primary key" if column["name"] in keys else ""
                parts.append(f"{column['name']} {column['type']}{note}")
            for fk in inspector.get_foreign_keys(table):
                for col, ref in zip(
                    fk["constrained_columns"], fk["referred_columns"], strict=False
                ):
                    parts.append(f"{col} references {fk['referred_table']}.{ref}")
            count = conn.execute(sa.text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
            lines.append(f"{table} ({count} rows): " + ", ".join(parts))
    return "\n".join(lines) or "The database has no tables."


HELPER_GLOBALS = ("SQL_ENGINES",)
HELPER_FUNCTIONS = (sql_engine, sql_value, run_sql, describe_database)
