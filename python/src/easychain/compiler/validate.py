"""Checks before a run: structure, connections, data flow and step settings.

Every problem is phrased for a person, pinned to a step when possible, and
offers a one-click fix where one exists.
"""

from __future__ import annotations

import re

from ..spec.models import FlowSpec
from .analysis import FlowAnalysis
from .issues import Fix, Issue, error, warning
from .reducers import check_combine


def validate(spec: FlowSpec, analysis: FlowAnalysis | None = None) -> list[Issue]:
    an = analysis or FlowAnalysis(spec)
    issues: list[Issue] = []
    issues += _check_structure(an)
    issues += _check_connections(an)
    for sid in an.order:
        step = an.steps[sid]
        if sid not in an.reachable and step.type != "input":
            issues.append(
                warning(
                    "unreachable",
                    "This step isn't connected to Input, so it won't run.",
                    step=sid,
                    hint="Connect it, or delete it if you don't need it.",
                )
            )
        try:
            issues += an.handlers[sid].check(step, an)
        except Exception as exc:  # a broken check must never hide the others
            issues.append(error("check_failed", f"Couldn't check this step: {exc}", step=sid))
    issues += _check_fields(an)
    issues += _check_loops(an)
    return _dedupe(issues)


def _check_structure(an: FlowAnalysis) -> list[Issue]:
    issues: list[Issue] = []
    if not an.input_steps:
        issues.append(
            error(
                "no_input",
                "The flow needs an Input step so a run knows where to start.",
                fix=Fix("add_step", "Add an Input step", {"type": "input"}),
            )
        )
    for extra in an.input_steps[1:]:
        issues.append(
            error(
                "multiple_inputs",
                "A flow has one Input step. Delete this one or merge its fields into the first.",
                step=extra.id,
            )
        )
    if not an.output_steps:
        issues.append(
            warning(
                "no_output",
                "The flow has no Output step, so a run returns all Flow Data.",
                fix=Fix("add_step", "Add an Output step", {"type": "output"}),
            )
        )
    for step in an.spec.steps:
        if "__" in step.id:
            issues.append(
                error(
                    "step_id_double_underscore",
                    f"The step id `{step.id}` has two underscores in a row; Easy Chain keeps those "
                    "for its own steps.",
                    step=step.id,
                    fix=Fix(
                        "rename_step",
                        f"Rename it to `{_single_underscores(step.id)}`",
                        {"to": _single_underscores(step.id)},
                    ),
                )
            )
    node_steps = [s for s in an.reachable if an.handlers[s].has_node]
    if an.input_step is not None and an.outgoing[an.input_step.id] and not node_steps:
        issues.append(
            error(
                "nothing_to_run",
                "There are no steps between Input and Output yet.",
                hint="Drag an AI Model or another step from the library onto the canvas.",
            )
        )
    return issues


def _single_underscores(name: str) -> str:
    return re.sub(r"_{2,}", "_", name)


def _check_connections(an: FlowAnalysis) -> list[Issue]:
    issues: list[Issue] = []
    for conn in an.bad_connections:
        missing = conn.source if conn.source not in an.steps else conn.target
        issues.append(
            error(
                "connection_missing_step",
                f"A connection points to a step that doesn't exist (“{missing}”).",
                hint="Delete the connection or re-create the step.",
            )
        )
    seen: set[tuple[str, str, str | None]] = set()
    for conn in an.spec.connections:
        if conn.source not in an.steps or conn.target not in an.steps:
            continue
        key = (conn.source, conn.target, conn.exit)
        if key in seen:
            issues.append(
                warning(
                    "duplicate_connection",
                    "Two identical connections; one is enough.",
                    step=conn.source,
                )
            )
        seen.add(key)
        source, target = an.steps[conn.source], an.steps[conn.target]
        if target.type == "input":
            issues.append(
                error(
                    "into_input",
                    "Nothing can connect into Input; it's where runs start.",
                    step=conn.source,
                )
            )
        if source.type == "output":
            issues.append(
                error(
                    "out_of_output",
                    "Output is the end of the flow; it can't lead anywhere.",
                    step=conn.source,
                )
            )
        if conn.source == conn.target:
            issues.append(error("self_loop", "A step can't connect to itself.", step=conn.source))
        if source.type == "input" and target.type == "output":
            issues.append(
                error(
                    "input_to_output",
                    "Input connects straight to Output, so nothing would happen.",
                    step=conn.source,
                    hint="Put a step such as an AI Model in between.",
                )
            )
        if conn.exit is not None and not an.handlers[conn.source].exits(source):
            issues.append(
                warning(
                    "exit_on_plain_step",
                    "This step has no exits; this connection's exit label is ignored.",
                    step=conn.source,
                )
            )
    return issues


