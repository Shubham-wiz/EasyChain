"""The MCP tool step: call one tool on an MCP server."""

from __future__ import annotations

from typing import Any

from ..compiler.issues import Issue, error
from ..compiler.pycode import docstring, py_str
from ..compiler.templates import variables
from .ai import missing_field_issue
from .base import FormField, StepCode, StepHandler, template_value


class McpToolHandler(StepHandler):
    type = "mcp_tool"
    label = "MCP tool"
    technical = "Action · MCP (langchain-mcp-adapters)"
    category = "actions"
    icon = "plug"
    summary = "Calls a tool on an MCP server you connected in Settings."
    beginner = False
    form = [
        FormField(
            key="server",
            label="MCP server",
            kind="mcp_server",
            help="Connect servers in Settings → MCP servers.",
        ),
        FormField(
            key="tool",
            label="Tool",
            kind="mcp_tool",
            help="One of the server's tools.",
        ),
        FormField(
            key="arguments",
            label="Arguments",
            kind="key_value",
            help="What to send to the tool. Put Flow Data in with {field}.",
            example="query = {question}",
        ),
        FormField(key="save_as", label="Save the result as", kind="field_name", example="result"),
    ]

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "any"}

    def reads(self, step: Any, an: Any) -> set[str]:
        return {name for value in step.settings.arguments.values() for name in variables(value)}

    def is_async(self, step: Any, an: Any) -> bool:
        return True

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        if not s.server:
            issues.append(
                error("mcp_no_server", "Pick the MCP server.", step=step.id, setting="server")
            )
        if not s.tool:
            issues.append(
                error("mcp_no_tool", "Pick the tool to call.", step=step.id, setting="tool")
            )
        available = an.available_fields(step.id)
        for name in sorted(self.reads(step, an)):
            if name not in available:
                issues.append(
                    missing_field_issue(step, name, an, "arguments", "This tool call uses")
                )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        loader = ctx.helper("mcp_tools")
        text = ctx.helper("mcp_text")
        args = (
            "{"
            + ", ".join(f"{py_str(k)}: {template_value(v, ctx)}" for k, v in s.arguments.items())
            + "}"
        )
        doc = docstring(
            f"{self.title(step)}\n\nCalls `{s.tool}` on the MCP server “{s.server}” and saves the "
            f"result as `{s.save_as}`."
        )
        code = (
            f"async def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n"
            f"    found = await {loader}({{{py_str(s.server)}: [{py_str(s.tool)}]}})\n"
            "    if not found:\n"
            f"        raise ValueError({py_str(f'The MCP server “{s.server}” has no tool called “{s.tool}”.')})\n"
            f"    result = await found[0].ainvoke({args})\n"
            f"    return {{{py_str(s.save_as)}: {text}(result)}}"
        )
        return StepCode([code], node=fn, is_async=True)
