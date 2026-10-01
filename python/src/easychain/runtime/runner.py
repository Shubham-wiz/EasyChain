"""Run a flow and stream what happens, step by step.

Events (plain dicts, JSON-ready) drive the canvas animation, the run trace and
the CLI output:

- run_started   {run_id, thread_id, flow, stand_in}
- step_started  {step, input}
- token         {step, text}
- step_finished {step, output, duration_ms, usage, cost, model}
- route         {step, exit}           (a Decision picked an exit)
- step_failed   {step, error}
- run_finished  {status, output, duration_ms, usage, cost, error?, issues?}
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk, BaseMessage

from ..compiler import CompileError, compile_flow
from ..compiler.codegen import CompiledFlow
from ..providers import PROVIDERS, estimate_cost
from ..spec.models import FlowSpec
from .errors import explain
from .gateway import RunSettings, reset_settings, use_settings
from .inputs import InputError, prepare_inputs
from .loader import load_graph

MAX_STRING = 20_000
_ROLES = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}


def to_jsonable(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return "…"
    if isinstance(value, BaseMessage):
        out: dict[str, Any] = {"role": _ROLES.get(value.type, value.type), "content": value.text}
        if getattr(value, "tool_calls", None):
            out["tool_calls"] = to_jsonable(value.tool_calls, depth + 1)
        return out
    if isinstance(value, str):
        return (
            value
            if len(value) <= MAX_STRING
            else value[:MAX_STRING] + f"… [{len(value) - MAX_STRING} more characters]"
        )
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [to_jsonable(v, depth + 1) for v in value]
    return str(value)


class _Redactor:
    def __init__(self, extra: list[str]):
        values = list(extra)
        for provider in PROVIDERS.values():
            if provider.key_env and os.environ.get(provider.key_env):
                values.append(os.environ[provider.key_env])
        self.values = sorted({v for v in values if v and len(v) >= 6}, key=len, reverse=True)

    def __call__(self, value: Any) -> Any:
        if not self.values:
            return value
        if isinstance(value, str):
            for secret in self.values:
                if secret in value:
                    value = value.replace(secret, "••••••")
            return value
        if isinstance(value, dict):
            return {k: self(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self(v) for v in value]
        return value


class UsageTracker(BaseCallbackHandler):
    """Collects tokens and cost per step from model calls."""

    raise_error = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[UUID, tuple[str | None, str | None, str | None]] = {}
        self.by_step: dict[str, dict[str, Any]] = {}

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
            )

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            node, provider, model = self._calls.pop(run_id, (None, None, None))
        if node is None:
            return
        usage: dict[str, int] = {}
        try:
            message = response.generations[0][0].message
            usage = dict(getattr(message, "usage_metadata", None) or {})
            model = model or (message.response_metadata or {}).get("model_name")
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
        cost = (
            0.0
            if stand_in
            else (estimate_cost(f"{provider}:{model}", inp, out) if provider and model else None)
        )
        with self._lock:
            entry = self.by_step.setdefault(
                node,
                {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "model": None, "calls": 0},
            )
            entry["input_tokens"] += inp
            entry["output_tokens"] += out
            entry["calls"] += 1
            entry["model"] = model if not stand_in else "stand-in"
            if cost is None or entry["cost"] is None:
                entry["cost"] = None
            else:
                entry["cost"] += cost

    def take(self, node: str) -> dict[str, Any] | None:
        with self._lock:
            return self.by_step.pop(node, None)


@dataclass
class RunOptions:
    thread_id: str | None = None
    run_id: str | None = None
    stand_in: bool = False
    redact: list[str] = field(default_factory=list)
    recursion_limit: int = 25


def _now() -> float:
    return time.time() * 1000


_compiled_cache: OrderedDict[str, CompiledFlow] = OrderedDict()


def compile_cached(spec: FlowSpec) -> CompiledFlow:
    """Compile once per distinct spec; repeated runs of the same flow skip the compiler."""
    key = hashlib.sha256(spec.model_dump_json().encode()).hexdigest()
    if key in _compiled_cache:
        _compiled_cache.move_to_end(key)
        return _compiled_cache[key]
    compiled = compile_flow(spec)
    _compiled_cache[key] = compiled
    while len(_compiled_cache) > 64:
        _compiled_cache.popitem(last=False)
    return compiled


async def stream_run(
    spec: FlowSpec,
    inputs: dict[str, Any] | None = None,
    options: RunOptions | None = None,
    compiled: CompiledFlow | None = None,
) -> AsyncIterator[dict[str, Any]]:
    opts = options or RunOptions()
    run_id = opts.run_id or uuid.uuid4().hex
    thread_id = opts.thread_id or uuid.uuid4().hex
    redact = _Redactor(opts.redact)
    started = time.perf_counter()

    def event(kind: str, **data: Any) -> dict[str, Any]:
        return redact({"type": kind, "run_id": run_id, "ts": _now(), **data})

    def finished(status: str, **data: Any) -> dict[str, Any]:
        return event(
            "run_finished",
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            **data,
        )

    try:
        compiled = compiled or compile_cached(spec)
    except CompileError as exc:
        yield event("run_started", thread_id=thread_id, flow=spec.name, stand_in=opts.stand_in)
        yield finished(
            "error",
            error={
                "kind": "invalid_flow",
                "message": "The flow has problems to fix before it can run.",
                "fixes": [],
            },
            issues=[i.to_dict() for i in exc.issues],
        )
        return
    an = compiled.analysis
    try:
        prepared = prepare_inputs(compiled, inputs)
    except InputError as exc:
        yield event("run_started", thread_id=thread_id, flow=spec.name, stand_in=opts.stand_in)
        yield finished(
            "error",
            error={"kind": "bad_input", "message": str(exc), "problems": exc.problems, "fixes": []},
        )
        return

    module, graph = await asyncio.to_thread(load_graph, compiled.source, compiled.module_name)
    tracker = UsageTracker()
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [tracker],
        "recursion_limit": opts.recursion_limit,
        "run_name": spec.name,
    }
    yield event("run_started", thread_id=thread_id, flow=spec.name, stand_in=opts.stand_in)
    if an.input_step is not None:
        sid = an.input_step.id
        yield event("step_started", step=sid, input={})
        yield event("step_finished", step=sid, output=to_jsonable(prepared), duration_ms=0)

    running: dict[str, float] = {}
    task_errors: dict[str, str] = {}
    executed: set[str] = set()
    task_inputs: dict[str, dict[str, Any]] = {}
    last_values: dict[str, Any] = dict(prepared)
    totals = {"input_tokens": 0, "output_tokens": 0}
    total_cost: float | None = 0.0

    token = use_settings(RunSettings(stand_in=opts.stand_in))
    try:
        async for mode, payload in graph.astream(
            prepared, config, stream_mode=["tasks", "messages", "values"]
        ):
            if mode == "tasks":
                sid = payload.get("name")
                if sid not in an.steps:
                    continue
                if "input" in payload:
                    running[sid] = time.perf_counter()
                    reads = an.reads.get(sid, set())
                    state = payload.get("input") or {}
                    task_inputs[sid] = state
                    yield event(
                        "step_started",
                        step=sid,
                        input=to_jsonable({k: v for k, v in state.items() if k in reads}),
                    )
                    continue
                if payload.get("error"):
                    task_errors[sid] = str(payload["error"])
                    continue
                began = running.pop(sid, time.perf_counter())
                executed.add(sid)
                usage = tracker.take(sid)
                data: dict[str, Any] = {
                    "step": sid,
                    "output": to_jsonable(payload.get("result") or {}),
                    "duration_ms": round((time.perf_counter() - began) * 1000, 1),
                }
                if usage:
                    data["usage"] = {
                        "input_tokens": usage["input_tokens"],
                        "output_tokens": usage["output_tokens"],
                    }
                    data["cost"] = usage["cost"]
                    data["model"] = usage["model"]
                    totals["input_tokens"] += usage["input_tokens"]
                    totals["output_tokens"] += usage["output_tokens"]
                    total_cost = (
                        None
                        if (total_cost is None or usage["cost"] is None)
                        else total_cost + usage["cost"]
                    )
                yield event("step_finished", **data)
                if sid in compiled.routers:
                    # A Decision's router reads the state as it is right after the step.
                    state = {**task_inputs.pop(sid, {}), **(payload.get("result") or {})}
                    try:
                        label = getattr(module, compiled.routers[sid])(state)
                    except Exception:  # routing errors surface from LangGraph itself
                        label = None
                    if label is not None:
                        yield event("route", step=sid, exit=label)
            elif mode == "messages":
                chunk, meta = payload
                # Only model output streams as tokens; messages a step writes to state don't.
                if not isinstance(chunk, AIMessageChunk):
                    continue
                sid = meta.get("langgraph_node")
                text = chunk.text
                if sid in an.steps and text:
                    yield event("token", step=sid, text=text)
            elif mode == "values":
                last_values = payload
    except Exception as exc:
        failed = next(iter(task_errors), None) or (
            next(iter(running)) if len(running) == 1 else None
        )
        if failed is None and running:
            failed = next(iter(running))
        info = explain(exc, an.steps.get(failed) if failed else None)
        if failed:
            yield event("step_failed", step=failed, error=info)
        yield finished("error", error=info, step=failed, usage=totals, cost=total_cost)
        return
    finally:
        with contextlib.suppress(ValueError):  # generator closed from another context
            reset_settings(token)

    output_names = compiled.output_fields
    output = (
        {k: v for k, v in last_values.items() if k in output_names}
        if output_names
        else dict(last_values)
    )
    reply = None
    if an.chat and last_values.get("messages"):
        last = last_values["messages"][-1]
        reply = last.text if isinstance(last, BaseMessage) else str(last)
    for out_step in an.output_steps:
        if any(c.source in executed for c in an.incoming[out_step.id]):
            yield event("step_started", step=out_step.id, input={})
            yield event(
                "step_finished", step=out_step.id, output=to_jsonable(output), duration_ms=0
            )
    yield finished(
        "ok",
        output=to_jsonable(output),
        reply=reply,
        usage=totals,
        cost=total_cost,
        thread_id=thread_id,
    )


async def run_flow(
    spec: FlowSpec, inputs: dict[str, Any] | None = None, options: RunOptions | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run to completion; returns (run_finished event, all events)."""
    events = [e async for e in stream_run(spec, inputs, options)]
    return events[-1], events
