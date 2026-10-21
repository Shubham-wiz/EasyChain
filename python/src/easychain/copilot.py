"""The describe-it copilot: describe a flow, or a change to one, in words; get a proposal to
review on the canvas.

The model writes the whole flow as YAML. Easy Chain reads it with the flow spec, runs the
checks, and sends any problems back for the model to fix (twice at most), so a proposal is
shown only once it has no errors. The proposal is a list of changes (Flow Data, steps,
connections, the flow's settings) and Test Set cases; the editor previews the ones the person
keeps and applies them as one undo step.

Any model the person has a key for can be the copilot (decision 4.3). With no key, the
stand-in builds an example from the template closest to the request, so the panel still shows
what a proposal looks like.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from .compiler.analysis import FlowAnalysis, Resolver
from .compiler.issues import Issue
from .compiler.validate import validate
from .runtime.gateway import (
    RunSettings,
    gateway_init_chat_model,
    key_status,
    reset_settings,
    use_settings,
)
from .runtime.standin import Script, words
from .spec import FlowSpec, SpecError, dumps_spec, parse_spec, spec_json
from .spec.models import STEP_MODELS
from .steps import HANDLERS

MAX_FIXES = 2

# Capable models, in the order the copilot picks them when the person hasn't chosen one: the
# first whose provider has its API key set (providers that need no key, like Ollama, are only
# used when chosen).
PREFERRED_MODELS = [
    "anthropic:claude-sonnet-5-5",
    "openai:gpt-4.1",
    "google_genai:gemini-2.5-pro",
    "mistralai:mistral-large-latest",
    "deepseek:deepseek-chat",
    "xai:grok-4",
    "groq:openai/gpt-oss-120b",
    "openrouter:anthropic/claude-haiku-4.5",
    "together:deepseek-ai/DeepSeek-V3",
    "azure_openai:gpt-4o",
]


def copilot_model(chosen: str | None = None) -> str | None:
    """The model the copilot uses: the chosen one, else the first preferred model with a key
    set, else None (the stand-in)."""
    if chosen:
        return chosen
    from .providers import PROVIDERS

    keys = key_status()
    for model in PREFERRED_MODELS:
        provider = PROVIDERS.get(model.split(":", 1)[0])
        if provider is not None and provider.key_env and keys.get(provider.id):
            return model
    return None


@dataclass
class Proposal:
    ok: bool
    summary: str
    spec: FlowSpec | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    test_cases: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    model: str = "stand-in"
    attempts: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary,
            "spec": spec_json(self.spec) if self.spec is not None else None,
            "changes": self.changes,
            "test_cases": self.test_cases,
            "issues": [i.to_dict() for i in self.issues],
            "model": self.model,
            "attempts": self.attempts,
            "problems": self.problems,
        }


# ── what the model is told ───────────────────────────────────────────────────


def _setting_line(name: str, info: Any) -> str:
    default = info.get_default(call_default_factory=True) if not info.is_required() else None
    shown = json.dumps(default, default=str) if default not in (None, [], {}, "") else ""
    text = re.split(r"(?<=[a-z)])\. (?=[A-Z])", info.description or "")[0].strip().rstrip(".")
    return f"{name}{'=' + shown if shown else ''}{' (' + text + ')' if text else ''}"


def catalog_text() -> str:
    """Every step type with its settings, for the model."""
    lines = []
    for handler in HANDLERS.values():
        model = STEP_MODELS[handler.type]
        settings_model = model.model_fields["settings"].annotation
        fields = getattr(settings_model, "model_fields", {})
        settings = "; ".join(_setting_line(n, f) for n, f in fields.items())
        lines.append(f"- {handler.type} ({handler.label}): {handler.summary} Settings: {settings}")
    return "\n".join(lines)


RULES = """\
Rules for the flow:
- version: 1, a name, and steps; one step of type input (its fields are what a run takes; mode: chat for a chat) and one of type output (fields: what a run returns).
- Step ids and Flow Data field names are lowercase_with_underscores, unique, and a step id is never the name of a Flow Data field.
- connections: [{from: <step id>, to: <step id>}]. Every step is reachable from the input step. A Decision (and Ask a Human, For Each, Jump) leaves by its exits: add exit: <label> to each of its connections, one per exit and one for its otherwise exit.
- Put Flow Data into text with {field}. A step's save_as names the Flow Data field it writes; later steps read it.
- An instructions step builds a prompt (saved as prompt) for the next ai_model step; an ai_model step alone sends the field in its prompt setting.
- Steps an agent uses as tools are listed in its tools setting and have no connections of their own; give each a description. A deep_agent lists its helper steps in helpers.
- Leave out settings that keep their default, and leave out canvas.
"""


def system_prompt(examples: list[str]) -> str:
    return (
        "You are the copilot of Easy Chain, a visual builder for LLM apps. You build and change "
        "flows. A flow is YAML: steps (each with id, type, name, settings), connections, and "
        "Flow Data (data). Step types:\n\n"
        f"{catalog_text()}\n\n{RULES}\n"
        "Reply with one YAML document in a ```yaml block, with three keys: summary (one or two "
        "plain sentences on what you made or changed), flow (the whole flow, also the parts you "
        "kept), and tests (two or three test cases: {name, inputs, expect: {output.<field>: "
        "{not_empty: true}}} or with contains).\n\nAn example flow:\n\n"
        + "\n\n".join(f"```yaml\n{e}```" for e in examples)
    )


def _without_canvas(spec: FlowSpec) -> str:
    return dumps_spec(spec.model_copy(update={"canvas": spec.canvas.__class__()}))


def request_message(request: str, current: FlowSpec | None, issue: dict | None) -> str:
    parts = []
    if current is not None and current.steps:
        parts.append(f"The flow now:\n\n```yaml\n{_without_canvas(current)}```")
    if issue:
        where = f" (step {issue.get('step')})" if issue.get("step") else ""
        parts.append(f"Fix this problem the checks found{where}: {issue.get('message')}")
    parts.append(f"What the person asks:\n{request.strip()}" if request.strip() else "")
    return "\n\n".join(p for p in parts if p)


# ── reading the reply ────────────────────────────────────────────────────────

_YAML_BLOCK = re.compile(r"```(?:ya?ml)?\s*\n(.*?)```", re.S)
_SUMMARY = re.compile(r"^summary:[ \t]*(?![\"'|>])(.+)$", re.M)


def read_reply(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    """The reply's summary, flow and tests, or the problems that keep it from being read."""
    found = _YAML_BLOCK.search(text)
    body = found.group(1) if found else text
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        # Models often write the summary as plain text with a colon in it; quote it and try again.
        repaired = _SUMMARY.sub(lambda m: f"summary: {json.dumps(m.group(1).strip())}", body, 1)
        try:
            data = yaml.safe_load(repaired)
        except yaml.YAMLError:
            return None, [f"The reply isn't valid YAML: {str(exc).splitlines()[0]}"]
    if isinstance(data, dict) and "flow" not in data and "steps" in data:
        data = {"flow": data}
    if not isinstance(data, dict) or not isinstance(data.get("flow"), dict):
        return None, ["The reply must be a YAML document with summary, flow and tests."]
    return data, []


