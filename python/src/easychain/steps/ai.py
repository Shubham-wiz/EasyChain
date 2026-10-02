"""AI Model and Instructions steps."""

from __future__ import annotations

import difflib
from typing import Any

from ..compiler.issues import Fix, Issue, error, warning
from ..compiler.pycode import RawCode, docstring, py_str
from ..compiler.schema_code import check_schema, emit_schema, spread_types
from ..compiler.templates import to_fstring_template, variables
from ..providers import PROVIDERS, model_info, split_model
from .base import FormField, StepCode, StepHandler


def check_model(step_id: str, model: str, setting: str = "model") -> list[Issue]:
    provider_id, name = split_model(model)
    if not provider_id or not name:
        return [
            error(
                "model_format",
                f"“{model}” isn't a model I recognise. Pick one from the list, or write provider:model.",
                step=step_id,
                setting=setting,
                hint="For example openai:gpt-4o-mini or anthropic:claude-haiku-4-5.",
            )
        ]
    if provider_id not in PROVIDERS:
        known = ", ".join(PROVIDERS)
        return [
            error(
                "unknown_provider",
                f"Easy Chain doesn't support the provider “{provider_id}” yet.",
                step=step_id,
                setting=setting,
                hint=f"Supported now: {known}.",
            )
        ]
    return []


def suggest_field(name: str, available: set[str]) -> str | None:
    matches = difflib.get_close_matches(name, sorted(available), n=1, cutoff=0.6)
    return matches[0] if matches else None


def missing_field_issue(step: Any, name: str, an: Any, setting: str, what: str) -> Issue:
    available = an.available_fields(step.id)
    guess = suggest_field(name, available | set(an.fields))
    hint = (
        f"Did you mean `{guess}`?"
        if guess
        else ("Connect a step that saves it earlier in the flow, or add it to Input.")
    )
    fix = None
    if guess:
        fix = Fix(
            "rename_variable", f"Use `{guess}`", {"setting": setting, "from": name, "to": guess}
        )
    level = "warning" if name in an.fields else "error"
    message = (
        f"{what} `{name}`, but no earlier step sets it."
        if name in an.fields
        else f"{what} `{name}`, which isn't a Flow Data field."
    )
    return Issue(level, "missing_field", message, step=step.id, setting=setting, hint=hint, fix=fix)


