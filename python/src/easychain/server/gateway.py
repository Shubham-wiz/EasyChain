"""Settings → Model gateway on a server (Phase 5.5, docs/gateway.md).

A workspace's gateway settings (``gateway``, per workspace) say:

- **budgets**: how much the model calls of the whole workspace, of drafts, or of one
  environment may cost a day or a month; an alert goes out at a share of it (80% by default)
  and when it's used up, and a budget that **stops** blocks new model calls until the next
  day or month;
- **caching**: where answers are cached (drafts and environments) and for how long;
- **fallbacks** per model, **retries**, **smoothing** (requests a second per provider) and
  **prices** for models Easy Chain has no price for (or another price).

For each run (and Test Run, Test Set run in the app, copilot request) the server makes a
``GatewayPolicy`` (runtime/model_policy.py) with a ``RunMeter``: before each model call it
adds up what the budgets' scopes have spent (every process records into ``model_calls``, so
the budgets hold across workers), and after it records the call. Model calls run in worker
threads; the meter hands its database work to the event loop that made it.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..environments import ENVIRONMENTS
from ..environments import LABELS as ENVIRONMENT_LABELS
from ..providers import PROVIDERS
from ..runtime.model_policy import AnswerCache, BudgetReached, GatewayPolicy
from . import notify

log = logging.getLogger("easychain.gateway")

SCOPES = ("workspace", "drafts", *ENVIRONMENTS)
SCOPE_LABELS = {"workspace": "Workspace", "drafts": "Drafts", **ENVIRONMENT_LABELS}
Scope = Literal["workspace", "drafts", "dev", "staging", "production"]
CacheScope = Literal["drafts", "dev", "staging", "production"]
MODEL = re.compile(r"^[a-z][a-z0-9_-]*:[^\s]+$")


class Budget(BaseModel):
    # USD a period
    limit: float = Field(gt=0)
    period: Literal["day", "month"] = "month"
    # An alert at this share of the limit as well as when it's used up (0: only then).
    alert_at: float = Field(0.8, ge=0, lt=1)
    # When it's used up, stop new model calls (else only alert).
    stop: bool = True


class Caching(BaseModel):
    scopes: list[CacheScope] = Field(default_factory=list)
    hours: float = Field(24, gt=0, le=24 * 30)


class Price(BaseModel):
    # USD per million tokens
    input: float = Field(ge=0)
    output: float = Field(ge=0)


class GatewaySettings(BaseModel):
    budgets: dict[Scope, Budget] = Field(default_factory=dict)
    cache: Caching = Field(default_factory=Caching)
    fallbacks: dict[str, list[str]] = Field(default_factory=dict)
    retries: int | None = Field(None, ge=0, le=10)
    per_second: dict[str, float] = Field(default_factory=dict)
    prices: dict[str, Price] = Field(default_factory=dict)

    @field_validator("fallbacks")
    @classmethod
    def _fallbacks(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for model, others in value.items():
            others = [o.strip() for o in others if o.strip()]
            for name in (model, *others):
                if not MODEL.match(name):
                    raise ValueError(f"“{name}” isn't a model as provider:model.")
            if model in others:
                raise ValueError(f"“{model}” can't be its own fallback.")
            if others:
                out[model] = others
        return out

    @field_validator("per_second")
    @classmethod
    def _per_second(cls, value: dict[str, float]) -> dict[str, float]:
        for provider, rate in value.items():
            if provider not in PROVIDERS:
                raise ValueError(f"There's no provider “{provider}”.")
            if not 0 < rate <= 1000:
                raise ValueError(f"{provider}: give between 0 and 1000 requests a second.")
        return value

    @field_validator("prices")
    @classmethod
    def _prices(cls, value: dict[str, Price]) -> dict[str, Price]:
        for model in value:
            if not MODEL.match(model):
                raise ValueError(f"“{model}” isn't a model as provider:model.")
        return value


def settings_key(space: Any) -> str:
    return space.setting("gateway")


async def load(db: Any, space: Any) -> GatewaySettings:
    try:
        return GatewaySettings.model_validate(await db.get_setting(settings_key(space), {}) or {})
    except ValueError:
        log.warning("The saved model gateway settings don't fit; using none of them.")
        return GatewaySettings()


# ── budgets ─────────────────────────────────────────────────────────────────


def period_of(period: str, now: float | None = None) -> tuple[float, float, str]:
    """The period (UTC) holding ``now``: its start, its end and its name."""
    t = datetime.fromtimestamp(now if now is not None else time.time(), UTC)
    if period == "day":
        start = t.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp(), (start + timedelta(days=1)).timestamp(), f"{start:%Y-%m-%d}"
    start = t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start.timestamp(), end.timestamp(), f"{start:%Y-%m}"


def money(amount: float) -> str:
    return f"${amount:,.2f}" if amount >= 0.01 or amount == 0 else f"${amount:.4f}"


def budget_name(scope: str) -> str:
    return "The workspace's budget" if scope == "workspace" else f"The {SCOPE_LABELS[scope]} budget"


def used_up(scope: str, budget: dict[str, Any], spent: float, end: float) -> str:
    when = "today" if budget["period"] == "day" else "this month"
    until = datetime.fromtimestamp(end, UTC)
    again = "tomorrow" if budget["period"] == "day" else f"{until.day} {until:%B}"
    return (
        f"{budget_name(scope)} is used up: {money(spent)} of {money(budget['limit'])} {when}. "
        f"Model calls stop until {again} (UTC)."
    )


def applies(scope: str, environment: str | None) -> bool:
    """Whether a budget of ``scope`` counts the model calls of a run in ``environment``."""
    return scope == "workspace" or scope == (environment or "drafts")


def _on_loop(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, Any], default: Any) -> Any:
    """Run ``coro`` on ``loop`` from another thread and wait for it. On the loop's own thread,
    which can't wait for itself, it runs later and ``default`` comes back."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is loop:
        _later(loop.create_task(coro))
        return default
    try:
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
    except Exception:
        log.exception("The model gateway couldn't reach the run database")
        return default


