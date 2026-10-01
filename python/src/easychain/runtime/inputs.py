"""Check and convert run inputs (form values arrive as text)."""

from __future__ import annotations

import json
from typing import Any

from ..compiler.codegen import CompiledFlow


class InputError(ValueError):
    def __init__(self, problems: list[dict[str, str]]):
        self.problems = problems
        super().__init__("; ".join(p["message"] for p in problems))


def _coerce(value: Any, ftype: str, name: str) -> Any:
    if value is None:
        return None
    if ftype == "number":
        if isinstance(value, int | float) and not isinstance(value, bool):
            return value
        text = str(value).strip()
        try:
            num = float(text)
        except ValueError:
            raise ValueError(f"`{name}` should be a number, not “{text}”.") from None
        return int(num) if num.is_integer() and "." not in text else num
    if ftype == "yes_no":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "yes", "y", "1", "on"):
            return True
        if text in ("false", "no", "n", "0", "off", ""):
            return False
        raise ValueError(f"`{name}` should be yes or no, not “{value}”.")
    if ftype in ("list", "object"):
        if isinstance(value, list | dict):
            return value
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            if ftype == "list":
                return [line.strip() for line in str(value).splitlines() if line.strip()]
            raise ValueError(f'`{name}` should be JSON, like {{"key": "value"}}.') from None
        return parsed
    return (
        value
        if isinstance(value, str)
        else json.dumps(value)
        if isinstance(value, dict | list)
        else str(value)
    )


def prepare_inputs(compiled: CompiledFlow, raw: dict[str, Any] | None) -> dict[str, Any]:
    """Apply defaults, convert types and check required fields. Raises InputError."""
    raw = dict(raw or {})
    an = compiled.analysis
    out: dict[str, Any] = dict(compiled.input_defaults)
    problems: list[dict[str, str]] = []
    if an.chat:
        message = raw.pop("message", None)
        messages = raw.pop("messages", None)
        if message is not None:
            messages = [{"role": "user", "content": str(message)}]
        if not messages:
            problems.append({"field": "message", "message": "Type a message to start the chat."})
        else:
            out["messages"] = messages
    fields = an.input_step.settings.fields if an.input_step else []
    for f in fields:
        value = raw.get(f.name)
        if value is None or value == "":
            if f.name in out:
                continue
            if f.required:
                problems.append(
                    {"field": f.name, "message": f"Fill in `{f.name}` to run the flow."}
                )
            continue
        try:
            out[f.name] = _coerce(value, f.type, f.name)
        except ValueError as exc:
            problems.append({"field": f.name, "message": str(exc)})
    if problems:
        raise InputError(problems)
    return out
