"""Test Sets: run a flow over example cases and apply their checks (checks.py has every check;
docs/testing.md describes them). A Test Set sits next to its flow, as
``<flow id>.tests.yaml``. File format::

    flow: summarise-url.flow.yaml        # relative to this file
    cases:
      - name: Summarises the LangChain page
        inputs: {url: "${base_url}/pages/langchain"}
        expect:
          status: ok
          output.summary:
            contains: LangChain
            not_empty: true
      - name: A person approves the draft
        inputs: {email: "Where is my order?"}
        answers:                         # given in order to Ask a Human steps
          - {action: approve, comment: "Fine"}
        expect:
          output.decision: Approved
      - name: A follow-up question in the same chat
        turns:                           # several messages in one conversation
          - {messages: [{role: user, content: "How do I reset my password?"}]}
          - {messages: [{role: user, content: "And how long does the link work?"}]}
        expect:
          output.messages.-1.content: {contains: "one hour"}
      - name: The analyst runs a query
        inputs: {question: "How many customers are in Germany?"}
        script:                          # the stand-in AI's turns (ignored by real models)
          - {call: run_query, args: {sql: "SELECT COUNT(*) FROM customers WHERE country = 'Germany'"}}
          - {answer: {answer: "There are 3 customers in Germany."}}
        expect:
          tools: [run_query]             # tools the agent called
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .checks import (
    RUN_CHECKS,
    VALUE_RULES,
    Check,
    RunFacts,
    check_value,
    evaluate,
    judge,
    lookup,
)
from .runtime import RunOptions, run_flow
from .runtime.standin import Script
from .spec import FlowSpec, load_spec

# Kept for older imports: the checks themselves are in checks.py.
CHECKS = VALUE_RULES
check_value = check_value
_lookup = lookup


@dataclass
class CaseResult:
    name: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    output: Any = None
    # Every check, passed, failed or skipped (checks.Check), and the run's time, tokens, cost
    # and tool calls.
    checks: list[Check] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def skipped(self) -> list[str]:
        return [f"{c.name}: {c.message}" for c in self.checks if c.passed is None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "failures": self.failures,
            "skipped": self.skipped,
            "output": self.output,
            "checks": [c.to_dict() for c in self.checks],
            "metrics": self.metrics,
        }


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


async def run_case(
    spec: FlowSpec,
    case: dict[str, Any],
    stand_in: bool,
    sandbox: Any = None,
    *,
    judge_model: str | None = None,
    options: dict[str, Any] | None = None,
) -> CaseResult:
    """Run one case and apply its checks. ``options``: more RunOptions (MCP servers, the egress
    policy, Sub-flows) for a run made by the server."""
    name = case.get("name") or "case"
    run_options = RunOptions(
        stand_in=stand_in,
        thread_id=case.get("thread") or uuid.uuid4().hex,
        sandbox=sandbox,
        **(options or {}),
    )
    if stand_in and case.get("script"):
        # What the stand-in AI says, turn by turn (a real model ignores this).
        run_options.script = Script(case["script"])
    # Answers for Ask a Human steps, in the order the run asks.
    answers = list(case.get("answers") or [])
    # A chat case can have several turns in one conversation; checks apply to the last.
    turns = case.get("turns") or [case.get("inputs") or {}]
    events: list[dict[str, Any]] = []
    final: dict[str, Any] = {}
    for turn in turns:
        run_options.action, run_options.resume = "start", None
        final, more = await run_flow(spec, turn, run_options)
        events += more
        while final.get("status") == "paused" and final.get("reason") == "ask_human" and answers:
            waiting = final.get("interrupts") or []
            answer = answers.pop(0)
            resume = {waiting[0]["id"]: answer} if len(waiting) == 1 else answer
            run_options.action, run_options.resume = "resume", resume
            final, more = await run_flow(spec, None, run_options)
            events += more
    facts = RunFacts(final, events, turns[-1] if len(turns) == 1 else turns)
    expect = dict(case.get("expect") or {})
    checks = evaluate(expect, facts)
    if answers:
        # Each answer stands for a pause the case expects; one left over means the run never
        # asked for it (it didn't pause, or paused fewer times).
        checks.append(
            Check(
                "answers",
                False,
                f"{len(answers)} answer(s) weren't used; the run didn't ask for them",
            )
        )
    if expect.get("judge"):
        checks.append(await judge(expect["judge"], facts, judge_model, stand_in=stand_in))
    failures = [f"{c.name}: {c.message}" for c in checks if c.passed is False]
    return CaseResult(name, not failures, failures, final.get("output"), checks, facts.metrics())


async def run_test_set(
    path: str | Path,
    variables: dict[str, str] | None = None,
    stand_in: bool | None = None,
    sandbox: Any = None,
) -> list[CaseResult]:
    """Run every case. ``stand_in`` True or False overrides the file's ``stand_in:``; None (the
    default) follows the file. ``sandbox`` is the Code step sandbox (None: EASYCHAIN_SANDBOX)."""
    from .templates.samples import ensure_samples

    ensure_samples()
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = _substitute(data, variables or {})
    spec = load_spec(path.parent / data["flow"])
    if any(
        st.type == "knowledge_search" and st.settings.knowledge_base == "help_centre"
        for st in spec.steps
    ):
        from .templates.samples import ensure_sample_knowledge

        ensure_sample_knowledge()
    if stand_in is None:
        stand_in = bool(data.get("stand_in"))
    model = data.get("judge_model")
    return [
        await run_case(spec, case, stand_in, sandbox, judge_model=model)
        for case in data.get("cases", [])
    ]


# What a case may hold (anything else is a mistake worth saying).
CASE_KEYS = ("name", "inputs", "turns", "answers", "script", "expect", "thread", "notes")


def read_test_set(path: Path, flow_file: str) -> dict[str, Any]:
    """A Test Set file as data (an empty one when there's no file yet)."""
    if not path.exists():
        return {"flow": flow_file, "cases": []}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} isn't a Test Set: it should be a mapping with cases.")
    data.setdefault("flow", flow_file)
    data["cases"] = [c for c in data.get("cases") or [] if isinstance(c, dict)]
    return data


