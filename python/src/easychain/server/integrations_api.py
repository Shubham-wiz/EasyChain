"""API for MCP servers (Settings → MCP servers) and OpenAPI import."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from .. import egress
from ..integrations import mcp as mcp_module
from ..integrations import openapi
from .network import policy as network_policy
from .secrets import MASK, masked, masked_url, unmasked_url
from .spaces import space_of

# Parts of an MCP server's settings that can hold tokens: never shown by the API.
_SECRET_PARTS = {"headers": "header", "env": "environment variable"}


class McpServer(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,40}$")
    name: str = ""
    transport: str = Field(default="http", pattern="^(http|sse|stdio)$")
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class McpSettings(BaseModel):
    servers: list[McpServer] = Field(default_factory=list)
    allowed_commands: list[str] = Field(default_factory=list)


class McpToolsRequest(BaseModel):
    server: McpServer | None = None
    server_id: str | None = None


class OpenApiRequest(BaseModel):
    source: str = Field(min_length=1, description="A URL, or the spec as JSON or YAML.")


class OpenApiStepsRequest(OpenApiRequest):
    operations: list[str] = Field(min_length=1)
    server: str | None = None
    auth_header: str | None = None
    auth_secret: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    taken: list[str] = Field(default_factory=list, description="Step ids already in the flow.")


def _shown(settings: dict[str, Any]) -> dict[str, Any]:
    """MCP settings as the API returns them: header and environment values hidden."""
    servers = []
    for server in settings["servers"]:
        server = dict(server)
        for part in _SECRET_PARTS:
            server[part] = {k: masked(v) for k, v in (server.get(part) or {}).items()}
        server["url"] = masked_url(server.get("url"))
        servers.append(server)
    return {**settings, "servers": servers}


def _restored(server: dict[str, Any], saved: list[dict[str, Any]]) -> dict[str, Any]:
    """A server sent back by a client: hidden values, unchanged, keep what was saved."""
    before = next((s for s in saved if s["id"] == server["id"]), {})
    out = dict(server)
    for part, label in _SECRET_PARTS.items():
        values = dict(server.get(part) or {})
        for key, value in values.items():
            if value != MASK:
                continue
            kept = (before.get(part) or {}).get(key)
            if kept is None:
                name = server.get("name") or server["id"]
                raise HTTPException(
                    422,
                    detail={
                        "message": f"The {key} {label} of the MCP server “{name}” is hidden "
                        "(••••••) and nothing is saved for it under this server id. Type its "
                        "value again."
                    },
                )
            values[key] = kept
        out[part] = values
    try:
        out["url"] = unmasked_url(server.get("url"), before.get("url"))
    except ValueError as exc:
        name = server.get("name") or server["id"]
        raise HTTPException(422, detail={"message": f"MCP server “{name}”: {exc}"}) from None
    return out


async def mcp_setting(db: Any, key: str) -> dict[str, Any]:
    """A workspace's MCP settings (``key`` is its settings key). Which commands MCP servers may
    run on this machine is the server's to say, so every workspace uses the default one's list."""
    stored = dict(await db.get_setting(key, {}) or {})
    if key != "mcp":
        server = await db.get_setting("mcp", {}) or {}
        stored["allowed_commands"] = list(server.get("allowed_commands") or [])
    return stored


def add_integration_routes(app: FastAPI, hub: Callable[[], Any]) -> None:
    async def mcp_settings(request: Request) -> dict[str, Any]:
        stored = await mcp_setting(hub().db, space_of(request).setting("mcp"))
        return McpSettings.model_validate(stored).model_dump()

    @app.get("/api/settings/mcp")
    async def get_mcp(request: Request) -> dict[str, Any]:
        return _shown(await mcp_settings(request))

    @app.put("/api/settings/mcp")
    async def put_mcp(body: McpSettings, request: Request) -> dict[str, Any]:
        ids = [s.id for s in body.servers]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, detail={"message": "Two MCP servers share an id."})
        current = await mcp_settings(request)
        value = body.model_dump()
        value["servers"] = [_restored(s, current["servers"]) for s in value["servers"]]
        value["allowed_commands"] = sorted({c.strip() for c in body.allowed_commands if c.strip()})
        key = space_of(request).setting("mcp")
        if key != "mcp":
            if value["allowed_commands"] != sorted(set(current["allowed_commands"])):
                raise HTTPException(
                    403,
                    detail={
                        "message": "Which commands MCP servers may run on this machine applies "
                        "to every workspace, so it is changed in the default workspace, by the "
                        "server's Owners.",
                    },
                )
            await hub().db.set_setting(key, {**value, "allowed_commands": []})
        else:
            await hub().db.set_setting(key, value)
        return _shown(value)

    @app.post("/api/mcp/tools")
    async def mcp_tools(req: McpToolsRequest, request: Request) -> dict[str, Any]:
        settings = await mcp_settings(request)
        server = _restored(req.server.model_dump(), settings["servers"]) if req.server else None
        if server is None:
            server = next((s for s in settings["servers"] if s["id"] == req.server_id), None)
        if server is None:
            raise HTTPException(404, detail={"message": "That MCP server isn't set up here."})
        try:
            with egress.using(await network_policy(app)):
                tools = await asyncio.wait_for(
                    mcp_module.list_tools(server, settings["allowed_commands"]), 30
                )
        except mcp_module.McpNotAllowed as exc:
            raise HTTPException(403, detail={"message": str(exc)}) from exc
        except egress.EgressBlocked as exc:
            raise HTTPException(403, detail={"message": str(exc)}) from exc
        except Exception as exc:
            reason = re.sub(r"\s+", " ", str(exc))[:300] or type(exc).__name__
            raise HTTPException(
                502, detail={"message": f"Couldn't reach the MCP server: {reason}"}
            ) from exc
        return {"tools": tools}

    @app.post("/api/openapi/inspect")
    async def openapi_inspect(req: OpenApiRequest) -> dict[str, Any]:
        try:
            with egress.using(await network_policy(app)):
                doc = await asyncio.to_thread(openapi.load, req.source)
        except openapi.OpenApiError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        info = doc.get("info") or {}
        return {
            "title": info.get("title", ""),
            "server": openapi.base_url(doc),
            "operations": openapi.operations(doc),
        }

    @app.post("/api/openapi/steps")
    async def openapi_steps(req: OpenApiStepsRequest) -> dict[str, Any]:
        try:
            with egress.using(await network_policy(app)):
                doc = await asyncio.to_thread(openapi.load, req.source)
        except openapi.OpenApiError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        return openapi.to_steps(
            doc,
            req.operations,
            server=req.server,
            auth_header=req.auth_header,
            auth_secret=req.auth_secret,
            taken=set(req.taken),
        )
