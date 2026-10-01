"""Minimal Test Sets: run a flow over example inputs and apply pass/fail checks.

This is the Phase 1 groundwork for Test Sets + Checks (Phase 5 adds LLM-as-judge,
trajectory checks, Test Runs and baselines). File format::

    flow: summarise-url.flow.yaml        # relative to this file
    cases:
      - name: Summarises the LangChain page
        inputs: {url: "${base_url}/pages/langchain"}
        expect:
          status: ok
          output.summary:
            contains: LangChain
            not_empty: true
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .runtime import RunOptions, run_flow
from .spec import FlowSpec, load_spec

CHECKS = ("equals", "contains", "not_contains", "matches", "not_empty", "max_length", "min_length")


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    output: Any = None


def _substitute(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
            lambda m: variables.get(m.group(1), os.environ.get(m.group(1), m.group(0))),
            value,
        )
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    return value


def _lookup(event: dict[str, Any], path: str) -> Any:
    node: Any = event
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.lstrip("-").isdigit():
            idx = int(part)
            node = node[idx] if -len(node) <= idx < len(node) else None
        else:
            return None
    return node


def check_value(value: Any, rules: Any) -> list[str]:
    if not isinstance(rules, dict):
        rules = {"equals": rules}
    failures = []
    text = value if isinstance(value, str) else ("" if value is None else str(value))
    for rule, expected in rules.items():
        if rule == "equals" and value != expected:
            failures.append(f"expected {expected!r}, got {value!r}")
        elif rule == "contains":
            for item in expected if isinstance(expected, list) else [expected]:
                if str(item).lower() not in text.lower():
                    failures.append(f"expected to contain {item!r}")
        elif rule == "not_contains":
            for item in expected if isinstance(expected, list) else [expected]:
                if str(item).lower() in text.lower():
                    failures.append(f"expected not to contain {item!r}")
        elif rule == "matches" and not re.search(str(expected), text, re.S):
            failures.append(f"expected to match /{expected}/")
        elif rule == "not_empty" and expected and not value:
            failures.append("expected a value, got nothing")
        elif rule == "max_length" and len(text) > int(expected):
            failures.append(f"expected at most {expected} characters, got {len(text)}")
        elif rule == "min_length" and len(text) < int(expected):
            failures.append(f"expected at least {expected} characters, got {len(text)}")
        elif rule not in CHECKS:
            failures.append(f"unknown check {rule!r}")
    return failures


async def run_case(spec: FlowSpec, case: dict[str, Any], stand_in: bool) -> CaseResult:
    name = case.get("name") or "case"
    options = RunOptions(stand_in=stand_in, thread_id=case.get("thread"))
    final, events = await run_flow(spec, case.get("inputs") or {}, options)
    failures: list[str] = []
    expect = dict(case.get("expect") or {})
    expected_status = expect.pop("status", "ok")
    if final.get("status") != expected_status:
        message = (final.get("error") or {}).get("message", "")
        failures.append(
            f"status: expected {expected_status}, got {final.get('status')} {message}".strip()
        )
    if "steps" in expect:
        ran = [e["step"] for e in events if e["type"] == "step_finished"]
        for step in expect.pop("steps"):
            if step not in ran:
                failures.append(f"steps: expected {step!r} to run")
    if "route" in expect:
        routes = {e["step"]: e["exit"] for e in events if e["type"] == "route"}
        for step, label in expect.pop("route").items():
            if routes.get(step) != label:
                failures.append(f"route {step}: expected {label!r}, got {routes.get(step)!r}")
    for path, rules in expect.items():
        failures += [f"{path}: {msg}" for msg in check_value(_lookup(final, path), rules)]
    return CaseResult(name, not failures, failures, final.get("output"))


async def run_test_set(
    path: str | Path, variables: dict[str, str] | None = None, stand_in: bool = False
) -> list[CaseResult]:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _substitute(data, variables or {})
    spec = load_spec(path.parent / data["flow"])
    stand_in = stand_in or bool(data.get("stand_in"))
    return [await run_case(spec, case, stand_in) for case in data.get("cases", [])]
