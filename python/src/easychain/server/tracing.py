"""Tracing (Phase 5.7, docs/tracing.md): a workspace's runs sent to an OpenTelemetry collector
and to LangSmith (Settings → Tracing, per workspace).

**OpenTelemetry.** When a worker has run part of a run (its start, an answer to Ask a Human, a
continue), that part's events become spans, sent over OTLP/HTTP:

- one span for the part (``run <flow>``);
- one per step (a Sub-flow's steps under the Sub-flow), with its tokens, cost and model;
- one per tool call, under its step;
- one per model call (from ``model_calls``), under its step, with the GenAI attributes
  (``gen_ai.request.model``, ``gen_ai.usage.input_tokens``, …).

The trace id comes from the run id, so every part of a run that waited for someone is one
trace. Spans are made after the part ends, from the events' times, so tracing never slows a
run down.

**LangSmith.** LangChain's own tracer is added to the run's callbacks, with the workspace's
key (``LANGSMITH_API_KEY`` in its secrets), project and endpoint.
"""

from __future__ import annotations

import contextvars
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, Field, field_validator

from .secrets import MASK, masked, unmasked

log = logging.getLogger("easychain.tracing")


class OtlpSettings(BaseModel):
    enabled: bool = False
    # The collector's OTLP/HTTP address, like http://collector:4318 (/v1/traces is added)
    endpoint: str = ""
    # Sent with each export, such as an API key; a value can be {secret:NAME}
    headers: dict[str, str] = Field(default_factory=dict)
    service_name: str = "easychain"

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^https?://[^\s/]+", value):
            raise ValueError("Give the collector's address, like http://collector:4318.")
        return value


class LangSmithSettings(BaseModel):
    enabled: bool = False
    project: str = "easychain"
    endpoint: str = "https://api.smith.langchain.com"


class TracingSettings(BaseModel):
    otlp: OtlpSettings = Field(default_factory=OtlpSettings)
    langsmith: LangSmithSettings = Field(default_factory=LangSmithSettings)


def shown(settings: TracingSettings) -> dict[str, Any]:
    """The settings as the API shows them: header values hidden (unless {secret:NAME})."""
    out = settings.model_dump()
    out["otlp"]["headers"] = {k: masked(v) for k, v in settings.otlp.headers.items()}
    return out


def to_save(sent: TracingSettings, saved: TracingSettings) -> TracingSettings:
    """A header sent back hidden keeps its saved value."""
    new = [k for k, v in sent.otlp.headers.items() if v == MASK and k not in saved.otlp.headers]
    if new:
        raise ValueError(f"The header {new[0]} is hidden (••••••) but new here: type its value.")
    headers = {k: unmasked(v, saved.otlp.headers.get(k)) for k, v in sent.otlp.headers.items() if k}
    return sent.model_copy(update={"otlp": sent.otlp.model_copy(update={"headers": headers})})


def _resolve(text: str) -> str:
    return re.sub(
        r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}", lambda m: os.environ.get(m.group(1), ""), text
    )


# ── OpenTelemetry ───────────────────────────────────────────────────────────

_trace_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "easychain_trace_id", default=None
)


class RunIds(RandomIdGenerator):
    """Trace ids from run ids (set around a run's first span); span ids at random."""

    def generate_trace_id(self) -> int:
        return _trace_id.get() or super().generate_trace_id()


def trace_id_of(run_id: str) -> int:
    return int(hashlib.sha256(run_id.encode()).hexdigest()[:32], 16) or 1


