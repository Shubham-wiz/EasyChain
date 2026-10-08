"""Run a flow and stream what happens, step by step.

Events (plain dicts, JSON-ready) drive the canvas animation, the run trace and
the CLI output:

- run_started   {run_id, thread_id, flow, stand_in, action}
- step_started  {step, input, item?}
- token         {step, text}
- step_finished {step, output, duration_ms, usage, cost, model, item?}
- route         {step, exit}           (a Decision, Jump, Ask a Human or For Each took an exit)
- progress      {step, done, total}    (For Each)
- custom        {data, step?}          (a step called get_stream_writer()(...))
- tool_started  {step, tool, args, call_id}    (an Agent called one of its tools)
- tool_finished {step, tool, call_id, result, duration_ms, status}
- step_paused   {step, interrupt_id, request}  (Ask a Human, or an Agent's tool approval)
- save_point    {checkpoint_id, next, step_number}
- step_failed   {step, error}
- paused        {reason, interrupts, next}
- run_finished  {status, output, duration_ms, usage, cost, checkpoint_id, error?, issues?}

``status`` is ok, error, paused (waiting for a person or at a breakpoint) or cancelled.
Events from steps inside a Sub-flow carry ``path``: the Sub-flow steps leading to them.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk, BaseMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph import END
from langgraph.types import Command

from ..compiler import CompileError, compile_flow
from ..compiler.analysis import EACH_ITEM, WHEN_DONE, FlowAnalysis, Resolver
from ..compiler.codegen import DEFAULT_MAX_STEPS, CompiledFlow, exit_targets
from ..providers import PROVIDERS, estimate_cost
from ..spec.models import FlowSpec
from ..steps.flow_control import index_field
from .errors import explain
from .gateway import RunSettings, reset_settings, use_settings
from .inputs import InputError, prepare_inputs
from .loader import load_graph
from .resources import Resources, memory_resources

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


class Redactor:
    """Hides secret values (``extra``, and the API keys in the environment) in run data."""

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
    """Collects tokens and cost per step run from model calls.

    Calls are keyed by the LangGraph task namespace (``node:task_id``, nested with ``|``
    inside sub-flows), so parallel runs of one step and steps inside sub-flows stay apart.
    """

    raise_error = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls: dict[UUID, tuple[str | None, str | None, str | None]] = {}
        self.by_task: dict[str, dict[str, Any]] = {}

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
        key = md.get("langgraph_checkpoint_ns") or md.get("langgraph_node")
        with self._lock:
            self._calls[run_id] = (key, md.get("ls_provider"), md.get("ls_model_name"))

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            key, provider, model = self._calls.pop(run_id, (None, None, None))
        if key is None:
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
            entry = self.by_task.setdefault(key, _empty_usage())
            _add_usage(entry, {"input_tokens": inp, "output_tokens": out, "cost": cost, "calls": 1})
            entry["model"] = model if not stand_in else "stand-in"

    def take(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self.by_task.pop(key, None)


def _empty_usage() -> dict[str, Any]:
    return {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "model": None, "calls": 0}


def _add_usage(into: dict[str, Any], usage: dict[str, Any]) -> None:
    into["input_tokens"] += usage.get("input_tokens", 0)
    into["output_tokens"] += usage.get("output_tokens", 0)
    into["calls"] = into.get("calls", 0) + usage.get("calls", 0)
    if into["cost"] is None or usage.get("cost") is None:
        into["cost"] = None
    else:
        into["cost"] += usage["cost"]
    if usage.get("model") and not into.get("model"):
        into["model"] = usage["model"]


# How a run begins: a new run, an answer to Ask a Human, carrying on after a pause or an
# error, or re-running from an earlier Save Point (time travel).
RunAction = Literal["start", "resume", "continue", "fork"]


@dataclass
class RunOptions:
    thread_id: str | None = None
    run_id: str | None = None
    stand_in: bool = False
    redact: list[str] = field(default_factory=list)
    recursion_limit: int | None = None
    action: RunAction = "start"
    # For "resume": the answer, or {interrupt_id: answer} when several steps wait.
    resume: Any = None
    # For "fork": the Save Point to start from, and Flow Data to change there first.
    checkpoint_id: str | None = None
    update: dict[str, Any] | None = None
    # Breakpoints: pause before or after these steps.
    pause_before: list[str] = field(default_factory=list)
    pause_after: list[str] = field(default_factory=list)
    resources: Resources | None = None
    # Looks up flows used as Sub-flows.
    resolve: Resolver | None = None
    flow_id: str | None = None
    # Set to stop the run; it ends with status "cancelled" and can be continued later.
    cancel: asyncio.Event | None = None
    # Saved with every Save Point (the run id is always added).
    metadata: dict[str, Any] = field(default_factory=dict)
    # For "continue": when nothing is left to run, finish with the Flow Data as it is
    # (a worker recovering a run that ended just before it crashed).
    finish_if_done: bool = False
    # Scripted turns for the stand-in AI (Test Sets); a Script shared across resumes.
    script: Any = None
    # MCP server connections (Settings → MCP servers); None reads EASYCHAIN_MCP_SERVERS.
    mcp: dict[str, Any] | None = None


def _now() -> float:
    return time.time() * 1000


_compiled_cache: OrderedDict[str, CompiledFlow] = OrderedDict()


def _flow_key(spec: FlowSpec, resolve: Resolver | None, seen: tuple[str, ...] = ()) -> str:
    """A key that changes when the flow, or any flow it uses as a Sub-flow, changes."""
    from ..steps.flow_control import subflow_ids

    parts = [spec.model_dump_json()]
    if resolve is not None:
        for flow_id in subflow_ids(spec):
            if flow_id in seen:
                continue
            child = resolve(flow_id)
            parts.append(
                f"{flow_id}={_flow_key(child, resolve, (*seen, flow_id)) if child else '-'}"
            )
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def compile_cached(
    spec: FlowSpec, resolve: Resolver | None = None, flow_id: str | None = None
) -> CompiledFlow:
    """Compile once per distinct flow; repeated runs of the same flow skip the compiler."""
    key = f"{flow_id}:{_flow_key(spec, resolve, (flow_id,) if flow_id else ())}"
    if key in _compiled_cache:
        _compiled_cache.move_to_end(key)
        return _compiled_cache[key]
    compiled = compile_flow(spec, resolve=resolve, flow_id=flow_id)
    _compiled_cache[key] = compiled
    while len(_compiled_cache) > 64:
        _compiled_cache.popitem(last=False)
    return compiled


@dataclass
class _Flow:
    """One flow inside a run: the root, or a Sub-flow reached through ``path``."""

    analysis: FlowAnalysis
    routers: dict[str, str]
    node_steps: dict[str, str]
    jumps: dict[str, dict[str, str]]


def _jump_targets(an: FlowAnalysis) -> dict[str, dict[str, str]]:
    """Jump step -> {next node: exit label}."""
    out: dict[str, dict[str, str]] = {}
    for sid, step in an.steps.items():
        if step.type != "jump":
            continue
        targets: dict[str, str] = {}
        for label, target in exit_targets(an, sid).items():
            node = END if target == "END" else json.loads(target)
            targets.setdefault(node, label)
        plain = [c for c in an.outgoing[sid] if c.exit is None]
        if plain and not step.settings.exits:
            node = END if an.steps[plain[0].target].type == "output" else plain[0].target
            targets.setdefault(node, step.settings.otherwise)
        out[sid] = targets
    return out


class _Flows:
    def __init__(self, compiled: CompiledFlow):
        self.compiled = compiled
        an = compiled.analysis
        self.root = _Flow(an, compiled.routers, compiled.node_steps, _jump_targets(an))
        self._cache: dict[tuple[str, ...], _Flow | None] = {(): self.root}

    def at(self, path: tuple[str, ...]) -> _Flow | None:
        if path in self._cache:
            return self._cache[path]
        parent = self.at(path[:-1])
        found: _Flow | None = None
        if parent is not None:
            step = parent.analysis.steps.get(path[-1])
            if step is not None and step.type == "subflow":
                parts = self.compiled.children.get(step.settings.flow)
                if parts is not None:
                    found = _Flow(
                        parts.analysis,
                        parts.routers,
                        parts.node_steps,
                        _jump_targets(parts.analysis),
                    )
        self._cache[path] = found
        return found


def _path(ns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part.split(":", 1)[0] for part in ns)


def _agent_at(flows: _Flows, path: tuple[str, ...]) -> str | None:
    """The Agent step whose own graph (model, tools, add-ons) emitted an event at ``path``."""
    if not path:
        return None
    parent = flows.at(path[:-1])
    if parent is None:
        return None
    step = parent.analysis.steps.get(path[-1])
    return path[-1] if step is not None and step.type == "agent" else None


def describe_request(value: Any) -> Any:
    """What a waiting step asks, in the shape the run panel and the Inbox show.

    Ask a Human requests already have that shape. An Agent's tool approval (LangChain's
    HumanInTheLoopMiddleware) becomes kind "approve_tool".
    """
    if isinstance(value, dict) and "action_requests" in value:
        actions = [
            {"tool": a.get("name"), "args": a.get("args") or {}}
            for a in value.get("action_requests") or []
        ]
        configs = value.get("review_configs") or []
        allowed = (configs[0].get("allowed_decisions") if configs else None) or [
            "approve",
            "reject",
        ]
        names = ", ".join(a["tool"] or "a tool" for a in actions)
        return {
            "kind": "approve_tool",
            "question": f"The agent wants to use {names}. Do you approve?",
            "actions": actions,
            "allowed": allowed,
        }
    return value


def _tool_decisions(value: Any, answer: Any) -> Any:
    """Turn an Inbox answer into the decisions HumanInTheLoopMiddleware expects."""
    if not (isinstance(value, dict) and "action_requests" in value):
        return answer
    if isinstance(answer, dict) and "decisions" in answer:
        return answer
    if not isinstance(answer, dict):
        answer = {"action": "approve" if answer is True or answer == "approve" else "reject"}
    # Only a clear "approve" lets the tool run; anything else is a no.
    action = "approve" if answer.get("action") == "approve" else "reject"
    decisions = []
    for request in value.get("action_requests") or []:
        if action == "reject":
            message = (answer.get("comment") or "").strip() or "A person said no to this."
            decisions.append({"type": "reject", "message": message})
            continue
        edited = answer.get("value")
        if isinstance(edited, dict) and edited != request.get("args"):
            decisions.append(
                {"type": "edit", "edited_action": {"name": request.get("name"), "args": edited}}
            )
        else:
            decisions.append({"type": "approve"})
    return {"decisions": decisions}


async def stream_run(
    spec: FlowSpec,
    inputs: dict[str, Any] | None = None,
    options: RunOptions | None = None,
    compiled: CompiledFlow | None = None,
) -> AsyncIterator[dict[str, Any]]:
    opts = options or RunOptions()
    run_id = opts.run_id or uuid.uuid4().hex
    thread_id = opts.thread_id or uuid.uuid4().hex
    redact = Redactor(opts.redact)
    started = time.perf_counter()
    action = opts.action

    def event(kind: str, **data: Any) -> dict[str, Any]:
        return redact({"type": kind, "run_id": run_id, "ts": _now(), **data})

    def finished(status: str, **data: Any) -> dict[str, Any]:
        return event(
            "run_finished",
            status=status,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            thread_id=thread_id,
            **data,
        )

    def run_started() -> dict[str, Any]:
        return event(
            "run_started",
            thread_id=thread_id,
            flow=spec.name,
            stand_in=opts.stand_in,
            action=action,
        )

    try:
        compiled = compiled or compile_cached(spec, opts.resolve, opts.flow_id)
    except CompileError as exc:
        yield run_started()
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
    flows = _Flows(compiled)
    prepared: dict[str, Any] = {}
    if action == "start":
        try:
            prepared = prepare_inputs(compiled, inputs)
        except InputError as exc:
            yield run_started()
            yield finished(
                "error",
                error={
                    "kind": "bad_input",
                    "message": str(exc),
                    "problems": exc.problems,
                    "fixes": [],
                },
            )
            return
        for name, value in compiled.round_counters.items():
            prepared.setdefault(name, value)

    res = opts.resources or memory_resources()
    module, graph = await asyncio.to_thread(load_graph, compiled.source, compiled.module_name, res)
    tracker = UsageTracker()
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "callbacks": [tracker],
        "recursion_limit": opts.recursion_limit
        or compiled.run_config.get("recursion_limit", DEFAULT_MAX_STEPS),
        "run_name": spec.name,
        "metadata": {**opts.metadata, "run_id": run_id},
    }
    if compiled.run_config.get("max_concurrency"):
        config["max_concurrency"] = compiled.run_config["max_concurrency"]
    thread_config = {"configurable": {"thread_id": thread_id}}

    graph_input: Any
    last_values: dict[str, Any] = dict(prepared)
    if action == "start":
        graph_input = prepared
        if opts.checkpoint_id:
            # Start from an earlier Save Point (rolling a conversation back).
            config["configurable"]["checkpoint_id"] = opts.checkpoint_id
    else:
        try:
            graph_input, last_values = await _prepare_action(graph, opts, config, thread_config)
        except _NotResumable as exc:
            yield run_started()
            yield finished("error", error={"kind": exc.kind, "message": str(exc), "fixes": []})
            return

    yield run_started()
    if action == "start" and an.input_step is not None:
        sid = an.input_step.id
        yield event("step_started", step=sid, input={})
        yield event("step_finished", step=sid, output=to_jsonable(prepared), duration_ms=0)

    # Per task (keyed by its namespace): when it started and which step it is.
    running: dict[str, tuple[float, str, tuple[str, ...]]] = {}
    task_errors: dict[str, tuple[str, tuple[str, ...], str]] = {}
    task_inputs: dict[str, dict[str, Any]] = {}
    executed: set[str] = set()
    child_usage: dict[str, dict[str, Any]] = {}
    totals = {"input_tokens": 0, "output_tokens": 0}
    total_cost: float | None = 0.0
    # For Each: (path, step) -> [started, done, total]
    each: dict[tuple[tuple[str, ...], str], list[Any]] = {}
    pending_jump: dict[tuple[str, ...], str] = {}
    paused_steps: dict[str, dict[str, Any]] = {}
    last_checkpoint: str | None = None

    def where(path: tuple[str, ...], **data: Any) -> dict[str, Any]:
        return {**data, "path": list(path)} if path else data

    def jump_route(path: tuple[str, ...], next_node: str) -> dict[str, Any] | None:
        jump = pending_jump.pop(path, None)
        flow = flows.at(path)
        if jump is None or flow is None:
            return None
        label = flow.jumps.get(jump, {}).get(next_node)
        return event("route", **where(path, step=jump, exit=label)) if label else None

    tool_started: dict[str, float] = {}

    def agent_event(
        ns: tuple[str, ...], path: tuple[str, ...], agent: str, name: str, payload: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Tool calls (and model usage) from inside an Agent step's own graph."""
        nonlocal total_cost
        key = "|".join([*ns, f"{name}:{payload.get('id')}"])
        outer = path[:-1]
        out: list[dict[str, Any]] = []
        if "input" in payload:
            if name == "tools":
                tool_started[key] = time.perf_counter()
                for call in payload.get("input") or []:
                    if isinstance(call, dict) and "name" in call:
                        out.append(
                            event(
                                "tool_started",
                                **where(
                                    outer,
                                    step=agent,
                                    tool=call["name"],
                                    args=to_jsonable(call.get("args") or {}),
                                    call_id=call.get("id"),
                                ),
                            )
                        )
            return out
        own = tracker.take(key)
        if own:
            totals["input_tokens"] += own["input_tokens"]
            totals["output_tokens"] += own["output_tokens"]
            total_cost = (
                None if (total_cost is None or own["cost"] is None) else total_cost + own["cost"]
            )
            _add_usage(child_usage.setdefault("|".join(ns), _empty_usage()), own)
        if name != "tools":
            return out
        began = tool_started.pop(key, time.perf_counter())
        duration = round((time.perf_counter() - began) * 1000, 1)
        if payload.get("error"):
            out.append(
                event(
                    "tool_finished",
                    **where(
                        outer,
                        step=agent,
                        tool=None,
                        call_id=None,
                        result=str(payload["error"])[:2000],
                        status="error",
                        duration_ms=duration,
                    ),
                )
            )
            return out
        result = payload.get("result") or {}
        messages = result.get("messages") if isinstance(result, dict) else None
        for message in messages or []:
            if getattr(message, "type", None) != "tool":
                continue
            out.append(
                event(
                    "tool_finished",
                    **where(
                        outer,
                        step=agent,
                        tool=message.name,
                        call_id=message.tool_call_id,
                        result=to_jsonable(message.text),
                        status=getattr(message, "status", "success") or "success",
                        duration_ms=duration,
                    ),
                )
            )
        return out

    stream_modes = ["tasks", "messages", "values", "checkpoints", "custom"]
    stream_kwargs: dict[str, Any] = {
        "stream_mode": stream_modes,
        "subgraphs": True,
        "durability": "sync" if res.durable else "async",
    }
    if opts.pause_before:
        stream_kwargs["interrupt_before"] = list(opts.pause_before)
    if opts.pause_after:
        stream_kwargs["interrupt_after"] = list(opts.pause_after)

    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def produce() -> None:
        if graph_input is _ALREADY_DONE:
            await queue.put(("done", None))
            return
        try:
            async with contextlib.aclosing(
                graph.astream(graph_input, config, **stream_kwargs)
            ) as stream:
                async for item in stream:
                    await queue.put(("item", item))
            await queue.put(("done", None))
        except asyncio.CancelledError:
            await queue.put(("cancelled", None))
            raise
        except BaseException as exc:  # handed to the consumer below
            await queue.put(("error", exc))

    token = use_settings(RunSettings(stand_in=opts.stand_in, script=opts.script, mcp=opts.mcp))
    producer = asyncio.create_task(produce())
    stop_wait = asyncio.create_task(opts.cancel.wait()) if opts.cancel else None
    cancelled = False
    failure: BaseException | None = None
    try:
        while True:
            getter = asyncio.create_task(queue.get())
            waiting = {getter} | ({stop_wait} if stop_wait else set())
            done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            if getter not in done:
                getter.cancel()
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer
                cancelled = True
                break
            kind, item = getter.result()
            if kind == "done":
                break
            if kind == "error":
                failure = item
                break
            if kind == "cancelled":
                cancelled = True
                break
            ns, mode, payload = item
            path = _path(ns)
            if mode == "values":
                if not ns:
                    last_values = payload
                continue
            if mode == "checkpoints":
                if not ns:
                    cp_id = payload["config"]["configurable"].get("checkpoint_id")
                    last_checkpoint = cp_id
                    yield event(
                        "save_point",
                        checkpoint_id=cp_id,
                        next=list(payload.get("next") or []),
                        step_number=(payload.get("metadata") or {}).get("step"),
                    )
                continue
            if mode == "custom":
                # A step reported progress with get_stream_writer(); credit the step when
                # it is the only one running at that level.
                here = [info for info in running.values() if info[2] == path]
                data = {"path": list(path)} if path else {}
                if len(here) == 1:
                    data["step"] = here[0][1]
                yield event("custom", data=to_jsonable(payload), **data)
                continue
            if mode == "messages":
                chunk, meta = payload
                # Only model output streams as tokens; messages a step writes to state don't.
                if not isinstance(chunk, AIMessageChunk) or not chunk.text:
                    continue
                node = meta.get("langgraph_node")
                agent = _agent_at(flows, path)
                if agent is not None:
                    if node == "model":
                        yield event("token", **where(path[:-1], step=agent, text=chunk.text))
                    continue
                flow = flows.at(path)
                if flow is not None and node in flow.analysis.steps:
                    yield event("token", **where(path, step=node, text=chunk.text))
                continue
            if mode != "tasks":
                continue

            name = payload.get("name")
            agent = _agent_at(flows, path)
            if agent is not None and name is not None:
                for ev in agent_event(ns, path, agent, name, payload):
                    yield ev
                continue
            flow = flows.at(path)
            if flow is None or name is None:
                continue
            fan = flow.analysis
            step_id = flow.node_steps.get(name, name)
            if step_id not in fan.steps:
                continue
            is_done_node = step_id != name
            key = "|".join([*ns, f"{name}:{payload.get('id')}"])

            if "input" in payload:
                if (ev := jump_route(path, name)) is not None:
                    yield ev
                if is_done_node:
                    continue
                state = payload.get("input") or {}
                running[key] = (time.perf_counter(), step_id, path)
                task_inputs[key] = state
                extra: dict[str, Any] = {}
                parent_each = fan.foreach_body.get(step_id)
                if parent_each is not None:
                    extra["item"] = state.get(index_field(fan.steps[parent_each]))
                step = fan.steps[step_id]
                if step.type == "for_each":
                    items = state.get(fan.handlers[step_id].items_field(step, fan) or "")
                    total = len(items) if isinstance(items, list) else (0 if items is None else 1)
                    each[(path, step_id)] = [time.perf_counter(), 0, total]
                reads = fan.reads.get(step_id, set())
                yield event(
                    "step_started",
                    **where(
                        path,
                        step=step_id,
                        input=to_jsonable({k: v for k, v in state.items() if k in reads}),
                        **extra,
                    ),
                )
                continue

            began, _, _ = running.pop(key, (time.perf_counter(), step_id, path))
            if payload.get("error"):
                task_errors[key] = (step_id, path, str(payload["error"]))
                continue
            interrupts = payload.get("interrupts") or []
            if interrupts:
                for intr in interrupts:
                    paused_steps.setdefault(intr["id"], {"step": step_id, "path": list(path)})
                if not path or fan.steps[step_id].type == "ask_human":
                    first = interrupts[0]
                    yield event(
                        "step_paused",
                        **where(
                            path,
                            step=step_id,
                            interrupt_id=first["id"],
                            request=to_jsonable(describe_request(first["value"])),
                        ),
                    )
                continue
            executed.add(step_id)
            result = payload.get("result") or {}
            step = fan.steps[step_id]

            if step.type == "for_each" and not is_done_node:
                yield event("route", **where(path, step=step_id, exit=EACH_ITEM))
                counter = each.get((path, step_id))
                if counter is not None and counter[2]:
                    yield event("progress", **where(path, step=step_id, done=0, total=counter[2]))
                continue

            own = tracker.take(key)
            nested = child_usage.pop(key, None)
            if own:
                totals["input_tokens"] += own["input_tokens"]
                totals["output_tokens"] += own["output_tokens"]
                total_cost = (
                    None
                    if (total_cost is None or own["cost"] is None)
                    else total_cost + own["cost"]
                )
                # Count it toward the Sub-flow steps this task runs inside.
                parts = key.split("|")
                for depth in range(1, len(parts)):
                    _add_usage(child_usage.setdefault("|".join(parts[:depth]), _empty_usage()), own)
            usage: dict[str, Any] | None = None
            if own or nested:
                usage = _empty_usage()
                for part in (own, nested):
                    if part:
                        _add_usage(usage, part)

            data: dict[str, Any] = {"step": step_id, "output": to_jsonable(result)}
            if is_done_node:
                counter = each.pop((path, step_id), None)
                began = counter[0] if counter else began
            data["duration_ms"] = round((time.perf_counter() - began) * 1000, 1)
            parent_each = fan.foreach_body.get(step_id)
            if parent_each is not None:
                data["item"] = task_inputs.get(key, {}).get(index_field(fan.steps[parent_each]))
            if usage:
                data["usage"] = {
                    "input_tokens": usage["input_tokens"],
                    "output_tokens": usage["output_tokens"],
                }
                data["cost"] = usage["cost"]
                data["model"] = usage["model"]
            yield event("step_finished", **where(path, **data))
            if is_done_node:
                yield event("route", **where(path, step=step_id, exit=WHEN_DONE))
            elif parent_each is not None:
                counter = each.get((path, parent_each))
                if counter is not None:
                    counter[1] += 1
                    yield event(
                        "progress",
                        **where(path, step=parent_each, done=counter[1], total=counter[2]),
                    )
            if step_id in flow.routers and not is_done_node:
                # A router reads the state as it is right after the step.
                state = {**task_inputs.pop(key, {}), **result}
                try:
                    label = getattr(module, flow.routers[step_id])(state)
                except Exception:  # routing errors surface from LangGraph itself
                    label = None
                if isinstance(label, str):
                    yield event("route", **where(path, step=step_id, exit=label))
            else:
                task_inputs.pop(key, None)
            if step.type == "jump":
                pending_jump[path] = step_id
    finally:
        if stop_wait is not None:
            stop_wait.cancel()
        if not producer.done():
            producer.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer
        with contextlib.suppress(ValueError):  # generator closed from another context
            reset_settings(token)

    if failure is not None:
        errors = list(task_errors.values())
        # The deepest failure is the real one; the steps around it fail because of it.
        errors.sort(key=lambda e: -len(e[1]))
        failed_step, failed_path = (errors[0][0], errors[0][1]) if errors else (None, ())
        if failed_step is None and running:
            _, failed_step, failed_path = next(iter(running.values()))
        flow = flows.at(failed_path) if failed_step else None
        step_obj = flow.analysis.steps.get(failed_step) if flow and failed_step else None
        if (
            isinstance(failure, GraphRecursionError)
            and step_obj is not None
            and step_obj.type == "agent"
        ):
            info = {
                "kind": "too_many_steps",
                "message": f"The agent “{step_obj.name or step_obj.id}” took more than "
                f"{step_obj.settings.max_steps} rounds without finishing.",
                "hint": "Give it a model-call or tool-call limit under Add-ons, make its "
                "instructions clearer, or raise its Most rounds.",
                "fixes": [],
            }
        elif isinstance(failure, GraphRecursionError):
            info = _recursion_error(compiled, config["recursion_limit"])
        else:
            info = explain(failure, step_obj)
        top = failed_path[0] if failed_path else failed_step
        if failed_step:
            yield event("step_failed", **where(failed_path, step=failed_step, error=info))
            if failed_path:
                yield event("step_failed", step=top, error=info)
        yield finished(
            "error",
            error=info,
            step=top,
            usage=totals,
            cost=total_cost,
            checkpoint_id=last_checkpoint,
        )
        return

    if cancelled:
        yield finished(
            "cancelled",
            output=to_jsonable(_outputs(compiled, last_values)),
            usage=totals,
            cost=total_cost,
            checkpoint_id=last_checkpoint,
        )
        return

    snapshot = await graph.aget_state(thread_config)
    last_checkpoint = snapshot.config["configurable"].get("checkpoint_id") or last_checkpoint
    if snapshot.values:
        last_values = snapshot.values
    output = _outputs(compiled, last_values)
    if snapshot.next:
        waiting = [
            {
                "id": intr.id,
                **paused_steps.get(intr.id, _interrupt_step(intr.value)),
                "request": to_jsonable(describe_request(intr.value)),
            }
            for intr in snapshot.interrupts
        ]
        reason = "ask_human" if waiting else "breakpoint"
        yield event("paused", reason=reason, interrupts=waiting, next=list(snapshot.next))
        yield finished(
            "paused",
            reason=reason,
            interrupts=waiting,
            next=list(snapshot.next),
            output=to_jsonable(output),
            usage=totals,
            cost=total_cost,
            checkpoint_id=last_checkpoint,
        )
        return

    if (ev := jump_route((), END)) is not None:
        yield ev
    reply = None
    if an.chat and last_values.get("messages"):
        last = last_values["messages"][-1]
        reply = last.text if isinstance(last, BaseMessage) else str(last)
    for out_step in an.output_steps:
        if any(c.source in executed for c in an.incoming[out_step.id]) or (
            action != "start" and an.incoming[out_step.id]
        ):
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
        checkpoint_id=last_checkpoint,
    )


