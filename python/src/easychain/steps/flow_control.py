"""Steps that shape a run: Ask a Human (interrupt), For Each (Send) and Sub-flow (subgraph)."""

from __future__ import annotations

from typing import Any

from ..compiler.analysis import EACH_ITEM, WHEN_DONE, FieldInfo
from ..compiler.issues import Issue, error, warning
from ..compiler.pycode import docstring, py_literal, py_str
from ..compiler.templates import secrets, variables
from .ai import missing_field_issue
from .base import FormField, StepCode, StepHandler, template_value
from .logic import exit_connection_issues

APPROVED = "Approved"
REJECTED = "Rejected"


# ── Ask a Human ──────────────────────────────────────────────────────────────


class AskHumanHandler(StepHandler):
    type = "ask_human"
    label = "Ask a Human"
    technical = "interrupt() · human in the loop"
    category = "people"
    icon = "user-check"
    summary = (
        "Pauses the run until a person approves, edits, answers or picks an option in the Inbox."
    )
    default_name = "Ask for approval"
    form = [
        FormField(
            key="kind",
            label="Ask them to",
            kind="select",
            options=[
                {"value": "approve", "label": "Approve or reject"},
                {"value": "edit", "label": "Edit a field, then approve or reject"},
                {"value": "answer", "label": "Type an answer"},
                {"value": "choose", "label": "Pick one of some options"},
            ],
            help="The run waits (for days if need be) until someone answers in the Inbox.",
        ),
        FormField(
            key="question",
            label="Question",
            kind="template",
            example="Send this reply to {email}?",
            help="What the person sees. Use {field} to show Flow Data in the question.",
        ),
        FormField(
            key="show",
            label="Show them",
            kind="field_multi",
            help="Fields shown next to the question, such as a draft to check.",
        ),
        FormField(
            key="field",
            label="Field they can edit",
            kind="field",
            show_if={"kind": "edit"},
            help="Their edited version replaces this field when they approve.",
        ),
        FormField(
            key="options",
            label="Options",
            kind="string_list",
            show_if={"kind": "choose"},
            help="Each option is also an exit, so the flow can go a different way for each.",
        ),
        FormField(
            key="save_as",
            label="Save the answer as",
            kind="field_name",
            help="Holds Approved or Rejected, the typed answer, or the chosen option. "
            "A comment goes in the same name with _comment added.",
        ),
        FormField(
            key="notify",
            label="Notify people when it pauses",
            kind="switch",
            advanced=True,
            help="Uses the notifications set up in Settings (webhook, Slack or email).",
        ),
    ]

    def exits(self, step: Any) -> list[str]:
        s = step.settings
        if s.kind in ("approve", "edit"):
            return [APPROVED, REJECTED]
        if s.kind == "choose":
            return list(dict.fromkeys(s.options))
        return []

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        s = step.settings
        out = {s.save_as: "text"}
        if s.kind != "answer":
            out[f"{s.save_as}_comment"] = "text"
        if s.kind == "edit" and s.field:
            out[s.field] = an.field_type(s.field) if an.fields.get(s.field) else "any"
        return out

    def reads(self, step: Any, an: Any) -> set[str]:
        s = step.settings
        names = set(variables(s.question)) | set(s.show)
        if s.kind == "edit" and s.field:
            names.add(s.field)
        return names

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        available = an.available_fields(step.id)
        if not s.question.strip():
            issues.append(
                error("no_question", "Write the question to ask.", step=step.id, setting="question")
            )
        if secrets(s.question):
            issues.append(
                error(
                    "secret_in_question",
                    "Secrets can't be shown in a question; the reviewer would see them.",
                    step=step.id,
                    setting="question",
                )
            )
        for name in variables(s.question):
            if name not in available:
                issues.append(missing_field_issue(step, name, an, "question", "The question uses"))
        for name in s.show:
            if name not in available:
                issues.append(missing_field_issue(step, name, an, "show", "This step shows"))
        if s.kind == "edit":
            if not s.field:
                issues.append(
                    error(
                        "no_edit_field",
                        "Pick the field the person can edit.",
                        step=step.id,
                        setting="field",
                    )
                )
            elif s.field not in available:
                issues.append(missing_field_issue(step, s.field, an, "field", "The person edits"))
        if s.kind == "choose":
            options = [o.strip() for o in s.options if o.strip()]
            if len(options) < 2:
                issues.append(
                    error(
                        "too_few_options",
                        "Add at least two options to choose from.",
                        step=step.id,
                        setting="options",
                    )
                )
            lowered = [o.lower() for o in options]
            if len(set(lowered)) != len(lowered):
                issues.append(
                    error(
                        "duplicate_option",
                        "Two options have the same name.",
                        step=step.id,
                        setting="options",
                    )
                )
            for option in options:
                if len(option) > 60:
                    issues.append(
                        error(
                            "long_option",
                            f"The option “{option[:30]}…” is longer than 60 characters.",
                            step=step.id,
                            setting="options",
                        )
                    )
        labels = self.exits(step)
        if labels:
            issues += exit_connection_issues(step, labels, an, "Ask a Human step")
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        ctx.imports.add_from("langgraph.types", "interrupt")
        ctx.module.has_interrupts = True
        if variables(s.question):
            question = f"{ctx.helper('fill')}({py_str(s.question)}, data)"
        else:
            question = py_str(s.question)
        if s.kind == "choose":
            question = f"problem + {question}"
        request = [
            f'            "step": {py_str(step.id)},',
            f'            "kind": {py_str(s.kind)},',
            f'            "question": {question},',
        ]
        if s.show:
            shown = ", ".join(f"{py_str(n)}: data.get({py_str(n)})" for n in s.show)
            request.append(f'            "show": {{{shown}}},')
        definitions: list[str] = []
        options_const = None
        if s.kind == "edit" and s.field:
            request.append(f'            "field": {py_str(s.field)},')
            request.append(f'            "value": data.get({py_str(s.field)}),')
        if s.kind == "choose":
            options_const = ctx.names.claim(f"{step.id}_options".upper())
            definitions.append(f"{options_const} = {py_literal(self.exits(step))}")
            request.append(f'            "options": {options_const},')

        save, comment = py_str(s.save_as), py_str(f"{s.save_as}_comment")
        what = {
            "approve": f'approves or rejects; saves "{APPROVED}" or "{REJECTED}" as `{s.save_as}`',
            "edit": f"edits `{s.field}` and approves or rejects; saves the decision as `{s.save_as}`",
            "answer": f"answers; saves the answer as `{s.save_as}`",
            "choose": f"picks an option; saves it as `{s.save_as}`. An answer that isn't one of "
            "the options is asked again",
        }[s.kind]
        head = [
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:",
            docstring(
                f"{self.title(step)}\n\nPauses the run (a LangGraph interrupt) until a person {what}."
            ),
        ]
        if s.kind == "choose":
            pick = ctx.helper("pick_option")
            lines = [
                *head,
                '    problem = ""  # says why when an answer has to be given again',
                "    while True:",
                "        answer = interrupt(",
                "            {",
                *("    " + line for line in request),
                "            }",
                "        )",
                "        if not isinstance(answer, dict):  # resumed with a bare value",
                '            answer = {"value": answer}',
                '        value = str(answer.get("value") or "")',
                f"        choice = {pick}(value, {options_const})",
                "        if choice is not None:",
                f'            return {{{save}: choice, {comment}: str(answer.get("comment") or "")}}',
                "        # Not one of the options: ask again (LangGraph matches answers to asks in order).",
                "        problem = f\"“{' '.join(value.split())[:60]}” isn't one of the options. \"",
            ]
            definitions.append("\n".join(lines))
            return self._with_router(step, ctx, definitions, fn, options_const)
        lines = [
            *head,
            "    answer = interrupt(",
            "        {",
            *request,
            "        }",
            "    )",
            "    if not isinstance(answer, dict):  # resumed with a bare value",
            '        answer = {"value": answer}',
        ]
        if s.kind == "answer":
            lines.append(f'    return {{{save}: str(answer.get("value") or "")}}')
        else:
            lines += [
                '    approved = answer.get("action", "approve") == "approve"',
                "    update: dict[str, Any] = {",
                f"        {save}: {py_str(APPROVED)} if approved else {py_str(REJECTED)},",
                f'        {comment}: str(answer.get("comment") or ""),',
                "    }",
            ]
            if s.kind == "edit" and s.field:
                lines += [
                    '    if approved and answer.get("value") is not None:',
                    f'        update[{py_str(s.field)}] = answer["value"]',
                ]
            lines.append("    return update")
        definitions.append("\n".join(lines))
        return self._with_router(step, ctx, definitions, fn, options_const)

    def _with_router(
        self, step: Any, ctx: Any, definitions: list[str], fn: str, options_const: str | None
    ) -> StepCode:
        s = step.settings
        save = py_str(s.save_as)
        if not self.exits(step):
            return StepCode(definitions, node=fn)
        router = ctx.names.claim(f"route_{step.id}")
        if s.kind == "choose":
            pick_line = f"    return data.get({save}) or {options_const}[0]"
        else:
            pick_line = f"    return {py_str(APPROVED)} if data.get({save}) == {py_str(APPROVED)} else {py_str(REJECTED)}"
        definitions.append(
            f"def {router}(data: {ctx.data_class}) -> str:\n"
            + docstring(f'Follow the answer given at "{step.name or step.id}".')
            + "\n"
            + pick_line
        )
        return StepCode(definitions, node=fn, router=router)