def make_exporter(settings: OtlpSettings) -> SpanExporter:
    """The OTLP/HTTP exporter for these settings (tests put an in-memory one here)."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    endpoint = settings.endpoint.rstrip("/")
    if not endpoint.endswith("/v1/traces"):
        endpoint += "/v1/traces"
    headers = {k: _resolve(v) for k, v in settings.headers.items()}
    return OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=10)


_providers: dict[str, TracerProvider] = {}
_providers_lock = threading.Lock()


def provider_for(settings: OtlpSettings) -> TracerProvider:
    """One provider (with its exporting thread) per collector, headers and service name."""
    key = settings.model_dump_json()
    with _providers_lock:
        found = _providers.get(key)
        if found is None:
            found = TracerProvider(
                resource=Resource.create({"service.name": settings.service_name or "easychain"}),
                id_generator=RunIds(),
            )
            found.add_span_processor(BatchSpanProcessor(make_exporter(settings)))
            _providers[key] = found
        return found


def _ns(ms: float | None) -> int | None:
    return int(ms * 1_000_000) if ms is not None else None


def _attrs(values: dict[str, Any]) -> dict[str, Any]:
    """OpenTelemetry attributes: strings, numbers and booleans only, no Nones."""
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            continue
        out[key] = value if isinstance(value, str | bool | int | float) else str(value)
    return out


@dataclass
class _Part:
    """A span to make: its name, parent, times (ms), attributes and status."""

    name: str
    parent: _Part | None
    start: float
    end: float | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    kind: SpanKind = SpanKind.INTERNAL
    step: str | None = None

    def widen(self, start: float, end: float) -> None:
        """Hold a child that began earlier or ended later (event times are when the run heard
        of a step, a moment after it began), and so do its parents."""
        self.start = min(self.start, start)
        self.end = max(self.end if self.end is not None else end, end)
        if self.parent is not None:
            self.parent.widen(self.start, self.end)


def export_run(
    tracer: Any,
    run: dict[str, Any],
    events: list[dict[str, Any]],
    calls: list[dict[str, Any]],
) -> int:
    """Turn one part of a run (its events, and its model calls) into spans; how many."""
    timed = [e for e in events if e.get("ts") is not None]
    if not timed:
        return 0
    first, last = timed[0]["ts"], timed[-1]["ts"]
    finished = next((e for e in reversed(events) if e.get("type") == "run_finished"), {})
    usage = finished.get("usage") or {}
    root = _Part(
        f"run {run.get('flow_name') or 'flow'}",
        None,
        first,
        last,
        attrs={
            "easychain.run_id": run["id"],
            "easychain.flow_id": run.get("flow_id"),
            "easychain.flow": run.get("flow_name"),
            "easychain.environment": run.get("environment"),
            "easychain.trigger": run.get("trigger"),
            "easychain.thread_id": run.get("thread_id"),
            "easychain.status": finished.get("status"),
            "easychain.cost": finished.get("cost"),
            "gen_ai.usage.input_tokens": usage.get("input_tokens"),
            "gen_ai.usage.output_tokens": usage.get("output_tokens"),
        },
    )
    if finished.get("status") == "error":
        root.error = str((finished.get("error") or {}).get("message") or "")
    parts: list[_Part] = [root]
    # Steps running now: (path, step, item) -> their part; tools by call id.
    running: dict[tuple[Any, ...], _Part] = {}
    steps: list[_Part] = []
    tools: dict[str, _Part] = {}

    def step_part(path: list[str], step: str | None) -> _Part | None:
        """The latest running part of ``step`` at ``path`` (any For Each item)."""
        for (p, name, _item), part in reversed(list(running.items())):
            if name == step and list(p) == path:
                return part
        return None

    for ev in events:
        kind, path, ts = ev.get("type"), list(ev.get("path") or []), ev.get("ts") or last
        key = (tuple(path), ev.get("step"), ev.get("item"))
        if kind == "step_started":
            # A Sub-flow's steps sit under the Sub-flow step that runs them.
            parent = (step_part(path[:-1], path[-1]) if path else None) or root
            part = _Part(
                f"step {ev['step']}",
                parent,
                ts,
                attrs={"easychain.step": ev["step"], "easychain.item": ev.get("item")},
                step=ev["step"],
            )
            running[key] = part
            parts.append(part)
            steps.append(part)
        elif kind in ("step_finished", "step_failed", "step_paused") and key in running:
            part = running.pop(key)
            part.end = max(ts, part.start)
            ev_usage = ev.get("usage") or {}
            part.attrs.update(
                {
                    "easychain.duration_ms": ev.get("duration_ms"),
                    "easychain.cost": ev.get("cost"),
                    "gen_ai.request.model": ev.get("model"),
                    "gen_ai.usage.input_tokens": ev_usage.get("input_tokens"),
                    "gen_ai.usage.output_tokens": ev_usage.get("output_tokens"),
                    # Ask a Human: the step goes on when someone answers (a later part).
                    "easychain.paused": True if kind == "step_paused" else None,
                }
            )
            if kind == "step_failed":
                part.error = str((ev.get("error") or {}).get("message") or "")
        elif kind == "tool_started":
            part = _Part(
                f"tool {ev.get('tool')}",
                step_part(path, ev.get("step")) or root,
                ts,
                attrs={"easychain.step": ev.get("step"), "gen_ai.tool.name": ev.get("tool")},
            )
            tools[ev.get("call_id") or f"{ev.get('tool')}:{ts}"] = part
            parts.append(part)
        elif kind == "tool_finished":
            part = tools.pop(ev.get("call_id") or "", None)
            if part is not None:
                part.end = max(ts, part.start)
                if ev.get("status") == "error":
                    part.error = "the tool failed"

    # Steps or tools left running (a stopped run) end with the part.
    for part in [*running.values(), *tools.values()]:
        part.end = last
        part.attrs["easychain.unfinished"] = True
    for part in parts:
        if part.end is None:
            part.end = last
        elif part.parent is not None:
            part.parent.widen(part.start, part.end)

    for call in calls:
        ended = (call.get("at") or 0) * 1000
        began = ended - (call.get("duration_ms") or 0)
        # Under the step that made it: the run of that step nearest the call.
        mine = [p for p in steps if p.step == call.get("step")]
        parent = min(
            mine,
            key=lambda p: (max(0.0, p.start - ended, began - (p.end or last)), -p.start),
            default=root,
        )
        part = _Part(
            f"chat {call.get('model')}",
            parent,
            began,
            ended,
            kind=SpanKind.CLIENT,
            attrs={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": call.get("provider"),
                "gen_ai.request.model": call.get("model"),
                "gen_ai.usage.input_tokens": call.get("input_tokens"),
                "gen_ai.usage.output_tokens": call.get("output_tokens"),
                "easychain.cost": call.get("cost"),
                "easychain.cached": call.get("cached"),
            },
            error=call.get("error"),
        )
        parent.widen(began, ended)
        parts.append(part)

    # Make the spans, parents first (they come first in ``parts``).
    made: dict[int, Any] = {}
    for part in parts:
        context = trace.set_span_in_context(made[id(part.parent)]) if part.parent else None
        token = _trace_id.set(trace_id_of(run["id"])) if part.parent is None else None
        try:
            span = tracer.start_span(
                part.name,
                context=context,
                kind=part.kind,
                start_time=_ns(part.start),
                attributes=_attrs(part.attrs),
            )
        finally:
            if token is not None:
                _trace_id.reset(token)
        made[id(part)] = span
    for part in reversed(parts):
        span = made[id(part)]
        if part.error is not None:
            span.set_status(Status(StatusCode.ERROR, part.error[:500]))
        span.end(end_time=_ns(part.end))
    return len(parts)


def send_test(settings: OtlpSettings) -> dict[str, Any]:
    """Send one span straight to the collector and say whether it took it."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult

    result: dict[str, Any] = {}

    class Recorder(SpanExporter):
        def __init__(self, inner: SpanExporter):
            self.inner = inner

        def export(self, spans: Any) -> SpanExportResult:
            outcome = self.inner.export(spans)
            result["ok"] = outcome == SpanExportResult.SUCCESS
            return outcome

        def shutdown(self) -> None:
            self.inner.shutdown()

    provider = TracerProvider(
        resource=Resource.create({"service.name": settings.service_name or "easychain"})
    )
    provider.add_span_processor(SimpleSpanProcessor(Recorder(make_exporter(settings))))
    provider.get_tracer("easychain").start_span("Easy Chain test span").end()
    provider.shutdown()
    if not result.get("ok"):
        result["error"] = (
            "The collector didn't take the span. Check the address, that it accepts OTLP over "
            "HTTP, and the headers (the server's log has the details)."
        )
    return result


# ── LangSmith ───────────────────────────────────────────────────────────────


def langsmith_tracer(settings: LangSmithSettings, metadata: dict[str, Any]) -> Any | None:
    """LangChain's tracer for a run, with the workspace's key; None without one."""
    key = os.environ.get("LANGSMITH_API_KEY")
    if not settings.enabled or not key:
        return None
    from langchain_core.tracers.langchain import LangChainTracer
    from langsmith import Client

    client = Client(api_url=settings.endpoint or None, api_key=key)
    return LangChainTracer(
        project_name=settings.project or "easychain",
        client=client,
        metadata={k: str(v) for k, v in metadata.items() if v is not None},
    )


def flush_langsmith(tracer: Any) -> None:
    """Wait until the run's traces have been sent (in a thread: it can take a moment)."""
    try:
        tracer.wait_for_futures()
        tracer.client.flush()
    except Exception:  # pragma: no cover - tracing must never break a run
        log.exception("Couldn't send the run's traces to LangSmith")
