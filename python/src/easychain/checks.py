"""Checks: what a Test Set case expects of a run (Phase 5.1, docs/testing.md).

A case's ``expect:`` maps what to look at to what it should be::

    expect:
      status: ok                          # the default
      output.answer:                      # any value of the run's result, by path
        contains: [refund, 14 days]
        max_length: 600
      output.order: {schema: {type: object, required: [id]}}
      steps: [lookup]                     # these steps ran
      route: {triage: Billing}            # the exit a Decision took
      tools: [find_order]                 # the agent called these
      not_tools: [refund]                 # … and not these
      tool_calls: {max: 4}                # how many tool calls
      trajectory: [find_order, reply]     # the tool calls, in this order
      max_seconds: 20                     # budgets, over every part of the case's run
      max_cost: 0.02                      # dollars
      max_tokens: 4000
      judge:                              # an AI model grades the answer against a rubric
        rubric: Polite, says when the refund arrives, and promises nothing else.
        pass_score: 0.7

Each check gives a ``Check``: passed, failed (with a message that says what was wanted and
what came), or skipped (with why: an AI judge with no model key, a cost the model's price
list doesn't give). Skipped checks don't fail a case, and they're never faked (decision 5.4).
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

# Rules for one value (``output.answer: {contains: …}``).
VALUE_RULES = (
    "equals",
    "contains",
    "not_contains",
    "matches",
    "not_empty",
    "max_length",
    "min_length",
    "count",
    "schema",
    "at_least",
    "at_most",
    "one_of",
)

# What a case can expect besides values (paths into the result).
RUN_CHECKS = (
    "status",
    "steps",
    "route",
    "tools",
    "not_tools",
    "tool_calls",
    "trajectory",
    "helpers",
    "files",
    "max_seconds",
    "max_cost",
    "max_tokens",
    "judge",
)

TRAJECTORY_ORDERS = ("in_order", "exact", "any_order")

JUDGE_SCHEMA = {
    "title": "verdict",
    "description": "How well the output meets the rubric.",
    "type": "object",
    "properties": {
        "score": {
            "type": "number",
            "description": "From 0 (doesn't meet the rubric at all) to 1 (meets all of it).",
        },
        "reason": {"type": "string", "description": "One or two sentences on why."},
    },
    "required": ["score", "reason"],
}

JUDGE_PROMPT = """You grade what an AI app produced against a rubric. Read the input it was \
given and its output, then score how well the output meets every point of the rubric, from 0 \
(none of it) to 1 (all of it). Be strict: a point that is only partly met lowers the score. \
Answer with the score and a short reason."""


@dataclass
class Check:
    """One check of a case: ``passed`` True or False, or None when it was skipped."""

    name: str
    passed: bool | None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunFacts:
    """What the checks look at: the final event, every event, and the case's inputs."""

    final: dict[str, Any]
    events: list[dict[str, Any]]
    inputs: Any = None

    @property
    def tools(self) -> list[str]:
        return [e["tool"] for e in self.events if e.get("type") == "tool_started"]

    @property
    def parts(self) -> list[dict[str, Any]]:
        """The run_finished event of each part (a resume after Ask a Human is a new part)."""
        return [e for e in self.events if e.get("type") == "run_finished"] or [self.final]

    def metrics(self) -> dict[str, Any]:
        """Time, tokens and cost over every part; cost is None when a price isn't known."""
        seconds = sum(float(p.get("duration_ms") or 0) for p in self.parts) / 1000
        tokens = sum(
            int((p.get("usage") or {}).get("input_tokens") or 0)
            + int((p.get("usage") or {}).get("output_tokens") or 0)
            for p in self.parts
        )
        costs = [p.get("cost") for p in self.parts]
        cost = None if any(c is None for c in costs) else round(sum(costs), 6)
        return {"seconds": round(seconds, 3), "tokens": tokens, "cost": cost, "tools": self.tools}


def lookup(data: Any, path: str) -> Any:
    """A value by a dotted path: ``output.messages.-1.content``."""
    node: Any = data
    for part in path.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.lstrip("-").isdigit():
            idx = int(part)
            node = node[idx] if -len(node) <= idx < len(node) else None
        else:
            return None
    return node


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _schema_problems(value: Any, schema: Any) -> list[str]:
    import jsonschema

    if isinstance(value, str):
        # A model's JSON answer that came back as text still counts.
        with contextlib.suppress(ValueError):
            value = json.loads(value)
    try:
        validator = jsonschema.Draft202012Validator(schema)
    except jsonschema.SchemaError as exc:
        return [f"the schema isn't valid: {exc.message}"]
    problems = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.path))[:3]:
        where = ".".join(str(p) for p in error.path)
        problems.append(f"{where}: {error.message}" if where else error.message)
    return problems