def check_flow(
    data: dict[str, Any], resolve: Resolver | None
) -> tuple[FlowSpec | None, list[str], list[Issue]]:
    """The flow from a reply, the problems (errors) to fix, and the warnings left."""
    flow = dict(data["flow"])
    flow.setdefault("version", 1)
    try:
        spec = parse_spec(flow)
    except SpecError as exc:
        return None, exc.problems, []
    issues = validate(spec, FlowAnalysis(spec, resolve=resolve))
    errors = [
        f"{i.message}" + (f" (step {i.step})" if i.step else "") + (f" {i.hint}" if i.hint else "")
        for i in issues
        if i.level == "error"
    ]
    return spec, errors, [i for i in issues if i.level != "error"]


def _test_cases(data: dict[str, Any]) -> list[dict[str, Any]]:
    tests = data.get("tests") or []
    cases = []
    for n, case in enumerate(tests if isinstance(tests, list) else []):
        if isinstance(case, dict) and isinstance(case.get("inputs", {}), dict):
            cases.append({"name": str(case.get("name") or f"Case {n + 1}"), **case})
    return cases[:10]


# ── the changes between two flows ────────────────────────────────────────────


def _name(step: Any) -> str:
    return step.name or step.id


def _connection(c: Any) -> dict[str, Any]:
    return {"from": c.source, "to": c.target, "exit": c.exit}