class _NotResumable(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


async def _prepare_action(
    graph: Any, opts: RunOptions, config: dict[str, Any], thread_config: dict[str, Any]
) -> tuple[Any, dict[str, Any]]:
    """The graph input and the Flow Data so far for resume, continue and fork."""
    if opts.action == "fork":
        if not opts.checkpoint_id:
            raise _NotResumable("no_save_point", "Pick the Save Point to run from.")
        at = {
            "configurable": {
                **thread_config["configurable"],
                "checkpoint_ns": "",
                "checkpoint_id": opts.checkpoint_id,
            }
        }
        snapshot = await graph.aget_state(at)
        if not snapshot.config or snapshot.created_at is None:
            raise _NotResumable("no_save_point", "That Save Point doesn't exist (any more).")
        if opts.update:
            try:
                at = await graph.aupdate_state(at, opts.update)
            except Exception as exc:
                raise _NotResumable(
                    "bad_update", f"Couldn't change the Flow Data there: {exc}"
                ) from exc
        config["configurable"].update(
            {k: v for k, v in at["configurable"].items() if k in ("checkpoint_id", "checkpoint_ns")}
        )
        return None, {**snapshot.values, **(opts.update or {})}

    snapshot = await graph.aget_state(thread_config)
    if not snapshot.created_at:
        raise _NotResumable("no_run", "There's nothing to continue in this conversation yet.")
    if opts.action == "resume":
        if not snapshot.interrupts:
            raise _NotResumable("not_waiting", "This run isn't waiting for an answer.")
        waiting = {intr.id: intr.value for intr in snapshot.interrupts}
        resume = opts.resume
        if isinstance(resume, dict) and resume and any(k in waiting for k in resume):
            # Each answer goes to the question it was given for. Answers to questions that
            # aren't waiting any more (the conversation moved on) are left out.
            resume = {k: _tool_decisions(waiting[k], v) for k, v in resume.items() if k in waiting}
        elif (
            isinstance(resume, dict) and resume and all(_INTERRUPT_ID.match(str(k)) for k in resume)
        ):
            raise _NotResumable(
                "not_waiting",
                "The question this answers isn't waiting any more: the conversation has moved "
                "on (for example, a newer run continued it). Answer its newest question instead.",
            )
        elif len(waiting) == 1:
            resume = _tool_decisions(next(iter(waiting.values())), resume)
        return Command(resume=resume), dict(snapshot.values)
    if not snapshot.next:
        if opts.finish_if_done:
            return _ALREADY_DONE, dict(snapshot.values)
        raise _NotResumable("finished", "This run has already finished.")
    return None, dict(snapshot.values)


_ALREADY_DONE = object()
# LangGraph interrupt ids: an xxh3-128 hex digest of the task's namespace.
_INTERRUPT_ID = re.compile(r"^[0-9a-f]{32}$")


def _interrupt_step(value: Any) -> dict[str, Any]:
    step = value.get("step") if isinstance(value, dict) else None
    return {"step": step, "path": []}


def _outputs(compiled: CompiledFlow, values: dict[str, Any]) -> dict[str, Any]:
    names = compiled.output_fields
    return {k: v for k, v in values.items() if k in names} if names else dict(values)


def _recursion_error(compiled: CompiledFlow, limit: int) -> dict[str, Any]:
    return {
        "kind": "too_many_steps",
        "message": f"The run stopped after {limit} rounds of steps; a loop may never end.",
        "hint": "Give the loop's Decision a round limit, or raise “Most rounds of steps” in the "
        "flow settings.",
        "fixes": [],
    }


async def run_flow(
    spec: FlowSpec, inputs: dict[str, Any] | None = None, options: RunOptions | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run to completion (or a pause); returns (run_finished event, all events)."""
    events = [e async for e in stream_run(spec, inputs, options)]
    return events[-1], events