class AIModelHandler(StepHandler):
    type = "ai_model"
    label = "AI Model"
    technical = "Chat model · init_chat_model"
    category = "ai"
    icon = "sparkles"
    summary = "Sends text or a prompt to an AI model and saves the reply."
    form = [
        FormField(
            key="model",
            label="Model",
            kind="model",
            help="Which AI model answers. Each provider needs its own API key (Ollama runs locally).",
            example="openai:gpt-4o-mini",
        ),
        FormField(
            key="prompt",
            label="Send this field",
            kind="field",
            help="What the model reads. Usually the prompt from an Instructions step. Empty means: "
            "whatever the previous step saved.",
            placeholder="From the previous step",
            example="prompt",
        ),
        FormField(
            key="save_as",
            label="Save the reply as",
            kind="field_name",
            help="The Flow Data field that holds the reply, for later steps and the Output.",
            example="summary",
        ),
        FormField(
            key="output",
            label="Reply format",
            kind="schema",
            help="Free text, or a fixed set of fields (for example a sentiment, a score and the "
            "reasons) that later steps can use directly.",
            technical="with_structured_output · Pydantic",
        ),
        FormField(
            key="temperature",
            label="Creativity",
            kind="slider",
            min=0,
            max=2,
            step=0.1,
            help="Low is focused and repeatable; high is more varied. Empty uses the model's default.",
            technical="temperature",
            advanced=True,
        ),
        FormField(
            key="max_tokens",
            label="Longest reply (tokens)",
            kind="number",
            min=1,
            help="Stops the reply after this many tokens (about ¾ of a word each).",
            technical="max_tokens",
            advanced=True,
        ),
        FormField(
            key="reasoning_effort",
            label="Thinking effort",
            kind="select",
            options=[
                {"value": "", "label": "Model default"},
                {"value": "low", "label": "Low"},
                {"value": "medium", "label": "Medium"},
                {"value": "high", "label": "High"},
            ],
            help="For reasoning models (OpenAI): how hard to think before answering.",
            technical="reasoning_effort",
            advanced=True,
            pro=True,
        ),
        FormField(
            key="stop",
            label="Stop at",
            kind="string_list",
            help="The reply stops when the model writes any of these.",
            technical="stop sequences",
            advanced=True,
            pro=True,
        ),
        FormField(
            key="timeout",
            label="Time limit (seconds)",
            kind="number",
            min=1,
            technical="timeout",
            advanced=True,
            pro=True,
        ),
        FormField(
            key="max_retries",
            label="Retries",
            kind="number",
            min=0,
            help="How many times to retry when the provider has a hiccup.",
            technical="max_retries",
            advanced=True,
            pro=True,
        ),
        FormField(
            key="base_url",
            label="Custom endpoint",
            kind="text",
            placeholder="https://my-server/v1",
            help="Use an OpenAI-compatible server or a remote Ollama.",
            technical="base_url",
            advanced=True,
            pro=True,
        ),
        FormField(
            key="api_key",
            label="Key for the custom endpoint",
            kind="secret",
            help="The secret that holds this endpoint's key (Settings → API keys). Empty: the "
            "provider's usual key.",
            technical="api_key",
            advanced=True,
            pro=True,
            show_if={"base_url": "*"},
        ),
    ]

    def prompt_field(self, step: Any, an: Any) -> str | None:
        return step.settings.prompt or an.upstream_output(step.id)

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        target = step.settings.save_as
        if step.settings.output is not None:
            return {target: "object", **spread_types(step.settings.output)}
        if an.fields.get(target) is not None and an.fields[target].type == "messages":
            return {target: "messages"}
        return {target: "text"}

    def reads(self, step: Any, an: Any) -> set[str]:
        field = self.prompt_field(step, an)
        return {field} if field else set()

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues = check_model(step.id, s.model)
        if s.output is not None:
            issues += check_schema(step.id, s.output)
        provider_id, _ = split_model(s.model)
        info = model_info(s.model)
        if info is not None and not info.accepts_temperature and s.temperature is not None:
            issues.append(
                warning(
                    "temperature_unsupported",
                    f"{info.label} doesn't accept a creativity (temperature) setting.",
                    step=step.id,
                    setting="temperature",
                    fix=Fix("set_setting", "Clear it", {"key": "temperature", "value": None}),
                )
            )
        if (
            s.reasoning_effort
            and provider_id in PROVIDERS
            and not PROVIDERS[provider_id].supports_reasoning_effort
        ):
            issues.append(
                warning(
                    "reasoning_effort_unsupported",
                    f"Thinking effort only applies to {', '.join(p.label for p in PROVIDERS.values() if p.supports_reasoning_effort)} models.",
                    step=step.id,
                    setting="reasoning_effort",
                )
            )
        field = self.prompt_field(step, an)
        if not field:
            issues.append(
                error(
                    "no_prompt",
                    "This AI Model has nothing to read.",
                    step=step.id,
                    setting="prompt",
                    hint="Connect an Instructions step before it, or pick a field to send.",
                    fix=Fix(
                        "add_step",
                        "Add Instructions before it",
                        {"type": "instructions", "before": step.id},
                    ),
                )
            )
        elif field not in an.available_fields(step.id):
            issues.append(missing_field_issue(step, field, an, "prompt", "This step sends"))
        return issues

    def kwargs(self, step: Any) -> dict[str, Any]:
        s = step.settings
        kw: dict[str, Any] = {}
        for key in (
            "temperature",
            "max_tokens",
            "reasoning_effort",
            "timeout",
            "max_retries",
            "base_url",
        ):
            value = getattr(s, key)
            if value is not None and value != "":
                kw[key] = value
        if s.stop:
            kw["stop"] = list(s.stop)
        if s.api_key:
            kw["api_key"] = RawCode(f'os.environ.get({py_str(s.api_key)}, "")')
        return kw

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        field = self.prompt_field(step, ctx.an) or "prompt"
        ftype = ctx.field_type(field)
        if ftype in ("text", "messages", "file"):
            arg = f"data[{py_str(field)}]"
        elif ftype in ("list", "object"):
            arg = f"json.dumps(data[{py_str(field)}], ensure_ascii=False, default=str)"
        else:
            arg = f"str(data[{py_str(field)}])"
        if s.api_key:
            ctx.imports.add("os")
        if s.output is not None:
            return self._emit_structured(step, ctx, fn, field, arg)
        out_type = self.writes(step, ctx.an)[s.save_as]
        result = "[reply]" if out_type == "messages" else "reply.text"
        doc = docstring(
            f"{self.title(step)}\n\nSends `{field}` to {s.model} and saves the reply as `{s.save_as}`."
        )
        code = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            f"{doc}\n"
            f"    model = {ctx.model_call(s.model, self.kwargs(step))}\n"
            f"    reply = model.invoke({arg})\n"
            f"    return {{{py_str(s.save_as)}: {result}}}"
        )
        return StepCode([code], node=fn)

    def _emit_structured(self, step: Any, ctx: Any, fn: str, field: str, arg: str) -> StepCode:
        s = step.settings
        output = s.output
        class_name = ctx.names.claim(f"{''.join(p.capitalize() for p in step.id.split('_'))}Reply")
        what = output.description.strip() or f"The reply of “{step.name or step.id}”."
        classes = emit_schema(output, class_name, what, ctx)
        structured = f"model.with_structured_output({class_name})"
        if output.retries:
            ctx.imports.add_from("pydantic", "ValidationError")
            ctx.imports.add_from("langchain_core.exceptions", "OutputParserException")
            structured += (
                ".with_retry(\n"
                "        retry_if_exception_type=(ValidationError, OutputParserException),\n"
                f"        stop_after_attempt={output.retries + 1},\n"
                "    )"
            )
        spread = [f.name for f in output.fields] if output.spread else []
        if spread:
            values = [f"{py_str(s.save_as)}: result"] + [
                f"{py_str(n)}: result[{py_str(n)}]" for n in spread
            ]
            returned = "{" + ", ".join(values) + "}"
            if len(returned) > 70:
                returned = "{\n" + "".join(f"        {v},\n" for v in values) + "    }"
            tail = f"    result = reply.model_dump()\n    return {returned}"
        else:
            tail = f"    return {{{py_str(s.save_as)}: reply.model_dump()}}"
        spread_note = (
            f" Each field is also saved on its own: {', '.join(f'`{n}`' for n in spread)}."
            if spread
            else ""
        )
        doc = docstring(
            f"{self.title(step)}\n\nSends `{field}` to {s.model} and saves the reply, in the "
            f"{class_name} format, as `{s.save_as}`.{spread_note}"
        )
        code = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            f"{doc}\n"
            f"    model = {ctx.model_call(s.model, self.kwargs(step))}\n"
            f"    structured = {structured}\n"
            f"    reply = structured.invoke({arg})\n"
            "    if reply is None:\n"
            f'        raise ValueError("The model didn\'t reply in the {class_name} format.")\n'
            f"{tail}"
        )
        return StepCode([*classes, code], node=fn)