COLUMN = 300  # from one step to the next on the canvas
ROW = 160


def _owners(spec: FlowSpec) -> dict[str, str]:
    """Steps used as an agent's tools or a Deep Agent's Helpers, and the step they belong to."""
    out: dict[str, str] = {}
    for step in spec.steps:
        settings = step.settings if isinstance(step.settings, dict) else step.settings.model_dump()
        for key in ("tools", "helpers"):
            for member in settings.get(key) or []:
                if isinstance(member, str):
                    out.setdefault(member, step.id)
    return out


def place_new_steps(current: FlowSpec | None, proposed: FlowSpec) -> dict[str, dict[str, float]]:
    """Canvas positions. Steps that were there keep theirs; a new step goes right of the step
    that leads into it (an agent's new tool goes under the agent). A step that was there and
    now comes after a new one moves right to make room."""
    old = {
        sid: {"x": p.x, "y": p.y} for sid, p in (current.canvas.steps if current else {}).items()
    }
    old = {sid: p for sid, p in old.items() if sid in proposed.step_map()}
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for c in proposed.connections:
        incoming.setdefault(c.target, []).append(c.source)
        outgoing.setdefault(c.source, []).append(c.target)
    owners = _owners(proposed)
    an = FlowAnalysis(proposed)
    positions: dict[str, dict[str, float]] = {}
    moved_on: set[str] = set()  # new steps, and steps that moved for them

    def after(sid: str) -> set[str]:
        seen, todo = set(), list(outgoing.get(sid, []))
        while todo:
            nxt = todo.pop()
            if nxt not in seen:
                seen.add(nxt)
                todo.extend(outgoing.get(nxt, []))
        return seen

    def free(sid: str, x: float, y: float) -> bool:
        # Steps that come after this one will move right, so they don't count.
        later = after(sid)
        others = [p for s, p in positions.items()] + [
            p for s, p in old.items() if s not in positions and s not in later
        ]
        return all(abs(x - p["x"]) >= 260 or abs(y - p["y"]) >= 140 for p in others)

    for sid in an.order:
        leads_in = [s for s in incoming.get(sid, []) if s in positions]
        if sid in old:
            x, y = old[sid]["x"], old[sid]["y"]
            pushers = [positions[s]["x"] for s in leads_in if s in moved_on]
            if pushers and x < max(pushers) + COLUMN:
                x = max(pushers) + COLUMN
                moved_on.add(sid)
            positions[sid] = {"x": x, "y": y}
            continue
        owner = positions.get(owners.get(sid, ""))
        if owner is not None:
            x, y = owner["x"], owner["y"] + 240
            while not free(sid, x, y):
                x += 260
        else:
            if leads_in:
                x = max(positions[s]["x"] for s in leads_in) + COLUMN
                y = positions[leads_in[0]]["y"]
            else:
                x, y = an.depth.get(sid, 0) * float(COLUMN), 120.0
            while not free(sid, x, y):
                y += ROW
        positions[sid] = {"x": x, "y": y}
        moved_on.add(sid)
    return positions


