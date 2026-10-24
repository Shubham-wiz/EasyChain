"""Test Runs and baselines (Phase 5.2, docs/testing.md): a Test Set run against one flow
version, kept with its results, and compared with the flow's baseline.

A Test Run can run each case several times (``repeat``), because real models don't answer
the same way twice. Each case then has a **pass rate**; a case that passed some times and
failed others is **flaky**.

Comparing with a baseline, each case is:

- ``better``: it passes more often than in the baseline;
- ``worse``: it passes less often (a **regression**);
- ``same``;
- ``new``: the baseline didn't have it; or ``gone``: only the baseline has it.

Time, tokens and cost are compared too, as the change in each case's average.

The same summaries are what ``easychain eval`` keeps in a baseline file (5.3).
"""

from __future__ import annotations

from typing import Any

FORMAT = "easychain-test-run/1"


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def case_summary(name: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    """One case over its repeats: ``runs`` are ``CaseResult.to_dict()``."""
    passed = sum(1 for r in runs if r.get("passed"))
    costs = [(r.get("metrics") or {}).get("cost") for r in runs]
    known = [c for c in costs if c is not None]
    rate = passed / len(runs) if runs else 0.0
    failures: list[str] = []
    for r in runs:
        for failure in r.get("failures") or []:
            if failure not in failures:
                failures.append(failure)
    return {
        "name": name,
        "runs": len(runs),
        "passed": passed,
        "pass_rate": round(rate, 4),
        "flaky": 0 < passed < len(runs),
        "failures": failures,
        "skipped": sorted({s for r in runs for s in r.get("skipped") or []}),
        "seconds": round(_mean([(r.get("metrics") or {}).get("seconds") or 0 for r in runs]), 3),
        "tokens": round(_mean([(r.get("metrics") or {}).get("tokens") or 0 for r in runs]), 1),
        "cost": round(_mean(known), 6) if known and len(known) == len(costs) else None,
    }


def summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole run, from its case summaries."""
    costs = [c["cost"] for c in cases]
    return {
        "total": len(cases),
        "passed": sum(1 for c in cases if c["passed"] == c["runs"] and c["runs"]),
        "failed": sum(1 for c in cases if c["passed"] < c["runs"]),
        "flaky": sum(1 for c in cases if c["flaky"]),
        "skipped": sum(1 for c in cases if c["skipped"]),
        "seconds": round(sum(c["seconds"] for c in cases), 3),
        "tokens": round(sum(c["tokens"] for c in cases), 1),
        "cost": None if any(c is None for c in costs) else round(sum(costs), 6),
    }


def _delta(after: float | None, before: float | None) -> float | None:
    if after is None or before is None:
        return None
    return round(after - before, 6)


def compare(current: list[dict[str, Any]], baseline: list[dict[str, Any]]) -> dict[str, Any]:
    """How each case did against the baseline (both are lists of case summaries)."""
    before = {c["name"]: c for c in baseline}
    rows: list[dict[str, Any]] = []
    for case in current:
        old = before.pop(case["name"], None)
        if old is None:
            rows.append({"name": case["name"], "change": "new", "after": case["pass_rate"]})
            continue
        if case["pass_rate"] > old["pass_rate"]:
            change = "better"
        elif case["pass_rate"] < old["pass_rate"]:
            change = "worse"
        else:
            change = "same"
        rows.append(
            {
                "name": case["name"],
                "change": change,
                "before": old["pass_rate"],
                "after": case["pass_rate"],
                "seconds": _delta(case["seconds"], old["seconds"]),
                "tokens": _delta(case["tokens"], old["tokens"]),
                "cost": _delta(case["cost"], old["cost"]),
            }
        )
    rows += [
        {"name": name, "change": "gone", "before": c["pass_rate"]} for name, c in before.items()
    ]
    counts = {k: sum(1 for r in rows if r["change"] == k) for k in ("better", "worse", "same")}
    return {
        "cases": rows,
        **counts,
        "new": sum(1 for r in rows if r["change"] == "new"),
        "gone": sum(1 for r in rows if r["change"] == "gone"),
        "regressions": [r["name"] for r in rows if r["change"] == "worse"],
    }


def describe_change(row: dict[str, Any]) -> str:
    """One line for a case's change: "Refund times: worse (100% → 50%), +1.2 s, +$0.0010"."""
    if row["change"] == "new":
        return f"{row['name']}: new ({row['after']:.0%})"
    if row["change"] == "gone":
        return f"{row['name']}: not in this run (was {row['before']:.0%})"
    parts = [f"{row['name']}: {row['change']} ({row['before']:.0%} → {row['after']:.0%})"]
    if row.get("seconds"):
        parts.append(f"{row['seconds']:+.1f} s")
    if row.get("cost"):
        parts.append(f"{row['cost']:+.4f} $")
    return ", ".join(parts)
