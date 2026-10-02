"""Structured replies: the fields people define in the builder become Pydantic classes.

AI Model steps pass the class to ``with_structured_output``; Agent steps pass it
as ``response_format``. Each field's description goes to the model too.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .issues import Issue, error, warning
from .pycode import docstring, py_str

if TYPE_CHECKING:
    from ..spec.models import SchemaField, StructuredOutput

_SCALARS = {"text": "str", "number": "float", "whole_number": "int", "yes_no": "bool"}

# Flow Data type for each field when a structured reply is spread into Flow Data.
FLOW_TYPES = {
    "text": "text",
    "number": "number",
    "whole_number": "number",
    "yes_no": "yes_no",
    "choice": "text",
    "list": "list",
    "object": "object",
}


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def emit_schema(output: StructuredOutput, class_name: str, doc: str, ctx: Any) -> list[str]:
    """Pydantic classes for a structured reply, nested classes first."""
    ctx.imports.add_from("pydantic", "BaseModel")
    blocks: list[str] = []

    def build(name: str, fields: list[SchemaField], text: str) -> None:
        lines = [f"class {name}(BaseModel):", docstring(text), ""]
        for f in fields:
            annotation = _annotation(f, name)
            if not f.required:
                annotation += " | None"
            args = []
            if not f.required:
                args.append("default=None")
            if f.description.strip():
                args.append(f"description={py_str(' '.join(f.description.split()))}")
            if args:
                ctx.imports.add_from("pydantic", "Field")
                lines.append(f"    {f.name}: {annotation} = Field({', '.join(args)})")
            else:
                lines.append(f"    {f.name}: {annotation}")
        if not fields:
            lines.append("    pass")
        blocks.append("\n".join(lines))

    def _annotation(f: SchemaField, owner: str) -> str:
        if f.type in _SCALARS:
            return _SCALARS[f.type]
        if f.type == "choice":
            ctx.imports.add_from("typing", "Literal")
            return "Literal[" + ", ".join(py_str(o) for o in f.options) + "]"
        if f.type == "object" or (f.type == "list" and f.items == "object"):
            # A list of "issues" holds one "Issue" each.
            singular = (
                f.name[:-1]
                if f.type == "list" and f.name.endswith("s") and not f.name.endswith("ss")
                else f.name
            )
            nested = ctx.names.claim(f"{owner}{_camel(singular)}")
            build(nested, f.fields, f.description or f"One {f.name.replace('_', ' ')}.")
            return f"list[{nested}]" if f.type == "list" else nested
        if f.type == "list":
            return f"list[{_SCALARS.get(f.items, 'str')}]"
        ctx.imports.add_from("typing", "Any")
        return "Any"

    build(class_name, output.fields, doc)
    return blocks


def check_schema(step_id: str, output: StructuredOutput, setting: str = "output") -> list[Issue]:
    issues: list[Issue] = []
    if not output.fields:
        issues.append(
            error(
                "schema_empty",
                "The reply format has no fields.",
                step=step_id,
                setting=setting,
                hint="Add the fields you want back, for example sentiment (a choice) and reason (text).",
            )
        )

    undescribed: list[str] = []

    def walk(fields: list[SchemaField], where: str) -> None:
        seen: set[str] = set()
        for f in fields:
            if f.name in seen:
                issues.append(
                    error(
                        "schema_duplicate",
                        f"Two fields{where} are called `{f.name}`.",
                        step=step_id,
                        setting=setting,
                    )
                )
            seen.add(f.name)
            if f.type == "choice" and not f.options:
                issues.append(
                    error(
                        "schema_no_options",
                        f"The choice `{f.name}` has no options to choose from.",
                        step=step_id,
                        setting=setting,
                        hint="Add the allowed values, for example positive, negative, mixed.",
                    )
                )
            nested = f.type == "object" or (f.type == "list" and f.items == "object")
            if nested:
                if not f.fields:
                    issues.append(
                        error(
                            "schema_empty_object",
                            f"`{f.name}` holds objects but has no fields of its own.",
                            step=step_id,
                            setting=setting,
                        )
                    )
                walk(f.fields, f" in `{f.name}`")
            if not f.description.strip() and f.type != "choice":
                undescribed.append(f.name)

    walk(output.fields, "")
    if undescribed:
        names = ", ".join(f"`{n}`" for n in undescribed)
        issues.append(
            warning(
                "schema_no_description",
                f"Describe {names} so the model knows what to put there.",
                step=step_id,
                setting=setting,
            )
        )
    return issues


def spread_types(output: StructuredOutput | None) -> dict[str, str]:
    """Flow Data fields set by spreading a structured reply."""
    if output is None or not output.spread:
        return {}
    return {f.name: FLOW_TYPES[f.type] for f in output.fields}
