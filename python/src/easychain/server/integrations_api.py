"""API for MCP servers (Settings → MCP servers) and OpenAPI import."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..integrations import mcp as mcp_module
from ..integrations import openapi


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


def add_integration_routes(app: FastAPI, hub: Callable[[], Any]) -> None:
    async def mcp_settings() -> dict[str, Any]:
        stored = await hub().db.get_setting("mcp", {}) or {}
        return McpSettings.model_validate(stored).model_dump()

    @app.get("/api/settings/mcp")
    async def get_mcp() -> dict[str, Any]:
        return await mcp_settings()

    @app.put("/api/settings/mcp")
    async def put_mcp(body: McpSettings) -> dict[str, Any]:
        ids = [s.id for s in body.servers]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, detail={"message": "Two MCP servers share an id."})
        value = body.model_dump()
        value["allowed_commands"] = sorted({c.strip() for c in body.allowed_commands if c.strip()})
        await hub().db.set_setting("mcp", value)
        return value

    @app.post("/api/mcp/tools")
    async def mcp_tools(req: McpToolsRequest) -> dict[str, Any]:
        settings = await mcp_settings()
        server = req.server.model_dump() if req.server else None
        if server is None:
            server = next((s for s in settings["servers"] if s["id"] == req.server_id), None)
        if server is None:
            raise HTTPException(404, detail={"message": "That MCP server isn't set up here."})
        try:
            tools = await asyncio.wait_for(
                mcp_module.list_tools(server, settings["allowed_commands"]), 30
            )
        except mcp_module.McpNotAllowed as exc:
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
