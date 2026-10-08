"""Read and write flow spec files (``*.flow.yaml``).

Files are written for humans and for git: stable key order, multi-line text as
YAML block scalars, settings that equal their default left out, and the visual
``canvas`` section last so that moving a box does not touch the logic above it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import SPEC_VERSION, FlowSpec

_TOP_ORDER = [
    "version",
    "name",
    "description",
    "settings",
    "data",
    "steps",
    "connections",
    "canvas",
]
_STEP_ORDER = ["id", "type", "name", "description", "settings", "run"]


class SpecError(ValueError):
    """A flow file could not be read. ``problems`` holds one plain-language line per issue."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("This flow file has problems:\n" + "\n".join(f"  - {p}" for p in problems))


def parse_spec(data: dict[str, Any] | Any) -> FlowSpec:
    """Validate a dict (from YAML or JSON) into a FlowSpec, with readable errors."""
    if not isinstance(data, dict):
        raise SpecError(["The file must contain a mapping with at least a `name` and `steps`."])
    try:
        return FlowSpec.model_validate(data)
    except ValidationError as exc:
        raise SpecError(format_validation_error(exc, data)) from exc


def load_spec(path: str | Path) -> FlowSpec:
    text = Path(path).read_text(encoding="utf-8")
    return loads_spec(text)


def loads_spec(text: str) -> FlowSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise SpecError([f"The file is not valid YAML: {exc}"]) from exc
    return parse_spec(data)


def spec_to_dict(spec: FlowSpec) -> dict[str, Any]:
    """Compact, ordered dict form of a spec (what gets written to YAML)."""
    raw = spec.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    raw["version"] = SPEC_VERSION
    raw.setdefault("name", spec.name)
    out: dict[str, Any] = {}
    for key in _TOP_ORDER:
        if key in raw and raw[key] not in ({}, []):
            out[key] = raw[key]
    if "steps" in out:
        out["steps"] = [_order(step, _STEP_ORDER) for step in out["steps"]]
    else:
        out["steps"] = []
    return out


def dumps_spec(spec: FlowSpec) -> str:
    return yaml.dump(
        spec_to_dict(spec),
        Dumper=_Dumper,
        sort_keys=False,
        allow_unicode=True,
        width=100,
        default_flow_style=False,
    )


def save_spec(spec: FlowSpec, path: str | Path) -> None:
    Path(path).write_text(dumps_spec(spec), encoding="utf-8", newline="\n")


def spec_json(spec: FlowSpec) -> dict[str, Any]:
    """Full JSON form (all defaults filled in) used by the web app."""
    return json.loads(spec.model_dump_json(by_alias=True))


def format_validation_error(exc: ValidationError, data: dict[str, Any]) -> list[str]:
    problems = []
    for err in exc.errors():
        loc = list(err["loc"])
        where = _describe_location(loc, data)
        msg = err["msg"]
        if err["type"] == "string_pattern_mismatch":
            msg = (
                "use lowercase letters, digits and underscores, starting with a letter "
                "(for example `page_text`)"
            )
        elif err["type"] == "union_tag_invalid":
            tag = err.get("input", {}).get("type") if isinstance(err.get("input"), dict) else None
            msg = f"unknown step type '{tag}'" if tag else msg
        elif err["type"] == "extra_forbidden":
            msg = "this setting is not recognised (check the spelling)"
        elif msg.startswith("Value error, "):
            msg = msg.removeprefix("Value error, ")
        problems.append(f"{where}: {msg}" if where else msg)
    return problems


def _describe_location(loc: list[Any], data: dict[str, Any]) -> str:
    parts: list[str] = []
    node: Any = data
    i = 0
    while i < len(loc):
        key = loc[i]
        if key == "steps" and i + 1 < len(loc) and isinstance(loc[i + 1], int):
            idx = loc[i + 1]
            steps = node.get("steps") if isinstance(node, dict) else None
            step = steps[idx] if isinstance(steps, list) and idx < len(steps) else None
            step_id = step.get("id") if isinstance(step, dict) else None
            parts.append(f"step '{step_id}'" if step_id else f"step #{idx + 1}")
            node = step
            i += 2
            # Skip the discriminator tag pydantic inserts (e.g. 'ai_model').
            if i < len(loc) and isinstance(node, dict) and loc[i] == node.get("type"):
                i += 1
            continue
        parts.append(str(key))
        node = (
            node.get(key)
            if isinstance(node, dict)
            else (
                node[key]
                if isinstance(node, list) and isinstance(key, int) and key < len(node)
                else None
            )
        )
        i += 1
    return " › ".join(parts)


def _order(d: dict[str, Any], order: list[str]) -> dict[str, Any]:
    out = {k: d[k] for k in order if k in d}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


class _Dumper(yaml.SafeDumper):
    pass


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if "\n" in value:
        # A block scalar (|) reads best, but it can't keep spaces at the end of a line (a
        # Markdown line break in a prompt, say). Such text is written double-quoted instead,
        # so saving never changes it.
        style = '"' if re.search(r"[ \t]$", value, flags=re.M) else "|"
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _str_representer)
