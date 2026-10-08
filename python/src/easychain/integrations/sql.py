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


def sql_without_strings(statement: str) -> str:
    """The SQL with its quoted text and comments blanked out, so checks only see the SQL itself."""
    return re.sub(
        r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", " ", statement, flags=re.S
    )


def check_read_only_sql(statement: str, dialect: str) -> None:
    """Refuse SQL that could change data or settings in a read-only query."""
    code = sql_without_strings(statement).strip().lower()
    if not re.match(r"(select|with|explain|show|describe|pragma|values)\b", code):
        raise ValueError(
            "This query is read-only, so it can only read data (SELECT). "
            "Turn off read-only to change data."
        )
    if code.startswith("pragma") and (
        "=" in code
        or not re.match(
            r"pragma\s+(\w+\.)?(table_info|table_xinfo|table_list|index_list|index_info|"
            r"index_xinfo|foreign_key_list|database_list|collation_list|function_list|"
            r"pragma_list|module_list|compile_options|user_version|schema_version|encoding|"
            r"page_count|page_size|freelist_count)\b",
            code,
        )
    ):
        raise ValueError(
            "A read-only query can only use PRAGMAs that describe the database "
            "(like table_info), not ones that change settings."
        )
    if dialect not in ("sqlite", "postgresql", "mysql", "mariadb") and re.search(
        r"\b(insert|update|delete|merge|upsert|replace|create|alter|drop|truncate|rename|"
        r"grant|revoke|call|exec|execute|copy|attach|detach|vacuum|lock|set)\b",
        code,
    ):
        # Easy Chain can't make a read-only transaction on this database, so be strict.
        raise ValueError(
            "This query is read-only, so it can only read data. On this kind of database, "
            "also connect as a user that can only read."
        )


def run_sql(
    connection: str,
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    read_only: bool = True,
    max_rows: int = 100,
    timeout_s: int = 60,
) -> list[dict[str, Any]]:
    """Run one SQL statement; rows come back as a list of {column: value}.

    Read-only queries run in a read-only transaction (SQLite, Postgres, MySQL), so they can't
    change anything even if the SQL tries to. Other changes are committed, including ones that
    return rows (INSERT … RETURNING).
    """
    statement = sql.strip().rstrip(";").strip()
    if not statement:
        raise ValueError("There is no SQL to run.")
    if ";" in sql_without_strings(statement):
        raise ValueError("Run one SQL statement at a time.")
    engine = sql_engine(connection)
    dialect = engine.dialect.name
    if read_only:
        check_read_only_sql(statement, dialect)
    # A plain read is fetched as it is read, so max_rows also limits what leaves the database.
    stream = {"stream_results": bool(re.match(r"(?is)(select|values)\b", statement))}
    with engine.connect() as conn:
        sqlite = dialect == "sqlite"
        try:
            if read_only and sqlite:
                conn.exec_driver_sql("PRAGMA query_only = ON")
            elif read_only and dialect in ("postgresql", "mysql", "mariadb"):
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            if dialect == "postgresql":
                conn.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_s * 1000)}")
            if params:
                result = conn.execute(sa.text(statement), params, execution_options=stream)
            else:
                result = conn.exec_driver_sql(statement, execution_options=stream)
            if not result.returns_rows:
                changed = result.rowcount
                conn.commit()
                return [{"rows_changed": changed}]
            rows = [
                {k: sql_value(v) for k, v in row.items()}
                for row in result.mappings().fetchmany(max_rows)
            ]
            result.close()
            if not read_only:
                conn.commit()
            return rows
        finally:
            if read_only and sqlite:
                conn.rollback()
                conn.exec_driver_sql("PRAGMA query_only = OFF")


def describe_database(connection: str, count_up_to: int = 10000) -> str:
    """The tables and their columns (with types, keys and row counts), for an AI to write SQL.

    Counting reads at most ``count_up_to`` rows of a table, so big tables stay quick; past
    that, Postgres gives its own estimate and other databases say "more than".
    """
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
            # sa.table quotes the name the way this database does (`order` on MySQL).
            rows = sa.select(sa.literal_column("1")).select_from(sa.table(table))
            rows = rows.limit(count_up_to + 1).subquery()
            count = conn.execute(sa.select(sa.func.count()).select_from(rows)).scalar() or 0
            size = f"{count} rows"
            if count > count_up_to:
                size = f"more than {count_up_to} rows"
                if engine.dialect.name == "postgresql":
                    estimate = conn.execute(
                        sa.text("SELECT reltuples FROM pg_class WHERE oid = to_regclass(:t)"),
                        {"t": engine.dialect.identifier_preparer.quote(table)},
                    ).scalar()
                    if estimate and estimate > count_up_to:
                        size = f"about {int(estimate)} rows"
            lines.append(f"{table} ({size}): " + ", ".join(parts))
    return "\n".join(lines) or "The database has no tables."


HELPER_GLOBALS = ("SQL_ENGINES",)
HELPER_FUNCTIONS = (
    sql_engine,
    sql_value,
    sql_without_strings,
    check_read_only_sql,
    run_sql,
    describe_database,
)