# ── For Each ─────────────────────────────────────────────────────────────────


def results_field(step: Any) -> str:
    return f"{step.id}_results"


def index_field(step: Any) -> str:
    return f"{step.id}_index"


def done_node(step: Any) -> str:
    return f"{step.id}__done"


class ForEachHandler(StepHandler):
    type = "for_each"
    label = "For Each"
    technical = "Map-reduce · Send"
    category = "logic"
    icon = "repeat"
    summary = "Runs a step once for every item in a list, side by side, and collects the results."
    default_name = "For each item"
    form = [
        FormField(
            key="items",
            label="Go through",
            kind="field",
            placeholder="From the previous step",
            help="A list field. Empty means: whatever the previous step saved.",
        ),
        FormField(
            key="item_name",
            label="Call each item",
            kind="field_name",
            help="The step connected to “Each item” reads the current item from this field.",
        ),
        FormField(
            key="save_as",
            label="Save the results as",
            kind="field_name",
            help="A list with what the step produced for each item, in the original order.",
        ),
        FormField(
            key="concurrency",
            label="At most this many at once",
            kind="number",
            min=1,
            max=100,
            advanced=True,
            help="Lower it if a service limits how fast you can call it.",
            technical="max_concurrency",
        ),
    ]

    def exits(self, step: Any) -> list[str]:
        return [EACH_ITEM, WHEN_DONE]

    def items_field(self, step: Any, an: Any) -> str | None:
        return step.settings.items or an.upstream_output(step.id)

    def body(self, step: Any, an: Any) -> str | None:
        bodies = [c.target for c in an.outgoing[step.id] if c.exit == EACH_ITEM]
        return bodies[0] if bodies else None

    def system_fields(self, step: Any, an: Any) -> list[Any]:
        label = step.name or step.id
        return [
            FieldInfo(
                results_field(step),
                "list",
                "append",
                f"Results collected by “{label}” while it runs.",
                private=True,
                reducer="collect_items",
            ),
            FieldInfo(
                index_field(step),
                "number",
                "replace",
                f"The item “{label}” is working on.",
                private=True,
            ),
        ]

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.item_name: "any", step.settings.save_as: "list"}

    def reads(self, step: Any, an: Any) -> set[str]:
        field = self.items_field(step, an)
        return {field} if field else set()

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        field = self.items_field(step, an)
        if not field:
            issues.append(
                error("no_items", "Pick the list to go through.", step=step.id, setting="items")
            )
        elif field not in an.available_fields(step.id):
            issues.append(missing_field_issue(step, field, an, "items", "This step goes through"))
        elif an.field_type(field) not in ("list", "any"):
            issues.append(
                warning(
                    "items_not_list",
                    f"`{field}` is {an.field_type(field)}, not a list, so there is one item: the whole value.",
                    step=step.id,
                    setting="items",
                    hint="Use a Code step to split it into a list first.",
                )
            )
        if s.item_name == s.save_as:
            issues.append(
                error(
                    "item_is_result",
                    "The item and the results need different names.",
                    step=step.id,
                    setting="save_as",
                )
            )
        bodies = [c.target for c in an.outgoing[step.id] if c.exit == EACH_ITEM]
        if not bodies:
            issues.append(
                error(
                    "foreach_no_body",
                    "Connect the “Each item” exit to the step to run for every item.",
                    step=step.id,
                )
            )
        elif len(bodies) > 1:
            issues.append(
                error(
                    "exit_fan_out",
                    "“Each item” leads to one step. To do several things per item, use a Sub-flow.",
                    step=step.id,
                )
            )
        else:
            issues += self._check_body(step, an.steps[bodies[0]], an)
        issues += exit_connection_issues(step, self.exits(step), an, "For Each step")
        return issues

    def _check_body(self, step: Any, body: Any, an: Any) -> list[Issue]:
        handler = an.handlers[body.id]
        name = body.name or body.id
        issues: list[Issue] = []
        if not handler.has_node or handler.exits(body) or body.type == "for_each":
            issues.append(
                error(
                    "foreach_bad_body",
                    f"“{name}” can't run once per item. Pick an action, AI or Sub-flow step.",
                    step=step.id,
                    hint="To choose between paths per item, put the Decision in a Sub-flow.",
                )
            )
        if an.foreach_body.get(body.id) != step.id:
            issues.append(
                error(
                    "foreach_shared_body",
                    f"“{name}” already runs for another For Each.",
                    step=step.id,
                )
            )
        others = [
            c for c in an.incoming[body.id] if not (c.source == step.id and c.exit == EACH_ITEM)
        ]
        if others:
            issues.append(
                error(
                    "foreach_body_entered",
                    f"Only this For Each may lead into “{name}”, because it runs once per item.",
                    step=body.id,
                )
            )
        if an.outgoing[body.id]:
            issues.append(
                error(
                    "foreach_body_leads_on",
                    f"“{name}” runs once per item, so it can't lead on. Connect “When done” instead.",
                    step=body.id,
                    hint="Move this step's outgoing connection to the For Each's “When done” exit.",
                )
            )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        an = ctx.an
        fn = ctx.fn(step.id)
        router = ctx.names.claim(f"send_{step.id}")
        done_fn = ctx.names.claim(f"{step.id}_done")
        ctx.imports.add_from("langgraph.types", "Send")
        ctx.helper("collect_items")
        field = self.items_field(step, an) or "items"
        body = self.body(step, an)
        label = step.name or step.id
        results, index, done = results_field(step), index_field(step), done_node(step)

        node = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            + docstring(
                f"{self.title(step)}\n\nStarts a new list of results; {router} below sends each item of "
                f"`{field}` to {f'`{body}`' if body else 'the step connected to Each item'}."
            )
            + f"\n    return {{{py_str(results)}: None}}  # None starts the list again"
        )
        lines = [
            f"def {router}(data: {ctx.data_class}) -> list[Send] | str:",
            docstring(
                f"Send every item of `{field}` to its own run of the step (they run side by side)."
            ),
            f"    items = data.get({py_str(field)})",
            "    if items is None:",
            "        items = []",
            "    elif not isinstance(items, list):",
            "        items = [items]",
        ]
        if body:
            lines += [
                "    if not items:",
                f"        return {py_str(done)}",
                "    return [",
                f"        Send({py_str(body)}, {{**data, {py_str(s.item_name)}: item, {py_str(index)}: number}})",
                "        for number, item in enumerate(items)",
                "    ]",
            ]
        else:
            lines.append(f"    return {py_str(done)}")
        definitions = [node, "\n".join(lines)]

        if body:
            body_step = an.steps[body]
            body_handler = an.handlers[body]
            wrapper = ctx.names.claim(f"{body}_for_{step.id}")
            primary = body_handler.primary_output(body_step)
            kept = f"result.get({py_str(primary)})" if primary else "result"
            what = f"`{primary}`" if primary else "the fields it sets"
            is_async = body_handler.is_async(body_step, an)
            call = f"await {ctx.fn(body)}(data)" if is_async else f"{ctx.fn(body)}(data)"
            definitions.append(
                f"{'async def' if is_async else 'def'} {wrapper}(data: {ctx.data_class}) -> dict[str, Any]:\n"
                + docstring(f"Run `{body}` for one item of “{label}” and keep {what}.")
                + f"\n    result = {call} or {{}}\n"
                f"    return {{{py_str(results)}: [(data[{py_str(index)}], {kept})]}}"
            )
            ctx.node_override[body] = wrapper
            ctx.edge_override[body] = [f"builder.add_edge({py_str(body)}, {py_str(done)})"]

        definitions.append(
            f"def {done_fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            + docstring(
                f"Put the results of “{label}” in item order and save them as `{s.save_as}`."
            )
            + f"\n    collected = sorted(data.get({py_str(results)}) or [], key=lambda pair: pair[0])\n"
            f"    return {{{py_str(s.save_as)}: [result for _, result in collected]}}"
        )
        targets = [py_str(done)] if not body else [py_str(body), py_str(done)]
        if body:
            wiring = [
                f"builder.add_conditional_edges({py_str(step.id)}, {router}, [{', '.join(targets)}])"
            ]
        else:
            wiring = [f"builder.add_edge({py_str(step.id)}, {py_str(done)})"]
        after = [c for c in an.outgoing[step.id] if c.exit == WHEN_DONE]
        if after:
            target = an.steps[after[0].target]
            goto = "END" if target.type == "output" else py_str(target.id)
        else:
            goto = "END"
        wiring.append(f"builder.add_edge({py_str(done)}, {goto})")
        return StepCode(
            definitions,
            node=fn,
            wiring=wiring,
            extra_nodes=[f"builder.add_node({py_str(done)}, {done_fn}, defer=True)"],
        )


