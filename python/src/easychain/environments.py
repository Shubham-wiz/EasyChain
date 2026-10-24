"""Environments (Phase 5.4, docs/publish.md): dev, staging and production.

A flow is drawn and tried as a **draft**. Publishing pins one of its saved versions to an
environment. Each environment of a workspace can have:

- its own **secrets**, laid over the workspace's (a key for the production account, say);
- **model swaps** (``openai:gpt-4.1`` → ``openai:gpt-4.1-mini`` in dev, to spend less);
- **Knowledge Base swaps** (``help_docs`` → ``help_docs_live`` in production).

The environment of the work in hand is a context variable, like the workspace: the worker
sets it around a run that belongs to one, and the model gateway and Knowledge Base search
read it. Drafts have none, so they run exactly as drawn.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

ENVIRONMENTS = ("dev", "staging", "production")
LABELS = {"dev": "Development", "staging": "Staging", "production": "Production"}


@dataclass(frozen=True)
class Environment:
    name: str
    models: Mapping[str, str] = field(default_factory=dict)
    knowledge: Mapping[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return LABELS.get(self.name, self.name)


_current: ContextVar[Environment | None] = ContextVar("easychain_environment", default=None)


def current() -> Environment | None:
    return _current.get()


def use(environment: Environment | None) -> Any:
    return _current.set(environment)


def reset(token: Any) -> None:
    _current.reset(token)


@contextlib.contextmanager
def using(environment: Environment | None) -> Iterator[None]:
    token = _current.set(environment)
    try:
        yield
    finally:
        _current.reset(token)


def model_for(model: str) -> str:
    """The model to call: the environment's swap for it, if it has one."""
    env = _current.get()
    return env.models.get(model, model) if env is not None and model else model


def knowledge_for(kb_id: str) -> str:
    """The Knowledge Base to search: the environment's swap for it, if it has one."""
    env = _current.get()
    return env.knowledge.get(kb_id, kb_id) if env is not None else kb_id


def from_settings(name: str, saved: Mapping[str, Any] | None) -> Environment:
    """An environment from what Settings keep for it."""
    saved = saved or {}
    models = {str(k): str(v) for k, v in (saved.get("models") or {}).items() if k and v}
    knowledge = {str(k): str(v) for k, v in (saved.get("knowledge") or {}).items() if k and v}
    return Environment(name, models, knowledge)
