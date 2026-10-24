"""The dashboard and Settings → Tracing (Phase 5.7, docs/dashboard.md, docs/tracing.md).

Viewers see the dashboard (runs, errors, latency, tokens and cost over time); Editors see the
tracing settings; Owners change them and send a test span.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from ..environments import ENVIRONMENTS
from .audit import note
from .dashboard import WINDOWS, summarise, window
from .spaces import space_of
from .tracing import TracingSettings, send_test, shown, to_save


def _problems(exc: ValidationError) -> list[str]:
    return [str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()]


def add_observe_routes(app: FastAPI) -> None:
    def db() -> Any:
        return app.state.hub.db

    @app.get("/api/dashboard")
    async def dashboard(
        request: Request,
        days: int = 30,
        flow_id: str | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """Runs, errors, latency, tokens and cost per day (UTC) for the workspace, a flow or an
        environment ("drafts": runs of flows that aren't published)."""
        if days not in WINDOWS:
            raise HTTPException(
                422, detail={"message": f"Show {', '.join(map(str, WINDOWS))} days."}
            )
        if environment and environment not in ("drafts", *ENVIRONMENTS):
            raise HTTPException(
                422,
                detail={"message": f"Pick drafts or an environment: {', '.join(ENVIRONMENTS)}."},
            )
        now = time.time()
        since, _ = window(days, now)
        runs, calls = await db().dashboard_rows(
            space_of(request).id, since=since, flow_id=flow_id or None, environment=environment
        )
        found = summarise(runs, calls, days=days, now=now)
        return {**found, "flow_id": flow_id or None, "environment": environment or None}

    async def _saved(request: Request) -> TracingSettings:
        raw = await db().get_setting(space_of(request).setting("tracing"), {}) or {}
        try:
            return TracingSettings.model_validate(raw)
        except ValidationError:
            return TracingSettings()

    def _view(request: Request, settings: TracingSettings) -> dict[str, Any]:
        # The workspace's secrets are laid over os.environ for the request.
        return {**shown(settings), "langsmith_key": bool(os.environ.get("LANGSMITH_API_KEY"))}

    @app.get("/api/settings/tracing")
    async def get_tracing(request: Request) -> dict[str, Any]:
        return _view(request, await _saved(request))

    @app.put("/api/settings/tracing")
    async def save_tracing(request: Request) -> dict[str, Any]:
        try:
            sent = TracingSettings.model_validate(await request.json())
            settings = to_save(sent, await _saved(request))
        except ValidationError as exc:
            raise HTTPException(
                422,
                detail={
                    "message": "These settings can't be saved yet.",
                    "problems": _problems(exc),
                },
            ) from None
        except ValueError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from None
        await db().set_setting(space_of(request).setting("tracing"), settings.model_dump())
        note(
            request,
            "tracing",
            opentelemetry=settings.otlp.enabled,
            langsmith=settings.langsmith.enabled,
        )
        return _view(request, settings)

    @app.post("/api/settings/tracing/test")
    async def test_tracing(request: Request) -> dict[str, Any]:
        """Send one span to the saved collector and say whether it took it."""
        settings = await _saved(request)
        if not settings.otlp.endpoint:
            raise HTTPException(422, detail={"message": "Save the collector's address first."})
        return await asyncio.to_thread(send_test, settings.otlp)
