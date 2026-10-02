"""The model gateway: every model call made during a Run goes through here.

Generated code calls ``init_chat_model`` and ``init_embeddings``. When Easy Chain
runs a flow it swaps those names for the gateway versions below, so it can check
for API keys and packages before calling a provider (and say so in plain words),
or hand back the stand-in AI. Exported code keeps LangChain's own functions.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from langchain.chat_models import init_chat_model as _init_chat_model
from langchain.embeddings import init_embeddings as _init_embeddings

from ..knowledge.embeddings import KeywordEmbeddings
from ..providers import EMBEDDING_MODELS, PROVIDERS, split_model
from .standin import Script, StandInChatModel


class MissingAPIKey(RuntimeError):
    def __init__(self, provider: str, env_var: str, label: str):
        self.provider = provider
        self.env_var = env_var
        self.label = label
        super().__init__(f"No {label} is set ({env_var}).")


class MissingPackage(RuntimeError):
    def __init__(self, provider: str, label: str, package: str, extra: str | None):
        self.provider = provider
        self.label = label
        self.package = package
        self.extra = extra
        super().__init__(f"The {label} package ({package}) isn't installed.")


@dataclass
class RunSettings:
    stand_in: bool = False
    # Scripted turns for the stand-in AI (Test Sets).
    script: Script | None = None


_DEFAULT = RunSettings()
_settings: ContextVar[RunSettings | None] = ContextVar("easychain_run_settings", default=None)


def use_settings(settings: RunSettings) -> Any:
    return _settings.set(settings)


def reset_settings(token: Any) -> None:
    _settings.reset(token)


def current_settings() -> RunSettings:
    return _settings.get() or _DEFAULT


def key_status() -> dict[str, bool]:
    """Which providers have their API key available (cloud-credential providers count as set)."""
    return {
        pid: (p.key_env is None or bool(os.environ.get(p.key_env))) for pid, p in PROVIDERS.items()
    }


def _check_provider(provider_id: str | None, kwargs: dict[str, Any]) -> None:
    provider = PROVIDERS.get(provider_id or "")
    if provider is None:
        return
    if not provider.installed():
        raise MissingPackage(provider.id, provider.label, provider.package, provider.extra)
    if provider.key_env and not os.environ.get(provider.key_env) and "api_key" not in kwargs:
        raise MissingAPIKey(provider.id, provider.key_env, provider.key_label)


def gateway_init_chat_model(model: str | None = None, **kwargs: Any) -> Any:
    settings = current_settings()
    model = model or ""
    if settings.stand_in:
        return StandInChatModel(model_name=model or "stand-in", script=settings.script)
    provider_id, _ = split_model(model)
    _check_provider(provider_id, kwargs)
    return _init_chat_model(model, **kwargs)


def gateway_init_embeddings(model: str, **kwargs: Any) -> Any:
    """Embeddings for a Knowledge Base. They must match how it was built, so the stand-in
    AI doesn't replace them; "keywords" needs no model at all."""
    if model == "keywords":
        return KeywordEmbeddings(**kwargs)
    provider_id, _ = split_model(model)
    info = EMBEDDING_MODELS.get(model)
    _check_provider(info.provider if info else provider_id, kwargs)
    return _init_embeddings(model, **kwargs)