def write_test_set(path: Path, data: dict[str, Any]) -> None:
    """Save a Test Set (YAML, cases in order; comments in a hand-written file aren't kept)."""
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n"
    )


def case_problems(cases: list[Any]) -> list[str]:
    """What's wrong with cases before they are saved: each says which case and what."""
    problems: list[str] = []
    seen: set[str] = set()
    for n, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            problems.append(f"Case {n} isn't a mapping.")
            continue
        name = str(case.get("name") or "").strip()
        label = f"“{name}”" if name else f"Case {n}"
        if not name:
            problems.append(f"{label} needs a name.")
        elif name in seen:
            problems.append(f"Two cases are called {label}; give each its own name.")
        seen.add(name)
        unknown = sorted(set(case) - set(CASE_KEYS))
        if unknown:
            problems.append(f"{label}: unknown part {', '.join(unknown)}.")
        for key, kind in (("inputs", dict), ("turns", list), ("answers", list), ("script", list)):
            if key in case and case[key] is not None and not isinstance(case[key], kind):
                what = "a mapping of field: value" if kind is dict else "a list"
                problems.append(f"{label}: {key} should be {what}.")
        expect = case.get("expect")
        if expect is None:
            continue
        if not isinstance(expect, dict):
            problems.append(f"{label}: expect should map what to check to what it should be.")
            continue
        for key, rule in expect.items():
            if key in RUN_CHECKS or key == "status":
                continue
            rules = rule if isinstance(rule, dict) else {"equals": rule}
            bad = sorted(set(rules) - set(VALUE_RULES))
            if bad:
                problems.append(
                    f"{label}: {key} has an unknown check {', '.join(bad)} (use one of: "
                    f"{', '.join(VALUE_RULES)})."
                )
    return problems


def add_cases(path: Path, flow_file: str, cases: list[dict[str, Any]]) -> dict[str, int]:
    """Add cases to a Test Set file (made when there is none). A name already taken gets a
    number, so no case replaces another."""
    data: dict[str, Any] = {"flow": flow_file, "cases": []}
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or data
    existing = data.setdefault("cases", [])
    names = {c.get("name") for c in existing if isinstance(c, dict)}
    for case in cases:
        name, n = str(case.get("name") or "Case"), 2
        unique = name
        while unique in names:
            unique, n = f"{name} ({n})", n + 1
        names.add(unique)
        existing.append({**case, "name": unique})
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n"
    )
    return {"added": len(cases), "total": len(existing)}