def check_value(value: Any, rules: Any) -> list[str]:
    """What's wrong with ``value`` against its rules (a plain value means ``equals``)."""
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
        elif rule == "count" and (not isinstance(value, list) or len(value) != int(expected)):
            got = len(value) if isinstance(value, list) else "no list"
            failures.append(f"expected {expected} items, got {got}")
        elif rule == "schema":
            failures += [f"doesn't fit the schema: {p}" for p in _schema_problems(value, expected)]
        elif rule in ("at_least", "at_most"):
            number = _number(value)
            if number is None:
                failures.append(f"expected a number, got {value!r}")
            elif rule == "at_least" and number < float(expected):
                failures.append(f"expected at least {expected}, got {value!r}")
            elif rule == "at_most" and number > float(expected):
                failures.append(f"expected at most {expected}, got {value!r}")
        elif rule == "one_of" and value not in (expected or []):
            failures.append(f"expected one of {expected!r}, got {value!r}")
        elif rule not in VALUE_RULES:
            failures.append(f"unknown check {rule!r} (use one of: {', '.join(VALUE_RULES)})")
    return failures


def _trajectory(called: list[str], rule: Any) -> str | None:
    """What's wrong with the order of the tool calls, if anything."""
    if isinstance(rule, dict):
        wanted, order = list(rule.get("tools") or []), rule.get("order", "in_order")
    else:
        wanted, order = list(rule or []), "in_order"
    shown = " → ".join(called) or "no tools"
    if order not in TRAJECTORY_ORDERS:
        return f"unknown order {order!r} (use one of: {', '.join(TRAJECTORY_ORDERS)})"
    if order == "exact":
        ok = called == wanted
        need = "exactly " + " → ".join(wanted)
    elif order == "any_order":
        ok = all(called.count(t) >= wanted.count(t) for t in set(wanted))
        need = "all of " + ", ".join(wanted)
    else:
        rest = iter(called)
        ok = all(tool in rest for tool in wanted)  # a subsequence, in this order
        need = " → ".join(wanted) + " in this order"
    return None if ok else f"expected {need}; the agent called {shown}"


def _budget(name: str, got: float | None, limit: Any, unit: str) -> Check:
    if got is None:
        return Check(name, None, "skipped: the model's price isn't known, so there's no cost")
    if got > float(limit):
        return Check(name, False, f"expected at most {limit}{unit}, took {got:g}{unit}")
    return Check(name, True, f"{got:g}{unit}")


