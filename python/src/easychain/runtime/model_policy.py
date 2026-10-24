"""The model gateway's policy (Phase 5.5, docs/gateway.md): what happens around each model call
of a run on an Easy Chain server.

``gateway.gateway_init_chat_model`` builds every chat model of a run. With a policy (from
Settings → Model gateway, made by the server for each run) it also:

- **caches answers** (``AnswerCache``): the same model, settings and messages get the answer
  kept the first time, for free, until it expires;
- **smooths requests** to a provider (a ``InMemoryRateLimiter`` per workspace and provider in
  this process), so bursts wait instead of hitting the provider's rate limit;
- **retries** a failed call (the provider package's own retries, with back-off);
- **falls back** to other models (``FallbackChatModel``) when the provider answers with an
  error or can't be reached;
- **meters** each call (``GatewayCallback``): before it, the budgets are checked (a budget
  used up stops the call with ``BudgetReached``); after it, the call is recorded with its
  tokens and cost, which is what budgets, alerts and the spending pages add up.

Exported code has none of this: it calls LangChain's own ``init_chat_model``.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from langchain_core.caches import BaseCache
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import messages_from_dict, messages_to_dict
from langchain_core.outputs import ChatGeneration
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import Runnable, RunnableWithFallbacks

from ..current_workspace import current as current_workspace
from ..providers import estimate_cost

# Set on an answer that came from the cache: it cost nothing and used no provider tokens.
CACHED = "easychain_cached"


class BudgetReached(RuntimeError):
    """A budget is used up and stops new model calls (a RuntimeError, so step retries don't
    try again)."""

    def __init__(self, message: str, budget: str):
        self.budget = budget
        super().__init__(message)


class _QuietBudgets(logging.Filter):
    """LangChain logs a warning for each callback that raises; a budget used up isn't news
    there (the run's error says it)."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "BudgetReached(" not in record.getMessage()


logging.getLogger("langchain_core.callbacks.manager").addFilter(_QuietBudgets())


class Meter(Protocol):
    """Made by the server for each run: checks budgets and records calls (server/gateway.py)."""

    def check(self) -> None: ...

    def record(self, call: dict[str, Any]) -> None: ...


@dataclass
class GatewayPolicy:
    # model -> the models to try, in order, when it fails ("provider:model")
    fallbacks: dict[str, list[str]] = field(default_factory=dict)
    # How many times a provider package retries a failed call (None: its own default)
    retries: int | None = None
    # provider -> at most this many requests a second, in this process (smoothing)
    per_second: dict[str, float] = field(default_factory=dict)
    # A langchain BaseCache when this run's answers are cached, else None
    cache: BaseCache | None = None
    # "provider:model" -> (USD per million input tokens, per million output tokens); these
    # win over the prices Easy Chain knows (providers.py)
    prices: dict[str, tuple[float, float]] = field(default_factory=dict)
    meter: Meter | None = None

    def price(self, model: str, input_tokens: int, output_tokens: int) -> float | None:
        own = self.prices.get(model)
        if own is not None:
            return (input_tokens * own[0] + output_tokens * own[1]) / 1_000_000
        return estimate_cost(model, input_tokens, output_tokens)


def price(policy: GatewayPolicy | None, model: str, inp: int, out: int) -> float | None:
    return policy.price(model, inp, out) if policy else estimate_cost(model, inp, out)


def call_usage(
    response: Any, provider: str | None, model: str | None, policy: GatewayPolicy | None
) -> dict[str, Any]:
    """Tokens and cost of one finished model call (a LangChain LLMResult)."""
    usage: dict[str, Any] = {}
    cached = False
    try:
        message = response.generations[0][0].message
        usage = dict(getattr(message, "usage_metadata", None) or {})
        meta = message.response_metadata or {}
        model = model or meta.get("model_name")
        cached = bool(meta.get(CACHED))
    except (AttributeError, IndexError):
        pass
    if not usage and response.llm_output:
        tu = response.llm_output.get("token_usage") or {}
        usage = {
            "input_tokens": tu.get("prompt_tokens", 0),
            "output_tokens": tu.get("completion_tokens", 0),
        }
    inp, out = int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)
    stand_in = "stand-in" in (provider or "") or model == "stand-in"
    if cached:
        # Kept from an earlier call: the provider wasn't asked.
        inp = out = 0
    if stand_in or cached:
        cost: float | None = 0.0
    else:
        cost = price(policy, f"{provider}:{model}", inp, out) if provider and model else None
    return {
        "provider": provider,
        "model": "stand-in" if stand_in else model,
        "input_tokens": inp,
        "output_tokens": out,
        "cost": cost,
        "cached": cached,
        "stand_in": stand_in,
    }