_pending: set[asyncio.Task[Any]] = set()


def _later(task: asyncio.Task[Any]) -> None:
    _pending.add(task)
    task.add_done_callback(_pending.discard)


class RunMeter:
    """Checks the budgets of one run before each model call and records each call."""

    def __init__(
        self,
        db: Any,
        loop: asyncio.AbstractEventLoop,
        *,
        workspace: str,
        kind: str,
        budgets: dict[str, dict[str, Any]],
        run_id: str | None = None,
        flow_id: str | None = None,
        flow_name: str | None = None,
        environment: str | None = None,
        by: tuple[str | None, str] | None = None,
        alert: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ):
        self.db = db
        self.loop = loop
        self.workspace = workspace
        self.kind = kind
        self.budgets = {s: b for s, b in budgets.items() if applies(s, environment)}
        self.row = {
            "workspace_id": workspace,
            "kind": kind,
            "run_id": run_id,
            "flow_id": flow_id,
            "flow_name": flow_name,
            "environment": environment,
            "user_id": by[0] if by else None,
            "user_name": by[1] if by else None,
        }
        self.alert = alert

    def check(self) -> None:
        for scope, budget in self.budgets.items():
            if not budget["stop"]:
                continue
            start, end, _ = period_of(budget["period"])
            spent = _on_loop(self.loop, self.db.spent(self.workspace, scope, start), None)
            if spent is not None and spent >= budget["limit"]:
                raise BudgetReached(used_up(scope, budget, spent, end), scope)

    def record(self, call: dict[str, Any]) -> None:
        _on_loop(self.loop, self._record({**self.row, **call, "at": time.time()}), None)

    async def _record(self, row: dict[str, Any]) -> None:
        await self.db.add_model_call(row)
        if not row.get("cost"):
            return
        for scope, budget in self.budgets.items():
            start, end, period = period_of(budget["period"])
            spent = await self.db.spent(self.workspace, scope, start)
            limit = budget["limit"]
            for share in sorted({budget["alert_at"], 1.0}):
                if share <= 0 or spent < share * limit:
                    continue
                level = round(share * 100)
                if await self.db.mark_alert(self.workspace, scope, period, level, spent, limit):
                    alert = {
                        "budget": scope,
                        "level": level,
                        "spent": spent,
                        "limit": limit,
                        "period": budget["period"],
                        "stops": budget["stop"],
                        "text": alert_text(scope, budget, spent, level),
                    }
                    if self.alert is not None:
                        # Sending can take a while; the model call doesn't wait for it.
                        _later(asyncio.get_running_loop().create_task(self.alert(alert)))