def evaluate(expect: dict[str, Any], facts: RunFacts) -> list[Check]:
    """Every check of a case except the AI judge (``judge`` is async: see ``judge``)."""
    expect = dict(expect or {})
    final, events = facts.final, facts.events
    checks: list[Check] = []
    expected_status = expect.pop("status", "ok")
    if final.get("status") != expected_status:
        message = (final.get("error") or {}).get("message", "")
        checks.append(
            Check(
                "status",
                False,
                f"expected {expected_status}, got {final.get('status')} {message}".strip(),
            )
        )
    else:
        checks.append(Check("status", True, str(expected_status)))
    expect.pop("judge", None)
    called = facts.tools
    metrics = facts.metrics()
    for key, rule in expect.items():
        # (label, message) for each thing wrong; the label is what the line starts with.
        failures: list[tuple[str, str]] = []
        if key == "steps":
            ran = [e["step"] for e in events if e.get("type") == "step_finished"]
            failures = [(key, f"expected {s!r} to run") for s in rule if s not in ran]
        elif key == "tools":
            failures = [
                (key, f"expected the agent to call {t!r} (called {called})")
                for t in rule
                if t not in called
            ]
        elif key == "not_tools":
            failures = [
                ("tools", f"expected the agent not to call {t!r}") for t in rule if t in called
            ]
        elif key == "tool_calls":
            count = len(called)
            limit = rule.get("max") if isinstance(rule, dict) else None
            least = rule.get("min") if isinstance(rule, dict) else None
            if limit is not None and count > int(limit):
                failures = [(key, f"expected at most {limit}, got {count}")]
            elif least is not None and count < int(least):
                failures = [(key, f"expected at least {least}, got {count}")]
            elif not isinstance(rule, dict) and count != int(rule):
                failures = [(key, f"expected {rule}, got {count}")]
        elif key == "trajectory":
            problem = _trajectory(called, rule)
            failures = [(key, problem)] if problem else []
        elif key == "helpers":
            handed = [e["to_helper"] for e in events if e.get("to_helper") and "result" not in e]
            failures = [
                (key, f"expected a task for {h!r} (got {handed})") for h in rule if h not in handed
            ]
        elif key == "files":
            # A Deep Agent's files, {path: text}, in whichever output field holds them.
            output = final.get("output") or {}
            for path, rules in rule.items():
                found = next(
                    (v[path] for v in output.values() if isinstance(v, dict) and path in v), None
                )
                if found is None:
                    failures.append((key, f"expected the file {path!r}"))
                else:
                    failures += [(f"files {path}", msg) for msg in check_value(found, rules)]
        elif key == "route":
            routes = {e["step"]: e["exit"] for e in events if e.get("type") == "route"}
            failures = [
                (f"route {step}", f"expected {label!r}, got {routes.get(step)!r}")
                for step, label in rule.items()
                if routes.get(step) != label
            ]
        elif key == "max_seconds":
            checks.append(_budget(key, metrics["seconds"], rule, " s"))
            continue
        elif key == "max_tokens":
            checks.append(_budget(key, float(metrics["tokens"]), rule, " tokens"))
            continue
        elif key == "max_cost":
            checks.append(_budget(key, metrics["cost"], rule, " $"))
            continue
        else:
            failures = [(key, msg) for msg in check_value(lookup(final, key), rule)]
        if failures:
            checks += [Check(label, False, message) for label, message in failures]
        else:
            checks.append(Check(key, True))
    return checks


def judge_model(chosen: str | None = None) -> str | None:
    """The judge's model: the one named, else the copilot's choice (a model with a key)."""
    from .copilot import copilot_model

    return copilot_model(chosen)


async def judge(
    rule: Any, facts: RunFacts, model: str | None = None, *, stand_in: bool = False
) -> Check:
    """An AI model grades the output against the rubric. Skipped when no model has a key, and
    when the case ran with the stand-in AI (its answers are made up, and offline runs stay
    free)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from .runtime.gateway import RunSettings, gateway_init_chat_model, reset_settings, use_settings

    if isinstance(rule, str):
        rule = {"rubric": rule}
    rubric = str(rule.get("rubric") or "").strip()
    if not rubric:
        return Check("judge", False, "the judge needs a rubric: what a good answer does")
    pass_score = float(rule.get("pass_score", 0.7))
    if stand_in:
        return Check(
            "judge",
            None,
            "skipped: the case ran with the stand-in AI; the judge grades real models' answers",
        )
    chosen = judge_model(rule.get("model") or model)
    if chosen is None:
        return Check(
            "judge",
            None,
            "skipped: no AI model has a key for the judge. Add one in Settings, or name a "
            "model with judge.model.",
        )
    output = (
        lookup(facts.final, rule["look_at"]) if rule.get("look_at") else facts.final.get("output")
    )
    message = (
        f"Rubric:\n{rubric}\n\nInput:\n{json.dumps(facts.inputs, ensure_ascii=False, default=str)}"
        f"\n\nOutput:\n{json.dumps(output, ensure_ascii=False, default=str)}"
    )
    # Always a real model: the judge's grade is never made up by the stand-in.
    token = use_settings(RunSettings(stand_in=False))
    try:
        chat = gateway_init_chat_model(chosen).with_structured_output(JUDGE_SCHEMA)
        verdict = await chat.ainvoke([SystemMessage(JUDGE_PROMPT), HumanMessage(message)])
    except Exception as exc:
        reason = re.sub(r"\s+", " ", str(exc))[:300] or type(exc).__name__
        return Check("judge", False, f"the judge ({chosen}) couldn't grade it: {reason}")
    finally:
        reset_settings(token)
    score = _number((verdict or {}).get("score"))
    reason = str((verdict or {}).get("reason") or "").strip()
    if score is None:
        return Check("judge", False, f"the judge ({chosen}) gave no score")
    score = max(0.0, min(1.0, score))
    return Check(
        "judge",
        score >= pass_score,
        f"score {score:.2f} (needs {pass_score:.2f}) from {chosen}: {reason}".strip(),
    )
