"""Input and Output steps: where a run starts and what it returns."""

from __future__ import annotations

from typing import Any

from ..compiler.issues import Fix, Issue, error, warning
from .base import FormField, StepHandler

_FIELD_TYPES = [
    {"value": "text", "label": "Text"},
    {"value": "number", "label": "Number"},
    {"value": "yes_no", "label": "Yes/No"},
    {"value": "list", "label": "List"},
    {"value": "object", "label": "Object"},
    {"value": "file", "label": "File"},
]


class InputHandler(StepHandler):
    type = "input"
    label = "Input"
    technical = "START + input schema"
    category = "start_end"
    icon = "log-in"
    summary = "Where a run starts. Lists what the flow needs, like a URL or a question."
    has_node = False
    form = [
        FormField(
            key="mode",
            label="Kind of flow",
            kind="select",
            options=[
                {"value": "form", "label": "Form: takes named fields"},
                {"value": "chat", "label": "Chat: takes messages"},
            ],
            help="Chat flows remember the conversation in the `messages` field and run in the chat panel.",
        ),
        FormField(
            key="fields",
            label="Fields",
            kind="input_fields",
            help="What a person (or another app) fills in to start a run. Chat flows can add "
            "extra fields too.",
            example="url (Text): The page to summarise",
            options=_FIELD_TYPES,
        ),
    ]

    def primary_output(self, step: Any) -> str | None:
        fields = step.settings.fields
        return fields[0].name if fields else None

    def writes(self, step: Any, an: Any) -> dict[str, str]:
        return {}

    def check(self, step: Any, an: Any) -> list[Issue]:
        issues: list[Issue] = []
        names = [f.name for f in step.settings.fields]
        for name in {n for n in names if names.count(n) > 1}:
            issues.append(
                error(
                    "duplicate_input",
                    f"Two input fields are called `{name}`.",
                    step=step.id,
                    setting="fields",
                )
            )
        if step.settings.mode == "form" and not names:
            issues.append(
                warning(
                    "no_input_fields",
                    "This flow takes no inputs.",
                    step=step.id,
                    setting="fields",
                    hint="Add a field such as `question` so a run has something to work on.",
                )
            )
        if step.settings.mode == "chat" and "messages" in names:
            issues.append(
                error(
                    "messages_reserved",
                    "Chat flows already have a `messages` field; pick another name.",
                    step=step.id,
                    setting="fields",
                )
            )
        if not an.outgoing[step.id]:
            issues.append(
                error(
                    "input_not_connected",
                    "Input isn't connected to anything, so a run has nowhere to go.",
                    step=step.id,
                    hint="Drag from the dot on the right of Input to the first step.",
                )
            )
        return issues


class OutputHandler(StepHandler):
    type = "output"
    label = "Output"
    technical = "END + output schema"
    category = "start_end"
    icon = "log-out"
    summary = "Where a run ends. Picks which fields to hand back."
    has_node = False
    form = [
        FormField(
            key="fields",
            label="Return these fields",
            kind="field_multi",
            help="The Flow Data fields a run returns. Leave empty to return everything.",
            example="summary",
        )
    ]

    def primary_output(self, step: Any) -> str | None:
        return None

    def reads(self, step: Any, an: Any) -> set[str]:
        return set(step.settings.fields)

    def check(self, step: Any, an: Any) -> list[Issue]:
        issues: list[Issue] = []
        if not an.incoming[step.id]:
            issues.append(
                warning(
                    "output_not_connected",
                    "Nothing leads to this Output.",
                    step=step.id,
                    hint="Connect the last step to it.",
                )
            )
        if not step.settings.fields and not an.chat:
            upstream = an.upstream_output(step.id)
            issues.append(
                warning(
                    "output_no_fields",
                    "Output doesn't pick any fields, so a run returns all Flow Data.",
                    step=step.id,
                    setting="fields",
                    fix=Fix(
                        "set_setting",
                        f"Return `{upstream}`",
                        {"key": "fields", "value": [upstream]},
                    )
                    if upstream
                    else None,
                )
            )
        return issues
