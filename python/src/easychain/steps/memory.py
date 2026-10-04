"""The Memory step: long-term memory (remember, recall) and short-term chat upkeep (trim, summarise)."""

from __future__ import annotations

from typing import Any

from ..compiler.issues import Issue, error
from ..compiler.pycode import docstring, py_str
from .ai import check_model, missing_field_issue
from .base import FormField, StepCode, StepHandler

LABELS = {
    "remember": "Remember",
    "recall": "Recall",
    "trim": "Trim chat history",
    "summarise": "Summarise chat history",
}


class MemoryHandler(StepHandler):
    type = "memory"
    label = "Memory"
    technical = "LangGraph store · trim_messages"
    category = "knowledge"
    icon = "brain"
    summary = "Remembers facts about a user across conversations, or keeps a long chat short."
    form = [
        FormField(
            key="action",
            label="What to do",
            kind="select",
            options=[
                {"value": "recall", "label": "Recall what I know about the user"},
                {"value": "remember", "label": "Remember something about the user"},
                {"value": "trim", "label": "Keep only the latest chat messages"},
                {"value": "summarise", "label": "Summarise older chat messages"},
            ],
            help="Remember and Recall keep facts for later conversations. Trim and Summarise keep "
            "a long chat within the model's limits.",
        ),
        FormField(
            key="text",
            label="Text",
            kind="field",
            help="Remember: the field to save. Recall: what to match (the question). Empty means: "
            "whatever the previous step saved.",
            placeholder="From the previous step",
            show_if={"action": ["remember", "recall"]},
        ),
        FormField(
            key="user",
            label="User id from",
            kind="field",
            help="Memories belong to one user. Empty: the `user_id` field when there is one, "
            "otherwise the conversation.",
            placeholder="user_id, or the conversation",
            show_if={"action": ["remember", "recall"]},
            advanced=True,
        ),
        FormField(
            key="limit",
            label="Facts to recall",
            kind="number",
            min=1,
            show_if={"action": "recall"},
        ),
        FormField(
            key="messages",
            label="Chat field",
            kind="field",
            show_if={"action": ["trim", "summarise"]},
        ),
        FormField(
            key="keep",
            label="Recent messages to keep",
            kind="number",
            min=1,
            show_if={"action": ["trim", "summarise"]},
        ),
        FormField(
            key="model",
            label="Model",
            kind="model",
            show_if={"action": "summarise"},
        ),
        FormField(
            key="save_as",
            label="Save as",
            kind="field_name",
            help="Recall: the facts, one per line. Summarise: the summary, for your Instructions.",
            show_if={"action": ["recall", "summarise"]},
        ),
    ]

    def title(self, step: Any) -> str:
        return " ".join(f"Memory · {step.name or LABELS[step.settings.action]}".split())

    def text_field(self, step: Any, an: Any) -> str | None:
        if step.settings.text:
            return step.settings.text
        if step.id in an.tool_of:
            return "fact" if step.settings.action == "remember" else "query"
        return an.upstream_output(step.id)

    def primary_output(self, step: Any) -> str | None:
        return step.settings.save_as if step.settings.action in ("recall", "summarise") else None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        s = step.settings
        if s.action == "recall":
            return {s.save_as: "text"}
        if s.action == "summarise":
            return {s.save_as: "text", s.messages: "messages"}
        if s.action == "trim":
            return {s.messages: "messages"}
        return {}

    def reads(self, step: Any, an: Any) -> set[str]:
        s = step.settings
        if s.action in ("trim", "summarise"):
            return {s.messages}
        names = set()
        field = self.text_field(step, an)
        if field:
            names.add(field)
        if s.user:
            names.add(s.user)
        elif "user_id" in an.available_fields(step.id) and step.id not in an.tool_of:
            names.add("user_id")
        return names

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        available = an.available_fields(step.id)
        if s.action in ("remember", "recall"):
            field = self.text_field(step, an)
            if not field:
                issues.append(
                    error(
                        "memory_no_text",
                        "There's nothing to remember yet."
                        if s.action == "remember"
                        else "There's nothing to match.",
                        step=step.id,
                        setting="text",
                        hint="Pick the field, or connect a step before this one.",
                    )
                )
            elif field not in available:
                issues.append(missing_field_issue(step, field, an, "text", "This step uses"))
            if s.user and s.user not in available:
                issues.append(
                    missing_field_issue(step, s.user, an, "user", "The user id comes from")
                )
        else:
            if s.messages not in available:
                issues.append(
                    missing_field_issue(step, s.messages, an, "messages", "This step trims")
                )
            elif an.field_type(s.messages) not in ("messages", "any"):
                issues.append(
                    error(
                        "memory_not_messages",
                        f"`{s.messages}` isn't a chat (messages) field.",
                        step=step.id,
                        setting="messages",
                    )
                )
            if s.action == "summarise":
                issues += check_model(step.id, s.model)
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        an = ctx.an
        if s.action in ("trim", "summarise"):
            return self._emit_chat(step, ctx, fn)
        ctx.helper("memory")
        ctx.module.uses_store = True
        field = self.text_field(step, an) or "text"
        user = s.user or ("user_id" if "user_id" in an.available_fields(step.id) else None)
        who = f"data.get({py_str(user)})" if user else ""
        if ctx.field_type(field) == "messages":
            value = f'data[{py_str(field)}][-1].text if data.get({py_str(field)}) else ""'
        else:
            value = f'str(data.get({py_str(field)}) or "")'
        owner = f"`{user}`" if user else "this conversation"
        if s.action == "remember":
            doc = docstring(
                f"{self.title(step)}\n\nSaves `{field}` to long-term memory for {owner} (LangGraph store)."
            )
            body = [
                f"    remember_fact(memory_namespace({who}), {value})",
                "    return {}",
            ]
        else:
            doc = docstring(
                f"{self.title(step)}\n\nLooks up what was remembered for {owner} that fits `{field}`, "
                f"and saves up to {s.limit} facts as `{s.save_as}`."
            )
            body = [
                f"    facts = recall_facts(memory_namespace({who}), {value}, limit={s.limit})",
                f'    return {{{py_str(s.save_as)}: "\\n".join(f"- {{fact}}" for fact in facts)}}',
            ]
        code = f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n" + "\n".join(body)
        return StepCode([code], node=fn)

    def _emit_chat(self, step: Any, ctx: Any, fn: str) -> StepCode:
        s = step.settings
        info = ctx.an.fields.get(s.messages)
        appends = info is not None and info.update == "append"
        msgs = py_str(s.messages)
        lines = [f"    messages = data.get({msgs}, [])", f"    if len(messages) <= {s.keep}:"]
        lines.append("        return {}")
        if appends:
            # With an append rule, old messages are removed by id (LangGraph RemoveMessage).
            ctx.imports.add_from("langchain_core.messages", "RemoveMessage")
            lines.append(f"    older = messages[:-{s.keep}]")
            update = "[RemoveMessage(id=m.id) for m in older]"
        else:
            lines.append(f"    older, recent = messages[:-{s.keep}], messages[-{s.keep}:]")
            update = "recent"
        if s.action == "summarise":
            model = ctx.model_call(s.model, {})
            lines += [
                f"    model = {model}",
                '    text = "\\n".join(f"{m.type}: {m.text}" for m in older)',
                "    summary = model.invoke(",
                '        "Summarise this conversation so far in a few sentences, keeping names, "',
                '        "numbers and decisions:\\n\\n" + text',
                "    ).text",
                f"    return {{{py_str(s.save_as)}: summary, {msgs}: {update}}}",
            ]
            what = (
                f"When `{s.messages}` has more than {s.keep} messages, summarises the older ones "
                f"with {s.model} into `{s.save_as}` and keeps the latest {s.keep}."
            )
        else:
            lines.append(f"    return {{{msgs}: {update}}}")
            what = f"Keeps only the latest {s.keep} messages of `{s.messages}`."
        doc = docstring(f"{self.title(step)}\n\n{what}")
        code = f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n{doc}\n" + "\n".join(lines)
        return StepCode([code], node=fn)
