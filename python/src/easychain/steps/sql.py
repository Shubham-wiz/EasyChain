"""The Database query step: run SQL, or describe the tables for an agent."""

from __future__ import annotations

import re
from typing import Any

from ..compiler.issues import Fix, Issue, error, warning
from ..compiler.pycode import docstring, py_str
from .ai import missing_field_issue
from .base import FormField, StepCode, StepHandler

_PLACEHOLDER = re.compile(r"""(['"]?)\{([a-z][a-z0-9_]*)\}\1""")


def sql_fields(query: str) -> list[str]:
    return list(dict.fromkeys(m.group(2) for m in _PLACEHOLDER.finditer(query)))


def lone_field(query: str) -> str | None:
    match = re.fullmatch(r"\s*\{([a-z][a-z0-9_]*)\}\s*", query)
    return match.group(1) if match else None


def bind(query: str) -> str:
    """{field} (with or without quotes around it) becomes a :field parameter."""
    return _PLACEHOLDER.sub(lambda m: f":{m.group(2)}", query)


class SqlQueryHandler(StepHandler):
    type = "sql_query"
    label = "Database query"
    technical = "Action · SQLAlchemy"
    category = "actions"
    icon = "database"
    summary = "Runs a SQL query on a database, or describes its tables for an agent."
    form = [
        FormField(
            key="connection",
            label="Database",
            kind="text",
            placeholder="postgresql://user:{secret:DB_PASSWORD}@host/db",
            help="The database URL. Keep passwords in secrets: {secret:NAME}. SQLite files work "
            "too: sqlite:///path/to/file.db.",
            example="sqlite:///{home}/samples/shop.db",
        ),
        FormField(
            key="mode",
            label="Do",
            kind="select",
            options=[
                {"value": "query", "label": "Run a query"},
                {"value": "schema", "label": "Describe the tables (for an agent)"},
            ],
        ),
        FormField(
            key="query",
            label="SQL",
            kind="sql",
            help="Put Flow Data in with {field}; values are sent safely as parameters. Use a lone "
            "{sql} when an agent writes the query.",
            example="SELECT status, total FROM orders WHERE id = {order_id}",
            show_if={"mode": "query"},
        ),
        FormField(
            key="read_only",
            label="Read only",
            kind="switch",
            help="Queries run in a read-only transaction, so nothing can be changed by mistake "
            "(keep this on for agents).",
            show_if={"mode": "query"},
        ),
        FormField(
            key="max_rows",
            label="Most rows",
            kind="number",
            min=1,
            advanced=True,
            show_if={"mode": "query"},
        ),
        FormField(
            key="save_as",
            label="Save as",
            kind="field_name",
            help="The rows, as a list of {column: value}; or the description of the tables.",
        ),
    ]

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "list" if step.settings.mode == "query" else "text"}

    def reads(self, step: Any, an: Any) -> set[str]:
        if step.settings.mode != "query":
            return set()
        return set(sql_fields(step.settings.query))

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        if not s.connection.strip():
            issues.append(
                error(
                    "sql_no_connection",
                    "Which database? Add its URL.",
                    step=step.id,
                    setting="connection",
                    hint="For example postgresql://user:{secret:DB_PASSWORD}@host/db.",
                )
            )
        elif re.search(r"://[^/@:]+:[^{/@][^/@]*@", s.connection):
            issues.append(
                warning(
                    "sql_password_in_url",
                    "The database password is written into the flow. Keep it in a secret instead.",
                    step=step.id,
                    setting="connection",
                    hint="Write it as {secret:DB_PASSWORD} and add DB_PASSWORD under Settings → API keys.",
                )
            )
        if s.mode == "query":
            if not s.query.strip():
                issues.append(
                    error("sql_empty", "Write the SQL to run.", step=step.id, setting="query")
                )
            available = an.available_fields(step.id)
            for name in sql_fields(s.query):
                if name not in available:
                    issues.append(missing_field_issue(step, name, an, "query", "This query uses"))
            if not s.read_only and step.id in an.tool_of:
                issues.append(
                    warning(
                        "sql_agent_can_write",
                        "An agent can change data with this query. Keep it read-only, or ask a "
                        "person to approve each call.",
                        step=step.id,
                        setting="read_only",
                        fix=Fix(
                            "set_setting", "Make it read-only", {"key": "read_only", "value": True}
                        ),
                    )
                )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        ctx.helper("sql")
        connection = py_str(s.connection)
        if s.mode == "schema":
            doc = docstring(
                f"{self.title(step)}\n\nDescribes the database's tables and columns and saves it "
                f"as `{s.save_as}`."
            )
            body = f"    return {{{py_str(s.save_as)}: describe_database({connection})}}"
            return StepCode(
                [f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n{body}"], node=fn
            )
        options = ""
        if not s.read_only:
            options += ", read_only=False"
        if s.max_rows != 100:
            options += f", max_rows={s.max_rows}"
        field = lone_field(s.query)
        if field:
            sql_src = f'str(data.get({py_str(field)}) or "")'
            call = f"run_sql({connection}, {sql_src}{options})"
            what = f"Runs the SQL in `{field}`"
        else:
            names = sql_fields(s.query)
            params = "{" + ", ".join(f"{py_str(n)}: data.get({py_str(n)})" for n in names) + "}"
            const = ctx.names.claim(f"{step.id}_sql".upper())
            ctx_const = f"{const} = {py_str(bind(s.query).strip())}"
            call = (
                f"run_sql({connection}, {const}, {params}{options})"
                if names
                else f"run_sql({connection}, {const}{options})"
            )
            what = "Runs a query"
        lines = [f"    rows = {call}"]
        if len(lines[0]) > 96:
            parts = call[len("run_sql(") : -1]
            lines = ["    rows = run_sql(", f"        {parts},", "    )"]
        mode = "read only" if s.read_only else "it may change data"
        doc = docstring(
            f"{self.title(step)}\n\n{what} on the database ({mode}) and saves up to {s.max_rows} "
            f"rows as `{s.save_as}`."
        )
        code = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n"
            + "\n".join(lines)
            + f"\n    return {{{py_str(s.save_as)}: rows}}"
        )
        definitions = [code] if field else [ctx_const, code]
        return StepCode(definitions, node=fn)