def diff(current: FlowSpec | None, proposed: FlowSpec) -> list[dict[str, Any]]:
    """The proposal as separate changes, each with a plain label, in the order to apply them."""
    before = current or FlowSpec(name=proposed.name)
    changes: list[dict[str, Any]] = []
    flow: dict[str, Any] = {}
    if proposed.name != before.name:
        flow["name"] = proposed.name
    if proposed.description != before.description:
        flow["description"] = proposed.description
    if proposed.settings != before.settings:
        flow["settings"] = proposed.settings.model_dump(mode="json")
    if flow:
        changes.append({"op": "set_flow", **flow, "label": "Change the flow's name and settings"})
    old_data = {d.name: d for d in before.data}
    for d in proposed.data:
        if old_data.get(d.name) != d:
            what = "Add" if d.name not in old_data else "Change"
            changes.append(
                {
                    "op": "set_data",
                    "field": d.model_dump(mode="json"),
                    "label": f"{what} the Flow Data field `{d.name}`",
                }
            )
    for name in old_data:
        if name not in {d.name for d in proposed.data}:
            changes.append(
                {"op": "remove_data", "name": name, "label": f"Remove the Flow Data field `{name}`"}
            )
    positions = place_new_steps(current, proposed)
    was = {sid: (p.x, p.y) for sid, p in before.canvas.steps.items()}
    old_steps = before.step_map()
    new_steps = proposed.step_map()
    for sid, step in new_steps.items():
        data = json.loads(step.model_dump_json(by_alias=True))
        label = HANDLERS[step.type].label
        if sid not in old_steps:
            changes.append(
                {
                    "op": "add_step",
                    "step": data,
                    "position": positions.get(sid, {"x": 0, "y": 0}),
                    "label": f"Add the {label} step “{_name(step)}”",
                }
            )
        elif old_steps[sid] != step:
            old = old_steps[sid]
            what = sorted(
                k
                for k in ("name", "description", "settings", "run")
                if getattr(old, k, None) != getattr(step, k, None)
            )
            if old.type != step.type:
                what = ["type", *what]
            changes.append(
                {
                    "op": "update_step",
                    "step": data,
                    "label": f"Change “{_name(step)}” ({', '.join(what)})",
                }
            )
    for sid, step in old_steps.items():
        if sid not in new_steps:
            changes.append(
                {"op": "remove_step", "step": sid, "label": f"Remove the step “{_name(step)}”"}
            )
    moved = {
        sid: positions[sid]
        for sid in old_steps
        if sid in positions
        and sid in was
        and (positions[sid]["x"], positions[sid]["y"]) != was[sid]
    }
    if moved:
        names_moved = ", ".join(f"“{_name(old_steps[sid])}”" for sid in moved)
        changes.append(
            {
                "op": "move_steps",
                "positions": moved,
                "label": f"Move {names_moved} right, to make room",
            }
        )
    old_conns = {(c.source, c.target, c.exit) for c in before.connections}
    new_conns = {(c.source, c.target, c.exit) for c in proposed.connections}
    names = {
        **{s: _name(st) for s, st in old_steps.items()},
        **{s: _name(st) for s, st in new_steps.items()},
    }
    for c in proposed.connections:
        if (c.source, c.target, c.exit) not in old_conns:
            via = f" by “{c.exit}”" if c.exit else ""
            changes.append(
                {
                    "op": "add_connection",
                    "connection": _connection(c),
                    "label": f"Connect “{names[c.source]}” to “{names[c.target]}”{via}",
                }
            )
    for c in before.connections:
        if (c.source, c.target, c.exit) not in new_conns:
            changes.append(
                {
                    "op": "remove_connection",
                    "connection": _connection(c),
                    "label": f"Remove the connection from “{names.get(c.source, c.source)}” to "
                    f"“{names.get(c.target, c.target)}”",
                }
            )
    for n, change in enumerate(changes):
        change["id"] = f"c{n + 1}"
    return changes