class InstructionsHandler(StepHandler):
    type = "instructions"
    label = "Instructions"
    technical = "Prompt template · ChatPromptTemplate"
    category = "ai"
    icon = "scroll-text"
    summary = "Writes the prompt for an AI Model, filling in {variables} from Flow Data."
    form = [
        FormField(
            key="system",
            label="Role and rules",
            kind="template",
            help="Tells the model who it is and how to behave (the system message).",
            example="You are a friendly support agent. Answer in two sentences.",
            technical="system message",
        ),
        FormField(
            key="user",
            label="Message",
            kind="template",
            help="The request itself. Put Flow Data in with {field}, for example {question}.",
            example="Summarise this page in 3 bullet points:\n\n{page}",
            technical="human message",
        ),
        FormField(
            key="history",
            label="Include chat history from",
            kind="field",
            help="Adds the conversation so far (a messages field) before the message.",
            example="messages",
            technical="MessagesPlaceholder",
            advanced=True,
        ),
        FormField(
            key="examples",
            label="Examples",
            kind="examples",
            help="Sample questions and ideal answers that show the model what you want.",
            technical="few-shot messages",
            advanced=True,
        ),
        FormField(
            key="save_as",
            label="Save the prompt as",
            kind="field_name",
            help="The field the next AI Model reads.",
            example="prompt",
            advanced=True,
        ),
    ]

    def template_vars(self, step: Any) -> list[str]:
        s = step.settings
        names: dict[str, None] = {}
        for text in [s.system, *(e.content for e in s.examples), s.user]:
            for name in variables(text):
                names.setdefault(name, None)
        return list(names)

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {step.settings.save_as: "messages"}

    def reads(self, step: Any, an: Any) -> set[str]:
        names = set(self.template_vars(step))
        if step.settings.history:
            names.add(step.settings.history)
        return names

    def check(self, step: Any, an: Any) -> list[Issue]:
        s = step.settings
        issues: list[Issue] = []
        if not s.system.strip() and not s.user.strip() and not s.history:
            issues.append(
                error(
                    "empty_instructions",
                    "These Instructions are empty.",
                    step=step.id,
                    setting="user",
                    hint="Write what you want the AI to do, for example: Summarise {page}.",
                )
            )
        available = an.available_fields(step.id)
        for name in self.template_vars(step):
            if name not in available:
                setting = "user" if name in variables(s.user) else "system"
                issues.append(
                    missing_field_issue(step, name, an, setting, "These instructions use")
                )
        if s.history:
            if s.history not in available:
                issues.append(
                    missing_field_issue(
                        step, s.history, an, "history", "The chat history comes from"
                    )
                )
            elif an.field_type(s.history) not in ("messages", "any"):
                issues.append(
                    error(
                        "history_not_messages",
                        f"Chat history must come from a messages field, and `{s.history}` holds {an.field_type(s.history)}.",
                        step=step.id,
                        setting="history",
                    )
                )
        return issues

    def emit(self, step: Any, ctx: Any) -> StepCode:
        s = step.settings
        fn = ctx.fn(step.id)
        ctx.imports.add_from("langchain_core.prompts", "ChatPromptTemplate")
        template_name = ctx.names.claim(f"{step.id}_template")
        constants: list[str] = []

        def text(role: str, value: str) -> str:
            literal = py_str(to_fstring_template(value))
            if "\n" not in literal:
                return literal
            # Multi-line text reads best as a module-level constant.
            const = ctx.names.claim(f"{step.id}_{role}".upper())
            constants.append(f"{const} = {literal}")
            return const

        messages: list[str] = []
        if s.system.strip():
            messages.append(f'("system", {text("system", s.system)})')
        for n, ex in enumerate(s.examples, start=1):
            role = "human" if ex.role == "user" else "ai"
            messages.append(f'("{role}", {text(f"example_{n}", ex.content)})')
        if s.history:
            ctx.imports.add_from("langchain_core.prompts", "MessagesPlaceholder")
            messages.append(f"MessagesPlaceholder({py_str(s.history)})")
        if s.user.strip():
            messages.append(f'("human", {text("message", s.user)})')
        rendered = ",\n".join("        " + m for m in messages)
        template = "\n\n".join(
            [
                *constants,
                f"{template_name} = ChatPromptTemplate.from_messages(\n    [\n{rendered},\n    ]\n)",
            ]
        )

        values = []
        for v in self.template_vars(step):
            if ctx.field_type(v) in ("list", "object"):
                # Lists and objects read better in a prompt as text than as Python reprs.
                values.append(f"{py_str(v)}: {ctx.helper('as_text')}(data.get({py_str(v)}))")
            else:
                values.append(f'{py_str(v)}: data.get({py_str(v)}, "")')
        if s.history:
            values.append(f"{py_str(s.history)}: data.get({py_str(s.history)}, [])")
        values_src = "{" + ", ".join(values) + "}"
        call = f"{template_name}.invoke({values_src})"
        if len(call) > 80:
            inner = ",\n".join(f"            {v}" for v in values)
            call = f"{template_name}.invoke(\n        {{\n{inner},\n        }}\n    )"
        used = ", ".join(f"{{{v}}}" for v in self.template_vars(step))
        detail = f"Fills in {used} and saves" if used else "Saves"
        doc = docstring(f"{self.title(step)}\n\n{detail} the prompt messages as `{s.save_as}`.")
        code = (
            f"def {fn}(data: {ctx.data_class}) -> dict[str, Any]:\n"
            f"{doc}\n"
            f"    prompt = {call}\n"
            f"    return {{{py_str(s.save_as)}: prompt.to_messages()}}"
        )
        return StepCode([template, code], node=fn)