def _check_fields(an: FlowAnalysis) -> list[Issue]:
    issues: list[Issue] = []
    for name, old, new in an.field_conflicts:
        writers = ", ".join(an.fields[name].written_by)
        issues.append(
            warning(
                "field_type_conflict",
                f"`{name}` is set to {old} by one step and {new} by another ({writers}).",
                hint="Use different field names, or declare the field's type in Flow Data.",
            )
        )
    for sid in an.steps:
        if an.handlers[sid].has_node and sid in an.fields:
            issues.append(
                error(
                    "step_id_is_field",
                    f"This step's id `{sid}` is also the name of a Flow Data field; they must differ.",
                    step=sid,
                    hint="Rename the step's id (under More options) or save its result under another name.",
                    fix=Fix(
                        "rename_step", f"Rename the step to `{sid}_step`", {"to": f"{sid}_step"}
                    ),
                )
            )
    for out in an.output_steps:
        for name in out.settings.fields:
            if name not in an.fields:
                issues.append(
                    error(
                        "output_unknown_field",
                        f"Output returns `{name}`, but no step saves a field with that name.",
                        step=out.id,
                        setting="fields",
                    )
                )
            elif name not in an.available_fields(out.id) and out.id in an.reachable:
                issues.append(
                    warning(
                        "output_field_unset",
                        f"Output returns `{name}`, but no step before it saves that field.",
                        step=out.id,
                        setting="fields",
                    )
                )
    for decl in an.spec.data:
        if decl.update == "custom":
            problem = (
                check_combine(decl.combine)
                if decl.combine.strip()
                else ("Write the function that combines the old and new value.")
            )
            if problem:
                issues.append(
                    error(
                        "bad_update_rule",
                        f"The update rule for `{decl.name}`: {problem}",
                        hint="It looks like: def combine(old, new): return new",
                    )
                )
        elif decl.combine.strip():
            issues.append(
                warning(
                    "update_code_unused",
                    f"`{decl.name}` has update code, but its rule is “{decl.update}”, so the code isn't used.",
                )
            )
    for info in an.fields.values():
        if info.update == "merge" and info.type not in ("object", "any"):
            issues.append(
                warning(
                    "merge_non_object",
                    f"The update rule “merge” only works for objects (`{info.name}`).",
                )
            )
        if info.update == "add" and info.type not in ("number", "list", "text", "any", "messages"):
            issues.append(
                warning(
                    "add_odd_type",
                    f"The update rule “add” doesn't fit `{info.name}` ({info.type}).",
                )
            )
    return issues


def _check_loops(an: FlowAnalysis) -> list[Issue]:
    issues: list[Issue] = []
    reported: set[frozenset[str]] = set()
    for sid in sorted(an.in_cycle):
        loop = frozenset(
            s for s in an.in_cycle if s in an.ancestors[sid] and sid in an.ancestors[s]
        )
        if loop in reported:
            continue
        reported.add(loop)
        in_order = sorted(loop, key=lambda s: (an.depth.get(s, 10**9), an.index[s]))
        leaving = [
            s
            for s in in_order
            if an.handlers[s].exits(an.steps[s]) and an.steps[s].type != "for_each"
        ]
        if not leaving:
            issues.append(
                error(
                    "loop_without_exit",
                    "These steps loop forever: nothing in the loop can decide to leave it.",
                    step=sid,
                    hint="Add a Decision inside the loop with an exit that leaves it.",
                )
            )
            continue
        decisions = [s for s in leaving if an.steps[s].type == "decision"]
        if any(an.steps[s].settings.max_rounds for s in decisions):
            continue
        if any(an.steps[s].type == "ask_human" for s in leaving):
            continue  # a person decides when to leave
        max_steps = an.spec.settings.max_steps
        if decisions:
            guard = decisions[0]
            issues.append(
                warning(
                    "loop_no_guard",
                    f"This loop has no round limit; a run stops with an error after {max_steps} steps.",
                    step=guard,
                    setting="max_rounds",
                    hint="Give this Decision a round limit, so the loop ends cleanly.",
                    fix=Fix(
                        "set_setting", "Add a round limit of 10", {"key": "max_rounds", "value": 10}
                    ),
                )
            )
        else:
            issues.append(
                warning(
                    "loop_no_guard",
                    f"This loop has no round limit; a run stops with an error after {max_steps} steps.",
                    step=leaving[0],
                    hint="Make sure the loop eventually leaves, or use a Decision with a round limit.",
                )
            )
    return issues


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: set[tuple] = set()
    out: list[Issue] = []
    for issue in issues:
        key = (issue.level, issue.code, issue.step, issue.setting, issue.message)
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out