def with_positions(current: FlowSpec | None, proposed: FlowSpec) -> FlowSpec:
    data = json.loads(proposed.model_dump_json(by_alias=True))
    data["canvas"] = {
        **(json.loads(current.canvas.model_dump_json()) if current is not None else {}),
        "steps": place_new_steps(current, proposed),
    }
    return FlowSpec.model_validate(data)


# ── proposals ────────────────────────────────────────────────────────────────


def _examples() -> list[str]:
    from .templates import load_template

    return [_without_canvas(load_template("summarise-url"))]


def propose(
    request: str,
    current: FlowSpec | None = None,
    *,
    model: str | None = None,
    stand_in: bool = False,
    script: Script | None = None,
    resolve: Resolver | None = None,
    issue: dict[str, Any] | None = None,
) -> Proposal:
    """A proposal for the request (and, with ``issue``, a fix for that problem)."""
    if not stand_in and model is None:
        model = copilot_model()
    if stand_in or model is None:
        if script is None:
            return canned(request, current, issue)
        model = model or "stand-in"
    token = use_settings(RunSettings(stand_in=stand_in or model == "stand-in", script=script))
    try:
        chat = gateway_init_chat_model(model)
        messages: list[Any] = [
            SystemMessage(system_prompt(_examples())),
            HumanMessage(request_message(request, current, issue)),
        ]
        problems: list[str] = []
        for attempt in range(1, MAX_FIXES + 2):
            reply = chat.invoke(messages)
            text = reply.text if hasattr(reply, "text") else str(reply.content)
            data, problems = read_reply(text)
            spec = issues = None
            if data is not None:
                spec, problems, issues = check_flow(data, resolve)
            if not problems and spec is not None and data is not None:
                placed = with_positions(current, spec)
                return Proposal(
                    ok=True,
                    summary=str(data.get("summary") or "Here is the flow.").strip(),
                    spec=placed,
                    changes=diff(current, placed),
                    test_cases=_test_cases(data),
                    issues=issues or [],
                    model=model,
                    attempts=attempt,
                )
            messages += [
                AIMessage(text),
                HumanMessage(
                    "Easy Chain found these problems in the flow:\n"
                    + "\n".join(f"- {p}" for p in problems)
                    + "\n\nReply with the whole corrected YAML document, in the same format."
                ),
            ]
        return Proposal(
            ok=False,
            summary="The copilot couldn't make a flow without problems. Try saying what you "
            "want in other words, or build it step by step.",
            model=model,
            attempts=MAX_FIXES + 1,
            problems=problems,
        )
    finally:
        reset_settings(token)


def canned(request: str, current: FlowSpec | None, issue: dict[str, Any] | None = None) -> Proposal:
    """The stand-in copilot (no API key): the template closest to the request, as an example."""
    from .templates import TEMPLATES, load_template, template_cases

    if issue:
        return Proposal(
            ok=False,
            summary="The stand-in copilot can't fix problems. Use the problem's own fix button "
            "if it has one, or add an API key (Settings) for a copilot that reads the flow.",
        )
    asked = words(request)
    best, score = TEMPLATES[0]["id"], -1
    for meta in TEMPLATES:
        spec = load_template(meta["id"])
        text = f"{spec.name} {spec.description} {' '.join(meta.get('proves', []))}"
        overlap = len(asked & words(text))
        if overlap > score:
            best, score = meta["id"], overlap
    example = load_template(best)
    if current is not None and current.steps:
        # Changing a flow: keep its name and where its steps are.
        placed = with_positions(current, example.model_copy(update={"name": current.name}))
    else:
        placed = example
    return Proposal(
        ok=True,
        summary=f"[Stand-in copilot] An example to start from: the “{load_template(best).name}” "
        "template, the closest to what you asked. Add an API key (Settings) for a copilot that "
        "builds what you describe.",
        spec=placed,
        changes=diff(current, placed),
        test_cases=template_cases(best)[:3],
        issues=[i for i in validate(placed) if i.level != "error"],
        model="stand-in",
        attempts=0,
    )