# ── Sub-flow ─────────────────────────────────────────────────────────────────


def flow_is_async(an: Any) -> bool:
    """Whether a flow has steps that need ``await graph.ainvoke`` (time limits)."""
    for sid in an.reachable:
        step = an.steps[sid]
        if step.run.timeout:
            return True
        if step.type == "subflow":
            child = an.child(step.settings.flow)
            if child is not None and flow_is_async(child):
                return True
    return False


class SubflowHandler(StepHandler):
    type = "subflow"
    label = "Sub-flow"
    technical = "Subgraph"
    category = "logic"
    icon = "layers"
    summary = "Runs another flow as one step, so a big flow can be built from small ones."
    default_name = "Run another flow"
    form = [
        FormField(
            key="flow",
            label="Flow to run",
            kind="flow",
            help="Any flow in this workspace. Double-click the step to open it.",
        ),
        FormField(
            key="share_data",
            label="Share Flow Data",
            kind="switch",
            help="On: the sub-flow reads and writes this flow's fields directly (same names). "
            "Off: it gets its own Flow Data, with the inputs and results mapped below.",
        ),
        FormField(
            key="inputs",
            label="Give it",
            kind="key_value",
            show_if={"share_data": False},
            example="url: {page_url}",
            help="Its input field, and the value to give it. Empty means: fields with the same name.",
        ),
        FormField(
            key="outputs",
            label="Save its results as",
            kind="key_value",
            show_if={"share_data": False},
            example="summary: summary",
            help="A field here, and the sub-flow output it comes from. Empty means: the same names.",
        ),
    ]

    def child(self, step: Any, an: Any) -> Any:
        return an.child(step.settings.flow)

    def inputs(self, step: Any, child: Any, an: Any) -> dict[str, str]:
        """Sub-flow input -> value. Empty settings mean: same-named fields this flow has."""
        if step.settings.inputs:
            return dict(step.settings.inputs)
        available = an.available_fields(step.id)
        return {
            name: f"{{{name}}}"
            for name, info in child.fields.items()
            if info.is_input and name in available
        }

    def outputs(self, step: Any, child: Any) -> dict[str, str]:
        if step.settings.outputs:
            return dict(step.settings.outputs)
        return {name: name for name in child.output_fields()}

    def shared_outputs(self, child: Any) -> list[str]:
        names = child.output_fields()
        if names:
            return names
        return [n for n, info in child.fields.items() if not info.private]

    def is_async(self, step: Any, an: Any) -> bool:
        child = self.child(step, an)
        return bool(child is not None and not step.settings.share_data and flow_is_async(child))

    def primary_output(self, step: Any) -> str | None:
        outputs = step.settings.outputs
        return next(iter(outputs)) if outputs else None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        child = self.child(step, an)
        if child is None:
            return dict.fromkeys(step.settings.outputs, "any")
        if step.settings.share_data:
            return {name: child.field_type(name) for name in self.shared_outputs(child)}
        return {
            mine: child.field_type(theirs) for mine, theirs in self.outputs(step, child).items()
        }

    def reads(self, step: Any, an: Any) -> set[str]:
        child = self.child(step, an)
        if child is None:
            return {n for v in step.settings.inputs.values() for n in variables(v)}
        if step.settings.share_data:
            return {name for name, info in child.fields.items() if info.is_input}
        return {n for v in self.inputs(step, child, an).values() for n in variables(v)}

    def check(self, step: Any, an: Any) -> list[Issue]:
        from ..compiler.validate import validate

        s = step.settings
        if not s.flow:
            return [error("subflow_no_flow", "Pick the flow to run.", step=step.id, setting="flow")]
        if an.includes_itself(s.flow):
            return [
                error(
                    "subflow_cycle",
                    "This would run a flow inside itself, which never ends.",
                    step=step.id,
                    setting="flow",
                    hint="Pick a different flow.",
                )
            ]
        child = self.child(step, an)
        if child is None:
            return [
                error(
                    "subflow_missing",
                    f"There's no flow called “{s.flow}” in this workspace.",
                    step=step.id,
                    setting="flow",
                )
            ]
        issues: list[Issue] = []
        problems = [i for i in validate(child.spec, analysis=child) if i.level == "error"]
        if problems:
            issues.append(
                error(
                    "subflow_has_errors",
                    f"The flow “{child.spec.name}” has {len(problems)} problem"
                    f"{'s' if len(problems) > 1 else ''} to fix first: {problems[0].message}",
                    step=step.id,
                    setting="flow",
                    hint="Open it (double-click this step) to fix it.",
                )
            )
        if child.chat and not an.chat and not s.share_data:
            issues.append(
                warning(
                    "subflow_chat",
                    f"“{child.spec.name}” is a chat flow; it gets the `messages` field from here.",
                    step=step.id,
                    setting="flow",
                )
            )
        available = an.available_fields(step.id)
        required = [
            f.name
            for f in (child.input_step.settings.fields if child.input_step else [])
            if f.required and f.default is None
        ]
        if s.share_data:
            for name in required:
                if name not in available:
                    issues.append(missing_field_issue(step, name, an, "flow", "The sub-flow needs"))
            for name in self.shared_outputs(child):
                info = an.fields.get(name)
                if info and info.update in ("append", "add") and info.type != "messages":
                    issues.append(
                        warning(
                            "subflow_shared_append",
                            f"`{name}` adds new values to old ones, and a shared sub-flow hands back "
                            "the whole list, so items would be added twice.",
                            step=step.id,
                            setting="share_data",
                            hint="Switch off sharing and map the result, or use the update rule replace.",
                        )
                    )
            for field in child.input_step.settings.fields if child.input_step else []:
                if field.default is not None and field.name not in available:
                    issues.append(
                        warning(
                            "subflow_default_unused",
                            f"With shared Flow Data, the sub-flow's default for `{field.name}` isn't used.",
                            step=step.id,
                            setting="share_data",
                            hint=f"Set `{field.name}` earlier in this flow, or switch off sharing.",
                        )
                    )
            return issues
        child_inputs = {n for n, info in child.fields.items() if info.is_input}
        for name, value in self.inputs(step, child, an).items():
            if name not in child_inputs:
                issues.append(
                    error(
                        "subflow_unknown_input",
                        f"“{child.spec.name}” has no input called `{name}`.",
                        step=step.id,
                        setting="inputs",
                        hint=f"Its inputs: {', '.join(sorted(child_inputs)) or 'none'}.",
                    )
                )
            for var in variables(value):
                if var not in available:
                    issues.append(
                        missing_field_issue(step, var, an, "inputs", f"The input `{name}` uses")
                    )
        given = self.inputs(step, child, an)
        for name in required:
            if name not in given:
                issues.append(
                    warning(
                        "subflow_input_missing",
                        f"“{child.spec.name}” needs `{name}`, but this step doesn't give it.",
                        step=step.id,
                        setting="inputs",
                    )
                )
        for theirs in self.outputs(step, child).values():
            if theirs not in child.fields:
                issues.append(
                    error(
                        "subflow_unknown_output",
                        f"“{child.spec.name}” doesn't produce `{theirs}`.",
                        step=step.id,
                        setting="outputs",
                        hint=f"It returns: {', '.join(child.output_fields()) or 'all its Flow Data'}.",
                    )
                )
            elif child.output_fields() and theirs not in child.output_fields():
                issues.append(
                    warning(
                        "subflow_output_hidden",
                        f"“{child.spec.name}” doesn't return `{theirs}`; tick it on its Output step.",
                        step=step.id,
                        setting="outputs",
                    )
                )
        if not self.outputs(step, child):
            issues.append(
                warning(
                    "subflow_no_outputs",
                    f"Nothing from “{child.spec.name}” is saved here.",
                    step=step.id,
                    setting="outputs",
                    hint="Map at least one of its results to a field.",
                )
            )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        parts = ctx.subflow(s.flow) if s.flow else None
        if parts is None:
            message = (
                f"The sub-flow “{s.flow}” can't be found." if s.flow else "Pick the flow to run."
            )
            return StepCode(
                [
                    f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
                    + docstring(self.title(step))
                    + f"\n    raise RuntimeError({py_str(message)})"
                ],
                node=fn,
            )
        child = parts.analysis
        if s.share_data:
            ctx.node_override[step.id] = parts.graph_var
            return StepCode(
                [
                    f"# {self.title(step)}: runs {parts.graph_var} (the flow “{child.spec.name}”) "
                    "on this flow's data."
                ],
                node=parts.graph_var,
            )
        is_async = self.is_async(step, ctx.an)
        values = [
            f"{py_str(name)}: {template_value(value, ctx)}"
            for name, value in self.inputs(step, child, ctx.an).items()
        ]
        if parts.defaults_var:
            values.insert(0, f"**{parts.defaults_var}")
        payload = "{" + ", ".join(values) + "}"
        call = f"await {parts.graph_var}.ainvoke" if is_async else f"{parts.graph_var}.invoke"
        result_items = [
            f"{py_str(mine)}: result.get({py_str(theirs)})"
            for mine, theirs in self.outputs(step, child).items()
        ]
        saved = ", ".join(f"`{m}`" for m in self.outputs(step, child)) or "nothing"
        head = (
            f"{'async def' if is_async else 'def'} {fn}(data: {ctx.data_class}) -> dict[str, Any]:"
        )
        invoke = f"    result = {call}({payload})"
        if len(invoke) > 92:
            inner = "".join(f"            {v},\n" for v in values)
            invoke = f"    result = {call}(\n        {{\n{inner}        }}\n    )"
        returned = "{" + ", ".join(result_items) + "}"
        code = (
            head
            + "\n"
            + docstring(
                f"{self.title(step)}\n\nRuns the flow “{child.spec.name}” with its own Flow Data and saves {saved}."
            )
            + "\n"
            + invoke
            + "\n"
            + f"    return {returned}"
        )
        return StepCode([code], node=fn, is_async=is_async)


def subflow_ids(spec: Any) -> list[str]:
    """Ids of the flows a flow uses directly as Sub-flows."""
    return list(
        dict.fromkeys(
            s.settings.flow for s in spec.steps if s.type == "subflow" and s.settings.flow
        )
    )
