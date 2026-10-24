"""The dashboard (Phase 5.7, docs/dashboard.md): runs, errors, latency, tokens and cost over
time, for a workspace, a flow or an environment.

Runs come from ``runs`` (when they started, how they ended, how long they took). Tokens and
cost come from ``model_calls`` (what the model gateway recorded for runs), so they match the
spending page. Days are UTC; every day of the window is in the series, empty or not.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

DAY = 86400
WINDOWS = (7, 30, 90)
FINISHED = ("ok", "error")


def day_start(ts: float) -> float:
    return math.floor(ts / DAY) * DAY


def window(days: int, now: float) -> tuple[float, list[float]]:
    """The start of the window and the start of each of its days (today last)."""
    today = day_start(now)
    starts = [today - DAY * (days - 1 - i) for i in range(days)]
    return starts[0], starts


def percentile(values: list[float], share: float) -> float | None:
    """The nearest-rank percentile (None for no values)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(share * len(ordered)))
    return ordered[rank - 1]


def _empty(start: float) -> dict[str, Any]:
    return {
        "day": datetime.fromtimestamp(start, UTC).strftime("%Y-%m-%d"),
        "start": start,
        "runs": 0,
        "ok": 0,
        "errors": 0,
        "paused": 0,
        "cancelled": 0,
        "running": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost": 0.0,
        "p50_ms": None,
        "p95_ms": None,
    }


def _add_run(into: dict[str, Any], run: dict[str, Any]) -> None:
    status = run.get("status")
    into["runs"] += 1
    if status == "ok":
        into["ok"] += 1
    elif status == "error":
        into["errors"] += 1
    elif status == "paused":
        into["paused"] += 1
    elif status == "cancelled":
        into["cancelled"] += 1
    else:
        into["running"] += 1


def summarise(
    runs: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    days: int,
    now: float,
) -> dict[str, Any]:
    """The totals, a series per day, each flow and the commonest errors."""
    since, starts = window(days, now)
    series = [_empty(s) for s in starts]
    durations: list[list[float]] = [[] for _ in starts]
    totals = _empty(since)
    all_durations: list[float] = []
    flows: dict[str, dict[str, Any]] = {}
    flow_durations: dict[str, list[float]] = {}
    errors: dict[tuple[str, str, str], dict[str, Any]] = {}

    for run in runs:
        at = run.get("created_at") or 0
        index = int((day_start(at) - since) // DAY)
        if not 0 <= index < days:
            continue
        _add_run(series[index], run)
        _add_run(totals, run)
        flow_key = run.get("flow_id") or "-"
        flow = flows.setdefault(
            flow_key,
            {
                "flow_id": run.get("flow_id"),
                "flow": run.get("flow_name") or "A flow given in full",
                **{k: 0 for k in ("runs", "ok", "errors", "paused", "cancelled", "running")},
                "input_tokens": 0,
                "output_tokens": 0,
                "cost": 0.0,
                "p95_ms": None,
            },
        )
        _add_run(flow, run)
        if run.get("status") in FINISHED and run.get("duration_ms") is not None:
            durations[index].append(run["duration_ms"])
            all_durations.append(run["duration_ms"])
            flow_durations.setdefault(flow_key, []).append(run["duration_ms"])
        if run.get("status") == "error":
            error = run.get("error") or {}
            message = str(error.get("message") or "The run failed.")[:200]
            key = (flow_key, str(error.get("kind") or "error"), message)
            found = errors.setdefault(
                key,
                {
                    "flow_id": run.get("flow_id"),
                    "flow": flow["flow"],
                    "kind": key[1],
                    "message": message,
                    "step": error.get("step"),
                    "count": 0,
                    "last_at": 0.0,
                    "last_run_id": None,
                },
            )
            found["count"] += 1
            if at >= found["last_at"]:
                found["last_at"] = at
                found["last_run_id"] = run.get("id")

    for call in calls:
        at = call.get("at") or 0
        index = int((day_start(at) - since) // DAY)
        if not 0 <= index < days:
            continue
        for into in (series[index], totals, flows.get(call.get("flow_id") or "-")):
            if into is None:
                continue
            into["input_tokens"] += call.get("input_tokens") or 0
            into["output_tokens"] += call.get("output_tokens") or 0
            into["cost"] += call.get("cost") or 0.0

    for day, found in zip(series, durations, strict=True):
        day["p50_ms"] = percentile(found, 0.5)
        day["p95_ms"] = percentile(found, 0.95)
    totals["p50_ms"] = percentile(all_durations, 0.5)
    totals["p95_ms"] = percentile(all_durations, 0.95)
    finished = totals["ok"] + totals["errors"]
    totals["error_rate"] = totals["errors"] / finished if finished else None
    for key, flow in flows.items():
        flow["p95_ms"] = percentile(flow_durations.get(key, []), 0.95)
        done = flow["ok"] + flow["errors"]
        flow["error_rate"] = flow["errors"] / done if done else None
    for name in ("day", "start"):
        totals.pop(name)
    return {
        "days": days,
        "since": since,
        "totals": totals,
        "series": series,
        "flows": sorted(flows.values(), key=lambda f: (-f["runs"], f["flow"])),
        "errors": sorted(errors.values(), key=lambda e: (-e["count"], -e["last_at"]))[:10],
    }
