"""The model gateway: every model call made during a Run goes through here.

Generated code calls ``init_chat_model``. When Easy Chain runs a flow it swaps
that one name for ``gateway_init_chat_model`` so it can check for API keys
before calling a provider (and say so in plain words), or hand back the
stand-in AI. Exported code keeps LangChain's own ``init_chat_model``.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain.chat_models import init_chat_model as _init_chat_model

from ..providers import PROVIDERS, split_model
from .standin import StandInChatModel


class MissingAPIKey(RuntimeError):
    def __init__(self, provider: str, env_var: str, label: str):
        self.provider = provider
        self.env_var = env_var
        self.label = label
        super().__init__(f"No {label} is set ({env_var}).")


@dataclass
class RunSettings:
    stand_in: bool = False


_DEFAULT = RunSettings()
_settings: ContextVar[RunSettings | None] = ContextVar("easychain_run_settings", default=None)


def use_settings(settings: RunSettings) -> Any:
    return _settings.set(settings)


def reset_settings(token: Any) -> None:
    _settings.reset(token)


def key_status() -> dict[str, bool]:
    """Which providers have their API key available (Ollama never needs one)."""
    return {
        pid: (p.key_env is None or bool(os.environ.get(p.key_env))) for pid, p in PROVIDERS.items()
    }


def gateway_init_chat_model(model: str | None = None, **kwargs: Any) -> Any:
    settings = _settings.get() or _DEFAULT
    model = model or ""
    if settings.stand_in:
        return StandInChatModel(model_name=model or "stand-in")
    provider_id, _ = split_model(model)
    provider = PROVIDERS.get(provider_id or "")
    if (
        provider
        and provider.key_env
        and not os.environ.get(provider.key_env)
        and "api_key" not in kwargs
    ):
        raise MissingAPIKey(provider.id, provider.key_env, provider.key_label)
    return _init_chat_model(model, **kwargs)