# ── metering: budgets before a call, a record after it ──────────────────────


class GatewayCallback(BaseCallbackHandler):
    """On each chat model the gateway builds. ``raise_error``: a budget used up stops the call
    (the meter's ``check`` raises BudgetReached); anything else here never breaks a run."""

    raise_error = True

    def __init__(self, policy: GatewayPolicy, model: str):
        self.policy = policy
        self.model = model
        self._lock = threading.Lock()
        self._calls: dict[UUID, tuple[str | None, str | None, str | None, float]] = {}

    def on_chat_model_start(
        self,
        serialized: Any,
        messages: Any,
        *,
        run_id: UUID,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        with self._lock:
            self._calls[run_id] = (
                md.get("langgraph_node"),
                md.get("ls_provider"),
                md.get("ls_model_name"),
                time.perf_counter(),
            )
        if self.policy.meter is not None:
            self.policy.meter.check()

    def _take(self, run_id: UUID) -> tuple[str | None, str | None, str | None, float]:
        with self._lock:
            return self._calls.pop(run_id, (None, None, None, time.perf_counter()))

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        step, provider, model, started = self._take(run_id)
        try:
            usage = call_usage(response, provider, model, self.policy)
            if usage["stand_in"]:
                return
            self._record(step, started, usage)
        except Exception:  # pragma: no cover - a record must never break the run
            return

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        step, provider, model, started = self._take(run_id)
        if isinstance(error, BudgetReached):
            return
        usage = {
            "provider": provider,
            "model": model,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
            "cached": False,
            "error": f"{type(error).__name__}: {error}"[:300],
        }
        self._record(step, started, usage)

    def _record(self, step: str | None, started: float, usage: dict[str, Any]) -> None:
        if self.policy.meter is None:
            return
        call = {
            "step": step,
            "provider": usage.get("provider"),
            "model": usage.get("model") or self.model,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cost": usage["cost"],
            "cached": usage["cached"],
            "error": usage.get("error"),
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        try:
            self.policy.meter.record(call)
        except Exception:  # pragma: no cover - a record must never break the run
            return


# ── caching ─────────────────────────────────────────────────────────────────


class CacheStore(Protocol):
    """Where answers are kept (server/gateway.py keeps them in the run database)."""

    def get(self, key: str) -> str | None: ...

    def put(self, key: str, value: str, expires_at: float) -> None: ...


class AnswerCache(BaseCache):
    """The same model, settings (tools and structured output included) and messages get the
    answer kept the first time, within the workspace, until it expires."""

    def __init__(self, store: CacheStore, seconds: float, scope: str | None = None):
        self.store = store
        self.seconds = seconds
        self.scope = scope

    def _key(self, prompt: str, llm_string: str) -> str:
        scope = self.scope or current_workspace()
        return hashlib.sha256(f"{scope}\0{llm_string}\0{prompt}".encode()).hexdigest()

    def lookup(self, prompt: str, llm_string: str) -> list[ChatGeneration] | None:
        raw = self.store.get(self._key(prompt, llm_string))
        if raw is None:
            return None
        try:
            found = messages_from_dict(json.loads(raw))
        except (ValueError, KeyError, TypeError):
            return None
        out = []
        for message in found:
            message.response_metadata = {**(message.response_metadata or {}), CACHED: True}
            out.append(ChatGeneration(message=message))
        return out

    def update(self, prompt: str, llm_string: str, return_val: Any) -> None:
        messages = [g.message for g in return_val if isinstance(g, ChatGeneration)]
        if not messages or len(messages) != len(return_val):
            return
        value = json.dumps(messages_to_dict(messages))
        self.store.put(self._key(prompt, llm_string), value, time.time() + self.seconds)

    def clear(self, **kwargs: Any) -> None:  # pragma: no cover - not used by LangChain here
        return None


# ── smoothing ───────────────────────────────────────────────────────────────

_limiters: dict[tuple[str, str, float], InMemoryRateLimiter] = {}
_limiters_lock = threading.Lock()


def limiter_for(provider: str, per_second: float) -> InMemoryRateLimiter:
    """One limiter per workspace, provider and rate in this process, shared by its runs."""
    key = (current_workspace(), provider, per_second)
    with _limiters_lock:
        found = _limiters.get(key)
        if found is None:
            found = InMemoryRateLimiter(
                requests_per_second=per_second,
                check_every_n_seconds=min(0.1, 1 / per_second / 4),
                max_bucket_size=max(1.0, per_second),
            )
            _limiters[key] = found
        return found


# ── fallbacks ───────────────────────────────────────────────────────────────

_ERRORS: tuple[type[BaseException], ...] | None = None


def provider_errors() -> tuple[type[BaseException], ...]:
    """What makes the gateway try the next model: the provider answered with an error, or
    couldn't be reached. Easy Chain's own errors (a budget used up, a reply that doesn't fit
    its schema) don't."""
    global _ERRORS
    if _ERRORS is None:
        import httpx

        found: list[type[BaseException]] = [httpx.HTTPError, TimeoutError, ConnectionError]
        for module, name in (
            ("openai", "APIError"),
            ("anthropic", "APIError"),
            ("ollama", "ResponseError"),
            ("groq", "APIError"),
            ("google.api_core.exceptions", "GoogleAPIError"),
            ("botocore.exceptions", "ClientError"),
        ):
            try:
                found.append(getattr(importlib.import_module(module), name))
            except (ImportError, AttributeError):
                continue
        _ERRORS = tuple(found)
    return _ERRORS


class FallbackChatModel(BaseChatModel):
    """A chat model that tries ``models`` in order: the first is the step's own, the others
    are its fallbacks. Each model keeps its own callbacks, so every call is recorded and
    priced as the model that made it. Tools and structured output are set up on each one."""

    models: list[BaseChatModel]

    @property
    def _llm_type(self) -> str:
        return "easychain-fallbacks"

    def _chain(self, build: Callable[[BaseChatModel], Runnable] | None = None) -> Runnable:
        made = [build(m) if build else m for m in self.models]
        return RunnableWithFallbacks(
            runnable=made[0], fallbacks=made[1:], exceptions_to_handle=provider_errors()
        )

    def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._chain().invoke(input, config, **kwargs)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        return await self._chain().ainvoke(input, config, **kwargs)

    def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        yield from self._chain().stream(input, config, **kwargs)

    async def astream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        async for chunk in self._chain().astream(input, config, **kwargs):
            yield chunk

    def batch(self, inputs: Any, config: Any = None, **kwargs: Any) -> Any:
        return self._chain().batch(inputs, config, **kwargs)

    async def abatch(self, inputs: Any, config: Any = None, **kwargs: Any) -> Any:
        return await self._chain().abatch(inputs, config, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self._chain(lambda m: m.bind_tools(tools, **kwargs))

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self._chain(lambda m: m.with_structured_output(schema, **kwargs))

    def bind(self, **kwargs: Any) -> Runnable:  # type: ignore[override]
        return self._chain(lambda m: m.bind(**kwargs))

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any):
        # Not reached (invoke and the rest go to the chain); here because it's required.
        return self.models[0]._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> Any:
        return self.models[0]._get_ls_params(stop=stop, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # The step's own model answers for everything else (model_name, profile, …), which
        # agents read to choose how to ask for structured output.
        try:
            return super().__getattr__(name)  # type: ignore[misc]
        except AttributeError:
            models = self.__dict__.get("models")
            if not models or name.startswith("__"):
                raise
            return getattr(models[0], name)