def alert_text(scope: str, budget: dict[str, Any], spent: float, level: int) -> str:
    when = "today" if budget["period"] == "day" else "this month"
    if level >= 100:
        then = " New model calls stop until it renews." if budget["stop"] else ""
        return (
            f"{budget_name(scope)} is used up: {money(spent)} of {money(budget['limit'])} "
            f"{when}.{then}"
        )
    return (
        f"{budget_name(scope)} is {level}% used: {money(spent)} of {money(budget['limit'])} {when}."
    )


# ── caching ─────────────────────────────────────────────────────────────────


class DbCacheStore:
    """Cached answers in the run database, shared by every process."""

    def __init__(self, db: Any, loop: asyncio.AbstractEventLoop, workspace: str):
        self.db = db
        self.loop = loop
        self.workspace = workspace

    def get(self, key: str) -> str | None:
        return _on_loop(self.loop, self.db.cache_get(key), None)

    def put(self, key: str, value: str, expires_at: float) -> None:
        _on_loop(self.loop, self.db.cache_put(key, self.workspace, value, expires_at), None)


# ── a run's policy ──────────────────────────────────────────────────────────


async def policy_for(
    hub: Any,
    *,
    kind: str,
    run_id: str | None = None,
    flow_id: str | None = None,
    flow_name: str | None = None,
    environment: str | None = None,
    by: tuple[str | None, str] | None = None,
) -> GatewayPolicy:
    """The gateway policy of a run (``kind``: run, test_run, test_set or copilot) in the
    workspace of the work in hand."""
    db, space = hub.db, hub.space
    saved = await load(db, space)
    loop = asyncio.get_running_loop()
    notifications = space.setting("notifications")

    async def alert(found: dict[str, Any]) -> None:
        config = await db.get_setting(notifications, {})
        await notify.send_alert(config, {**found, "workspace": space.id})

    meter = RunMeter(
        db,
        loop,
        workspace=space.id,
        kind=kind,
        budgets={s: b.model_dump() for s, b in saved.budgets.items()},
        run_id=run_id,
        flow_id=flow_id,
        flow_name=flow_name,
        environment=environment,
        by=by,
        alert=alert,
    )
    cache = None
    if (environment or "drafts") in saved.cache.scopes and kind != "copilot":
        store = DbCacheStore(db, loop, space.id)
        cache = AnswerCache(store, saved.cache.hours * 3600, scope=space.id)
    return GatewayPolicy(
        fallbacks=saved.fallbacks,
        retries=saved.retries,
        per_second=saved.per_second,
        cache=cache,
        prices={m: (p.input, p.output) for m, p in saved.prices.items()},
        meter=meter,
    )


async def budget_status(db: Any, space: Any, saved: GatewaySettings) -> list[dict[str, Any]]:
    """Each scope with its budget (if any) and what it has spent in the period."""
    out = []
    for scope in SCOPES:
        budget = saved.budgets.get(scope)  # type: ignore[call-overload]
        period = budget.period if budget else "month"
        start, end, name = period_of(period)
        spent = await db.spent(space.id, scope, start)
        out.append(
            {
                "scope": scope,
                "label": SCOPE_LABELS[scope],
                "budget": budget.model_dump() if budget else None,
                "period": name,
                "since": start,
                "until": end,
                "spent": spent,
                "share": (spent / budget.limit) if budget else None,
            }
        )
    return out
