"""Settings → Model gateway and spending (Phase 5.5, docs/gateway.md).

Editors see the settings, the budgets with what each has spent, and what model calls cost
per flow, person, environment, model or run; Owners change the settings and forget cached
answers.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError

from ..environments import LABELS as ENVIRONMENT_LABELS
from .audit import note
from .gateway import GatewaySettings, budget_status, load, period_of, settings_key
from .spaces import space_of

PERIODS = ("today", "7d", "30d", "month")
GROUPS = ("flow", "person", "environment", "model", "run")
KINDS = {"test_run": "Test Runs", "test_set": "Tests tab"}


def since_of(period: str, now: float | None = None) -> float:
    now = now or time.time()
    if period == "today":
        return period_of("day", now)[0]
    if period == "month":
        return period_of("month", now)[0]
    return now - (7 if period == "7d" else 30) * 86400


def label_of(row: dict[str, Any], by: str) -> str:
    if by == "person":
        return row.get("user_name") or "Triggers and schedules"
    if by == "environment":
        return ENVIRONMENT_LABELS.get(row.get("environment") or "", "Drafts")
    if by == "model":
        provider, model = row.get("provider"), row.get("model") or "?"
        return f"{provider}:{model}" if provider else model
    if row.get("kind") == "copilot":
        return "Copilot"
    name = row.get("flow_name") or row.get("flow_id") or "A flow"
    extra = KINDS.get(row.get("kind") or "")
    return f"{name} · {extra}" if extra else name


def _key(row: dict[str, Any], by: str) -> str | None:
    return {
        "flow": row.get("flow_id"),
        "person": row.get("user_id"),
        "environment": row.get("environment"),
        "model": row.get("model"),
        "run": row.get("run_id"),
    }[by]


def _totals(row: dict[str, Any] | None) -> dict[str, Any]:
    keys = ("calls", "cached", "errors", "unpriced", "input_tokens", "output_tokens", "cost")
    return {k: (row or {}).get(k) or 0 for k in keys}


def add_gateway_routes(app: FastAPI) -> None:
    def db() -> Any:
        return app.state.hub.db

    @app.get("/api/gateway")
    async def get_gateway(request: Request) -> dict[str, Any]:
        """The settings, each budget with what it has spent, and the latest alerts."""
        space = space_of(request)
        saved = await load(db(), space)
        return {
            "settings": saved.model_dump(),
            "budgets": await budget_status(db(), space, saved),
            "alerts": await db().alerts(space.id),
        }

    @app.put("/api/gateway")
    async def save_gateway(request: Request) -> dict[str, Any]:
        space = space_of(request)
        try:
            saved = GatewaySettings.model_validate(await request.json())
        except ValidationError as exc:
            problems = [
                str(e.get("ctx", {}).get("error") or e["msg"]).removeprefix("Value error, ")
                for e in exc.errors()
            ]
            raise HTTPException(
                422,
                detail={"message": "These settings can't be saved yet.", "problems": problems},
            ) from None
        except ValueError:
            raise HTTPException(422, detail={"message": "Send the settings as JSON."}) from None
        await db().set_setting(settings_key(space), saved.model_dump())
        note(
            request,
            "model gateway",
            budgets=sorted(saved.budgets),
            caching=saved.cache.scopes,
            fallbacks=len(saved.fallbacks),
        )
        return {
            "settings": saved.model_dump(),
            "budgets": await budget_status(db(), space, saved),
            "alerts": await db().alerts(space.id),
        }

    @app.delete("/api/gateway/cache")
    async def forget_answers(request: Request) -> dict[str, Any]:
        forgotten = await db().clear_cache(space_of(request).id)
        note(request, "cached answers", forgotten=forgotten)
        return {"forgotten": forgotten}

    @app.get("/api/spending")
    async def spending(request: Request, period: str = "month", by: str = "flow") -> dict[str, Any]:
        """What model calls cost in the period, added up per ``by``, the costliest first."""
        if period not in PERIODS or by not in GROUPS:
            raise HTTPException(
                422,
                detail={"message": f"Use period {', '.join(PERIODS)} and by {', '.join(GROUPS)}."},
            )
        workspace = space_of(request).id
        since = since_of(period)
        rows = await db().spending(workspace, since=since, by=by)
        total = await db().spending(workspace, since=since, by="total")
        return {
            "period": period,
            "by": by,
            "since": since,
            "total": _totals(total[0] if total else None),
            "rows": [
                {
                    "key": _key(row, by),
                    "label": label_of(row, by),
                    **_totals(row),
                    "first": row["first"],
                    "last": row["last"],
                    **(
                        {
                            "kind": row["kind"],
                            "flow_id": row["flow_id"],
                            "environment": row["environment"],
                            "by": row["user_name"],
                        }
                        if by == "run"
                        else {}
                    ),
                }
                for row in rows
            ],
        }
