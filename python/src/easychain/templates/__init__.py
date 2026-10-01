"""Starter flows for the Templates gallery.

Each template is a normal flow file. ``tests`` points to its Test Set, which
also serves as the template's acceptance test.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

from ..providers import PROVIDERS, split_model
from ..spec import FlowSpec, load_spec

HERE = Path(__file__).parent

TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "summarise-url",
        "category": "Get started",
        "proves": ["AI Model", "Instructions", "Web request", "streaming"],
    },
    {
        "id": "chat-assistant",
        "category": "Get started",
        "proves": ["Chat", "Instructions", "AI Model", "memory per conversation"],
    },
    {
        "id": "reply-to-feedback",
        "category": "Customer support",
        "proves": ["Decision (AI)", "Instructions", "AI Model"],
    },
    {
        "id": "smart-summary",
        "category": "Research",
        "proves": ["Web request", "Code", "Decision (rules)", "AI Model"],
    },
]


def template_path(template_id: str) -> Path:
    path = HERE / f"{template_id}.flow.yaml"
    if not path.exists() or template_id not in {t["id"] for t in TEMPLATES}:
        raise KeyError(template_id)
    return path


def load_template(template_id: str) -> FlowSpec:
    return load_spec(template_path(template_id))


def keys_needed(spec: FlowSpec) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for step in spec.steps:
        model = getattr(step.settings, "model", None)
        if not model or (step.type == "decision" and step.settings.mode != "ai"):
            continue
        provider = PROVIDERS.get(split_model(model)[0] or "")
        if provider and provider.key_env:
            seen[provider.id] = {
                "provider": provider.id,
                "label": provider.key_label,
                "env": provider.key_env,
            }
    return list(seen.values())


@cache
def list_templates() -> list[dict[str, Any]]:
    out = []
    for meta in TEMPLATES:
        spec = load_template(meta["id"])
        input_step = next((s for s in spec.steps if s.type == "input"), None)
        sample = (
            {f.name: f.example for f in input_step.settings.fields if f.example is not None}
            if input_step
            else {}
        )
        out.append(
            {
                **meta,
                "name": spec.name,
                "description": spec.description,
                "keys": keys_needed(spec),
                "chat": bool(input_step and input_step.settings.mode == "chat"),
                "sample_inputs": sample,
                "steps": len(spec.steps),
                "has_tests": (HERE / f"{meta['id']}.tests.yaml").exists(),
            }
        )
    return out
