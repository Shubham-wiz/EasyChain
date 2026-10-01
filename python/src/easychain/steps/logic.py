"""Decision step: picks which connection to follow (a LangGraph conditional edge)."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable
from typing import Any

from ..compiler.analysis import FieldInfo
from ..compiler.expressions import ExpressionError, compile_expression, field_names
from ..compiler.issues import Issue, error, warning
from ..compiler.pycode import docstring, py_literal, py_regex, py_str
from ..providers import model_info
from .ai import check_model, missing_field_issue
from .base import FormField, StepCode, StepHandler, template_value

OPS: dict[str, str] = {
    "equals": "is",
    "not_equals": "is not",
    "contains": "contains",
    "not_contains": "doesn't contain",
    "starts_with": "starts with",
    "ends_with": "ends with",
    "matches": "matches the pattern",
    "is_empty": "is empty",
    "is_not_empty": "is not empty",
    "greater_than": "is more than",
    "less_than": "is less than",
    "longer_than": "is longer than (characters or items)",
    "shorter_than": "is shorter than (characters or items)",
    "is_true": "is yes",
    "is_false": "is no",
}
NO_VALUE_OPS = {"is_empty", "is_not_empty", "is_true", "is_false"}
NUMBER_OPS = {"greater_than", "less_than", "longer_than", "shorter_than"}


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    try:
        num = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return int(num) if num.is_integer() else num


def describe_condition(cond: Any) -> str:
    if cond is None:
        return "always"
    if cond.expression:
        return cond.expression
    op = OPS.get(cond.op, cond.op)
    if cond.op in NO_VALUE_OPS:
        return f"{cond.field} {op}"
    return f"{cond.field} {op} {cond.value!r}"


def condition_source(cond: Any, field_type: str) -> str:
    """Python source for one condition, reading fields from ``data``."""
    if cond.expression:
        return compile_expression(cond.expression)
    get = f"data.get({py_str(cond.field)})"
    text = f'str({get} or "")'
    op, value = cond.op, cond.value
    if op == "is_empty":
        return f"not {get}"
    if op in ("is_not_empty", "is_true"):
        return f"bool({get})"
    if op == "is_false":
        return f"not {get}"
    if op in ("longer_than", "shorter_than"):
        sign = ">" if op == "longer_than" else "<"
        return f'len({get} or "") {sign} {py_literal(_number(value))}'
    if op in ("greater_than", "less_than"):
        sign = ">" if op == "greater_than" else "<"
        return f"float({get} or 0) {sign} {py_literal(_number(value))}"
    if op == "matches":
        return f"re.search({py_regex(str(value))}, {text}, re.IGNORECASE) is not None"
    if op in ("equals", "not_equals"):
        sign = "==" if op == "equals" else "!="
        if field_type == "number" and _number(value) is not None:
            return f"{get} {sign} {py_literal(_number(value))}"
        if field_type == "yes_no" or isinstance(value, bool):
            truthy = str(value).strip().lower() in ("true", "yes", "1")
            return f"bool({get}) is {truthy}" if sign == "==" else f"bool({get}) is not {truthy}"
        return f"{text}.strip().lower() {sign} {py_str(str(value).strip().lower())}"
    if op in ("contains", "not_contains"):
        neg = "not in" if op == "not_contains" else "in"
        if field_type == "list":
            return f"{py_literal(value)} {neg} ({get} or [])"
        return f"{py_str(str(value).lower())} {neg} {text}.lower()"
    if op in ("starts_with", "ends_with"):
        method = "startswith" if op == "starts_with" else "endswith"
        return f"{text}.lower().{method}({py_str(str(value).lower())})"
    raise ValueError(f"Unknown condition {op}")


def conditions_body(
    exits: list[Any],
    otherwise: str,
    ctx: Any,
    result: Callable[[str], str] | None = None,
    indent: str = "    ",
) -> list[str]:
    """Lines that check exit rules top to bottom and return the first match."""
    out = result or py_str
    body: list[str] = []
    for ex in exits:
        if ex.when is None:
            body.append(f"{indent}return {out(ex.label)}")
            return body
        src = condition_source(ex.when, ctx.field_type(ex.when.field))
        if "re.search" in src:
            ctx.imports.add("re")
        body.append(f"{indent}if {src}:\n{indent}    return {out(ex.label)}")
    body.append(f"{indent}return {out(otherwise)}")
    return body


def exit_connection_issues(
    step: Any, labels: list[str], an: Any, noun: str = "Decision"
) -> list[Issue]:
    """Connections out of a step with exits must each leave from one existing exit."""
    issues: list[Issue] = []
    connected: set[str] = set()
    for conn in an.outgoing[step.id]:
        if conn.exit is None:
            issues.append(
                error(
                    "decision_connection_no_exit",
                    f"A connection leaves this {noun} without an exit label.",
                    step=step.id,
                    hint="Drag from one of the exit dots instead.",
                )
            )
        elif conn.exit not in labels:
            issues.append(
                error(
                    "unknown_exit",
                    f"A connection leaves from the exit “{conn.exit}”, which no longer exists.",
                    step=step.id,
                    hint="Reconnect it from one of the current exits.",
                )
            )
        elif conn.exit in connected:
            issues.append(
                error(
                    "exit_fan_out",
                    f"The exit “{conn.exit}” leads to more than one step. Each exit leads to one step.",
                    step=step.id,
                )
            )
        else:
            connected.add(conn.exit)
    for lab in labels:
        if lab not in connected:
            issues.append(
                warning(
                    "exit_unconnected",
                    f"The exit “{lab}” isn't connected; the run ends there.",
                    step=step.id,
                    setting="exits",
                )
            )
    return issues


def round_counter(step: Any) -> str:
    return f"{step.id}_rounds"


class DecisionHandler(StepHandler):
    type = "decision"
    label = "Decision"
    technical = "Router · conditional edge"
    category = "logic"
    icon = "split"
    summary = "Sends the flow down one of several paths, by a rule or by asking an AI."
    form = [
        FormField(
            key="mode",
            label="Decide by",
            kind="select",
            options=[
                {"value": "rules", "label": "Rules (checks on Flow Data)"},
                {"value": "ai", "label": "Asking an AI to pick an exit"},
            ],
            help="Rules are instant and free. An AI can judge meaning, like the tone of a message.",
        ),
        FormField(
            key="exits",
            label="Exits",
            kind="exits",
            help="Each exit is a labelled path. Rules are checked top to bottom; the first match wins.",
        ),
        FormField(
            key="otherwise",
            label="When nothing matches, take",
            kind="text",
            help="The name of the fallback exit.",
        ),
        FormField(
            key="model",
            label="Model",
            kind="model",
            show_if={"mode": "ai"},
            help="The AI that picks the exit.",
        ),
        FormField(
            key="input",
            label="Look at this field",
            kind="field",
            placeholder="From the previous step",
            show_if={"mode": "ai"},
            help="What the AI reads to decide. Empty means: whatever the previous step saved.",
        ),
        FormField(
            key="instructions",
            label="Guidance",
            kind="textarea",
            show_if={"mode": "ai"},
            example="Decide whether this customer message is a complaint.",
            help="Optional extra guidance for the AI.",
        ),
        FormField(
            key="save_as",
            label="Save the chosen exit as",
            kind="field_name",
            show_if={"mode": "ai"},
            advanced=True,
        ),
        FormField(
            key="max_rounds",
            label="Leave the loop after (rounds)",
            kind="number",
            min=1,
            max=1000,
            advanced=True,
            help="Stops a loop going round forever: after this many visits, take the exit below.",
            technical="counter field + check in the router",
        ),
        FormField(
            key="when_max",
            label="Then take",
            kind="text",
            advanced=True,
            placeholder="The fallback exit",
            help="The exit to take once the round limit is reached.",
        ),
    ]

    def exits(self, step: Any) -> list[str]:
        return [e.label for e in step.settings.exits] + [step.settings.otherwise]

    def system_fields(self, step: Any, an: Any) -> list[Any]:
        if not step.settings.max_rounds:
            return []
        return [
            FieldInfo(
                round_counter(step),
                "number",
                "replace",
                f"How many times “{step.name or step.id}” has run in this run.",
                private=True,
                reset=0,
            )
        ]

    def reads(self, step: Any, an: Any) -> set[str]:
        names = self._reads(step, an)
        if step.settings.max_rounds:
            names.add(round_counter(step))
        return names

    def primary_output(self, step: Any) -> str | None:
        return step.settings.save_as if step.settings.mode == "ai" else None

    def input_field(self, step: Any, an: Any) -> str | None:
        return step.settings.input or an.upstream_output(step.id)

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "text"} if step.settings.mode == "ai" else {}

    def _reads(self, step: Any, an: Any) -> set[str]:
        s = step.settings
        if s.mode == "ai":
            field = self.input_field(step, an)
            return {field} if field else set()
        names: set[str] = set()
        for ex in s.exits:
            cond = ex.when
            if cond is None:
                continue
            if cond.expression:
                with contextlib.suppress(ExpressionError):
                    names |= field_names(cond.expression)
            elif cond.field:
                names.add(cond.field)
        return names

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        labels = self.exits(step)
        lowered = [lab.strip().lower() for lab in labels]
        for lab in {lab for lab in lowered if lowered.count(lab) > 1}:
            issues.append(
                error(
                    "duplicate_exit",
                    f"Two exits are called “{lab}”. Exit names must differ.",
                    step=step.id,
                    setting="exits",
                )
            )
        available = an.available_fields(step.id)
        if s.mode == "ai":
            issues += check_model(step.id, s.model)
            if not s.exits:
                issues.append(
                    error(
                        "ai_no_exits",
                        "Add at least one exit for the AI to choose from.",
                        step=step.id,
                        setting="exits",
                    )
                )
            field = self.input_field(step, an)
            if not field:
                issues.append(
                    error(
                        "ai_no_input",
                        "Pick the field the AI should look at.",
                        step=step.id,
                        setting="input",
                    )
                )
            elif field not in available:
                issues.append(
                    missing_field_issue(step, field, an, "input", "This Decision looks at")
                )
        else:
            if not s.exits:
                issues.append(
                    warning(
                        "no_rules",
                        f"This Decision has no rules, so it always takes “{s.otherwise}”.",
                        step=step.id,
                        setting="exits",
                    )
                )
            for ex in s.exits:
                issues += self._check_condition(step, ex, an, available)

        issues += exit_connection_issues(step, labels, an)
        if s.when_max and s.when_max not in labels:
            issues.append(
                error(
                    "unknown_when_max",
                    f"“{s.when_max}” isn't one of this Decision's exits.",
                    step=step.id,
                    setting="when_max",
                    hint=f"Use one of: {', '.join(labels)}.",
                )
            )
        if s.when_max and not s.max_rounds:
            issues.append(
                warning(
                    "when_max_unused",
                    "Set a round limit too, or the loop exit is never used.",
                    step=step.id,
                    setting="max_rounds",
                )
            )
        return issues

    def _check_condition(self, step: Any, ex: Any, an: Any, available: set[str]) -> list[Issue]:
        cond = ex.when
        if cond is None:
            return [
                warning(
                    "exit_no_rule",
                    f"The exit “{ex.label}” has no rule, so it always matches.",
                    step=step.id,
                    setting="exits",
                )
            ]
        issues: list[Issue] = []
        if cond.expression:
            try:
                names = field_names(cond.expression)
            except ExpressionError as exc:
                return [
                    error(
                        "bad_expression", f"Exit “{ex.label}”: {exc}", step=step.id, setting="exits"
                    )
                ]
            for name in sorted(names):
                if name not in available:
                    issues.append(
                        missing_field_issue(step, name, an, "exits", f"Exit “{ex.label}” checks")
                    )
            return issues
        if not cond.field:
            return [
                error(
                    "rule_no_field",
                    f"Pick the field the exit “{ex.label}” should check.",
                    step=step.id,
                    setting="exits",
                )
            ]
        if cond.field not in available:
            issues.append(
                missing_field_issue(step, cond.field, an, "exits", f"Exit “{ex.label}” checks")
            )
        if cond.op not in NO_VALUE_OPS and (cond.value is None or cond.value == ""):
            issues.append(
                error(
                    "rule_no_value",
                    f"The exit “{ex.label}” needs a value to compare with.",
                    step=step.id,
                    setting="exits",
                )
            )
        elif cond.op in NUMBER_OPS and _number(cond.value) is None:
            issues.append(
                error(
                    "rule_not_number",
                    f"The exit “{ex.label}” compares with “{cond.value}”, which isn't a number.",
                    step=step.id,
                    setting="exits",
                )
            )
        elif cond.op == "matches":
            try:
                re.compile(str(cond.value))
            except re.error as exc:
                issues.append(
                    error(
                        "bad_pattern",
                        f"The pattern for exit “{ex.label}” isn't valid: {exc}.",
                        step=step.id,
                        setting="exits",
                    )
                )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        router = ctx.names.claim(f"route_{step.id}")
        name = step.name or step.id
        if s.mode == "ai":
            return self._emit_ai(step, ctx, fn, router)

        if s.max_rounds:
            counter = round_counter(step)
            node = (
                f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
                + docstring(
                    f"{self.title(step)}\n\nCounts the rounds in `{counter}`; {router} below picks the exit."
                )
                + f"\n    return {{{py_str(counter)}: data.get({py_str(counter)}, 0) + 1}}"
            )
        else:
            node = (
                f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
                + docstring(
                    f"{self.title(step)}\n\nChanges no data; {router} below picks the exit."
                )
                + "\n    return {}"
            )
        body = self._guard_lines(step) + conditions_body(s.exits, s.otherwise, ctx)
        router_code = (
            f"def {router}(data: {ctx.data_class}) -> str:\n"
            + docstring(f'Pick the exit for the Decision "{name}".')
            + "\n"
            + "\n".join(body)
        )
        return StepCode([node, router_code], node=fn, router=router)

    def _guard_lines(self, step: Any) -> list[str]:
        s = step.settings
        if not s.max_rounds:
            return []
        counter = round_counter(step)
        leave = s.when_max or s.otherwise
        return [
            f"    if data.get({py_str(counter)}, 0) >= {s.max_rounds}:  # round limit reached\n"
            f"        return {py_str(leave)}"
        ]

    def _emit_ai(self, step: Any, ctx: Any, fn: str, router: str) -> StepCode:
        s = step.settings
        field = self.input_field(step, ctx.an) or "input"
        pick = ctx.helper("pick_exit")
        exits_const = ctx.names.claim(f"{step.id}_exits".upper())
        guide_const = ctx.names.claim(f"{step.id}_guidance".upper())
        lines = [s.instructions.strip()] if s.instructions.strip() else []
        lines.append("Choose the exit that fits best. Reply with the exit name only.")
        lines.append("")
        lines.append("Exits:")
        for ex in s.exits:
            lines.append(f"- {ex.label}" + (f": {ex.description}" if ex.description else ""))
        lines.append(f"- {s.otherwise}: none of the above")
        guidance = "\n".join(lines)
        labels = [e.label for e in s.exits]
        consts = f"{exits_const} = {py_literal(labels)}\n{guide_const} = {py_str(guidance)}"
        ftype = ctx.field_type(field)
        if ftype == "messages":
            content = f'data[{py_str(field)}][-1].text if data.get({py_str(field)}) else ""'
        else:
            content = f'str(data.get({py_str(field)}, ""))'
        info = model_info(s.model)
        kwargs = {} if info is not None and not info.accepts_temperature else {"temperature": 0}
        model = ctx.model_call(s.model, kwargs)
        extra = ""
        if s.max_rounds:
            counter = py_str(round_counter(step))
            extra = f", {counter}: data.get({counter}, 0) + 1"
        node = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            + docstring(
                f"{self.title(step)}\n\nAsks {s.model} which exit fits `{field}` and saves it as `{s.save_as}`."
            )
            + "\n"
            f"    model = {model}\n"
            f'    reply = model.invoke([("system", {guide_const}), ("human", {content})])\n'
            f"    return {{{py_str(s.save_as)}: {pick}(reply.text, {exits_const}, {py_str(s.otherwise)}){extra}}}"
        )
        router_code = (
            f"def {router}(data: {ctx.data_class}) -> str:\n"
            + docstring(f'Follow the exit the AI picked for "{step.name or step.id}".')
            + "\n"
            + "".join(line + "\n" for line in self._guard_lines(step))
            + f"    return data.get({py_str(s.save_as)}) or {py_str(s.otherwise)}"
        )
        return StepCode([consts, node, router_code], node=fn, router=router)


class JumpHandler(StepHandler):
    type = "jump"
    label = "Jump"
    technical = "Command(update, goto)"
    category = "logic"
    icon = "corner-down-right"
    summary = "Sets some Flow Data and jumps to the next step in one move."
    beginner = False
    form = [
        FormField(
            key="updates",
            label="Set these fields",
            kind="field_updates",
            help="Each field gets a value: text with {field} placeholders, or (Pro) an expression.",
            example="count = count + 1",
        ),
        FormField(
            key="exits",
            label="Then go to",
            kind="exits",
            help="Rules are checked on the updated data, top to bottom; the first match wins.",
        ),
        FormField(
            key="otherwise",
            label="When nothing matches, take",
            kind="text",
            help="The name of the fallback exit.",
        ),
    ]

    def exits(self, step: Any) -> list[str]:
        return [e.label for e in step.settings.exits] + [step.settings.otherwise]

    def primary_output(self, step: Any) -> str | None:
        updates = step.settings.updates
        return updates[-1].field if updates else None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        out: dict[str, str] = {}
        for up in step.settings.updates:
            out[up.field] = "any" if up.expression else "text"
        return out

    def reads(self, step: Any, an: Any) -> set[str]:
        names: set[str] = set()
        for up in step.settings.updates:
            if up.expression:
                with contextlib.suppress(ExpressionError):
                    names |= field_names(up.expression)
            else:
                names |= set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", up.value))
        written = {up.field for up in step.settings.updates}
        for ex in step.settings.exits:
            cond = ex.when
            if cond is None:
                continue
            if cond.expression:
                with contextlib.suppress(ExpressionError):
                    names |= field_names(cond.expression) - written
            elif cond.field and cond.field not in written:
                names.add(cond.field)
        return names

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        decision = DecisionHandler()
        available = an.available_fields(step.id) | {up.field for up in s.updates}
        if not s.updates and not s.exits:
            issues.append(
                warning(
                    "jump_empty",
                    "This Jump sets nothing and has no rules; a plain connection does the same.",
                    step=step.id,
                )
            )
        seen: set[str] = set()
        for up in s.updates:
            if up.field in seen:
                issues.append(
                    error(
                        "jump_duplicate_field",
                        f"`{up.field}` is set twice. Set each field once.",
                        step=step.id,
                        setting="updates",
                    )
                )
            seen.add(up.field)
            if up.expression:
                try:
                    names = field_names(up.expression)
                except ExpressionError as exc:
                    issues.append(
                        error(
                            "bad_expression",
                            f"`{up.field}`: {exc}",
                            step=step.id,
                            setting="updates",
                        )
                    )
                    continue
                for name in sorted(names - an.available_fields(step.id)):
                    issues.append(
                        missing_field_issue(step, name, an, "updates", f"`{up.field}` uses")
                    )
            else:
                for name in re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", up.value):
                    if name not in an.available_fields(step.id):
                        issues.append(
                            missing_field_issue(step, name, an, "updates", f"`{up.field}` uses")
                        )
        for ex in s.exits:
            issues += decision._check_condition(step, ex, an, available)
        labels = self.exits(step)
        lowered = [lab.strip().lower() for lab in labels]
        for lab in {lab for lab in lowered if lowered.count(lab) > 1}:
            issues.append(
                error(
                    "duplicate_exit",
                    f"Two exits are called “{lab}”. Exit names must differ.",
                    step=step.id,
                    setting="exits",
                )
            )
        plain = [c for c in an.outgoing[step.id] if c.exit is None]
        if s.exits or not plain:
            issues += exit_connection_issues(step, labels, an, "Jump")
        elif len(an.outgoing[step.id]) > 1:
            issues.append(
                error(
                    "exit_fan_out",
                    "A Jump without rules leads to one step. Add rules to choose between several.",
                    step=step.id,
                )
            )
        return issues

    def _targets(self, step: Any, ctx: Any) -> dict[str, str]:
        from ..compiler.codegen import exit_targets

        targets = exit_targets(ctx.an, step.id)
        # A single plain connection works for a Jump with no rules.
        plain = [c for c in ctx.an.outgoing[step.id] if c.exit is None]
        if plain and not step.settings.exits:
            target = ctx.an.steps[plain[0].target]
            targets[step.settings.otherwise] = (
                "END" if target.type == "output" else py_str(plain[0].target)
            )
        return targets

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        ctx.imports.add_from("langgraph.types", "Command")
        targets = self._targets(step, ctx)
        lines: list[str] = []
        for up in s.updates:
            value = (
                compile_expression(up.expression)
                if up.expression
                else template_value(up.value, ctx)
            )
            lines.append(f"    data[{py_str(up.field)}] = {value}")
        what = ", ".join(f"`{up.field}`" for up in s.updates) or "nothing"
        doc = docstring(f"{self.title(step)}\n\nSets {what} (in order), then picks the next step.")
        body = [f"def {fn}(data: {ctx.data_class}) -> Command:", doc]
        if lines:
            names = ", ".join(py_str(up.field) for up in s.updates) + (
                "," if len(s.updates) == 1 else ""
            )
            body += [
                "    data = {**data}  # later fields see the earlier ones",
                *lines,
                f"    update = {{name: data[name] for name in ({names})}}",
            ]
        else:
            body.append("    update: dict[str, Any] = {}")
        body += conditions_body(
            s.exits,
            s.otherwise,
            ctx,
            result=lambda label: f"Command(update=update, goto={targets.get(label, 'END')})",
        )
        destinations = list(dict.fromkeys(targets.values()))
        kwargs = {
            "destinations": "("
            + ", ".join(destinations)
            + ("," if len(destinations) == 1 else "")
            + ")"
        }
        return StepCode(["\n".join(body)], node=fn, node_kwargs=kwargs)
