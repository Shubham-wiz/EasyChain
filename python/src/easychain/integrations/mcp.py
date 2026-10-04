"""MCP (Model Context Protocol) servers: their tools for agents and the MCP tool step.

``mcp_tools`` and ``mcp_text`` are copied into generated code; exported code finds
how to reach each server in the EASYCHAIN_MCP_SERVERS environment variable. Inside
Easy Chain the connections come from Settings → MCP servers instead (see
``connections``), where local (stdio) servers must be on the approved list.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from typing import Any

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient


async def mcp_tools(servers: dict[str, list[str]]) -> list[BaseTool]:
    """Tools from MCP servers (an empty list means all of a server's tools).

    How to reach each server comes from the EASYCHAIN_MCP_SERVERS environment variable,
    as JSON: {"docs": {"transport": "streamable_http", "url": "https://example.com/mcp"}}.
    """
    connections = json.loads(os.environ.get("EASYCHAIN_MCP_SERVERS") or "{}")
    missing = [name for name in servers if name not in connections]
    if missing:
        raise RuntimeError(f"No connection for the MCP server(s) {', '.join(missing)}.")
    client = MultiServerMCPClient({name: connections[name] for name in servers})
    tools: list[BaseTool] = []
    for name, wanted in servers.items():
        found = await client.get_tools(server_name=name)
        tools += [t for t in found if not wanted or t.name in wanted]
    return tools


def mcp_text(result: Any) -> Any:
    """An MCP tool's result as plain text (MCP returns a list of content blocks)."""
    if isinstance(result, tuple):
        result = result[0]
    if isinstance(result, list):
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in result]
        return "\n".join(p for p in parts if p)
    return result


HELPER_FUNCTIONS = (mcp_tools,)


# ── inside Easy Chain ────────────────────────────────────────────────────────


class McpNotAllowed(RuntimeError):
    """A local MCP server whose command isn't on the approved list."""


def _secrets(value: str) -> str:
    return re.sub(
        r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), value
    )


def split_command(command: str) -> list[str]:
    """Split a command line the way this system's shell would.

    Windows paths keep their backslashes (``C:\\Tools\\server.exe``); quotes group words with
    spaces on both systems.
    """
    if os.name != "nt":
        return shlex.split(command)
    lexer = shlex.shlex(command, posix=False)
    lexer.whitespace_split = True
    return [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in lexer]


def _same_command(a: str, b: str) -> bool:
    return a.lower() == b.lower() if os.name == "nt" else a == b


def connection(server: dict[str, Any], allowed_commands: list[str]) -> dict[str, Any]:
    """A langchain-mcp-adapters connection for one server from Settings → MCP servers."""
    transport = server.get("transport") or "http"
    name = server.get("name") or server.get("id")
    if transport == "stdio":
        try:
            parts = split_command(server.get("command") or "")
        except ValueError as exc:
            raise McpNotAllowed(
                f"The command of the MCP server “{name}” can't be read ({exc}). "
                "Check its quotes in Settings → MCP servers."
            ) from None
        if not parts:
            raise McpNotAllowed(f"The MCP server “{name}” has no command to run.")
        if not any(_same_command(parts[0], allowed) for allowed in allowed_commands):
            raise McpNotAllowed(
                f"The MCP server “{name}” runs `{parts[0]}` on "
                "this machine, and that command isn't on the approved list (Settings → MCP "
                "servers, in Pro mode)."
            )
        # Only the server's own variables: the MCP client adds the basics (PATH, HOME and
        # so on), and Easy Chain's other secrets stay out of a third-party process.
        return {
            "transport": "stdio",
            "command": parts[0],
            "args": parts[1:] + list(server.get("args") or []),
            "env": {k: _secrets(v) for k, v in (server.get("env") or {}).items()},
        }
    out: dict[str, Any] = {
        "transport": "sse" if transport == "sse" else "streamable_http",
        "url": _secrets(server.get("url") or ""),
    }
    headers = {k: _secrets(v) for k, v in (server.get("headers") or {}).items()}
    if headers:
        out["headers"] = headers
    return out


def connections(settings: dict[str, Any]) -> dict[str, Any]:
    """All usable connections; servers that aren't allowed map to an error message."""
    allowed = list(settings.get("allowed_commands") or [])
    out: dict[str, Any] = {}
    for server in settings.get("servers") or []:
        try:
            out[server["id"]] = connection(server, allowed)
        except McpNotAllowed as exc:
            out[server["id"]] = {"error": str(exc)}
    return out


async def list_tools(server: dict[str, Any], allowed_commands: list[str]) -> list[dict[str, Any]]:
    client = MultiServerMCPClient({"server": connection(server, allowed_commands)})
    tools = await client.get_tools(server_name="server")
    return [
        {
            "name": t.name,
            "description": t.description or "",
            "args": (t.args_schema if isinstance(t.args_schema, dict) else {}).get(
                "properties", {}
            ),
        }
        for t in tools
    ]
