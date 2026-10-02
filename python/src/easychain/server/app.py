"""The Easy Chain API server (FastAPI).

REST for flows, the step catalog, checks, code and exports; Server-Sent Events
for runs. It also serves the built web app, so one process is the whole app.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets as secrets_module
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..compiler import CompileError, FlowAnalysis, compile_flow, validate
from ..compiler.issues import Fix, warning
from ..compiler.templates import secrets as template_secrets
from ..export import export_zip
from ..providers import EMBEDDING_MODELS, PROVIDERS, split_model
from ..runtime import key_status
from ..runtime.resources import open_resources
from ..spec import FlowSpec, SpecError, loads_spec, parse_spec, spec_json
from ..spec.models import SPEC_VERSION
from ..spec.schema import flow_json_schema
from ..steps import catalog as step_catalog
from ..templates import list_templates, load_template
from ..templates.samples import ensure_sample_knowledge, ensure_samples
from . import notify
from .cron import CronError, next_fire
from .cron import describe as describe_cron
from .db import Database, checkpoint_url
from .hub import Busy, Hub, Invalid, NotFound
from .integrations_api import add_integration_routes
from .knowledge_api import add_knowledge_routes, use_database
from .secrets import SecretStore
from .store import FlowNotFound, FlowStore
from .worker import Worker
from .worker import fire as fire_trigger

log = logging.getLogger("easychain.server")

STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    flow_id: str | None = None
    spec: dict[str, Any] | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    stand_in: bool = False
    pause_before: list[str] = Field(default_factory=list)
    pause_after: list[str] = Field(default_factory=list)
    # Return {run_id} straight away instead of streaming the run's events.
    background: bool = False
    trigger: str | None = None


class ResumeRequest(BaseModel):
    # {interrupt_id: answer}; or one answer when only one step is waiting.
    answers: dict[str, Any] | None = None
    answer: Any = None
    by: str | None = None
    background: bool = False


class ForkRequest(BaseModel):
    checkpoint_id: str
    update: dict[str, Any] | None = None
    pause_before: list[str] = Field(default_factory=list)
    pause_after: list[str] = Field(default_factory=list)
    background: bool = False


class AnswerRequest(BaseModel):
    action: str = "approve"
    value: Any = None
    comment: str = ""
    by: str | None = None


class TriggerRequest(BaseModel):
    flow_id: str
    kind: str = Field(pattern="^(webhook|schedule|upload|after_flow|email)$")
    name: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class TriggerUpdate(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    config: dict[str, Any] | None = None


class CreateFlowRequest(BaseModel):
    template: str | None = None
    name: str | None = None
    spec: dict[str, Any] | None = None
    yaml: str | None = None


class SecretValue(BaseModel):
    value: str


def _spec_or_422(data: dict[str, Any] | None) -> FlowSpec:
    if data is None:
        raise HTTPException(422, detail={"message": "Send a flow spec.", "problems": []})
    try:
        return parse_spec(data)
    except SpecError as exc:
        raise HTTPException(
            422, detail={"message": "The flow isn't valid.", "problems": exc.problems}
        ) from exc


def blank_flow(name: str = "My flow") -> FlowSpec:
    return parse_spec(
        {
            "name": name,
            "steps": [
                {
                    "id": "input",
                    "type": "input",
                    "name": "Input",
                    "settings": {"fields": [{"name": "question", "description": "What to ask"}]},
                },
                {"id": "output", "type": "output", "name": "Output"},
            ],
            "canvas": {"steps": {"input": {"x": 0, "y": 120}, "output": {"x": 720, "y": 120}}},
        }
    )


def default_database_url(home: Path) -> str:
    return os.environ.get("EASYCHAIN_DATABASE_URL") or f"sqlite:///{home / 'easychain.db'}"


def create_app(
    workspace: str | Path | None = None,
    home: str | Path | None = None,
    static_dir: str | Path | None = None,
    database_url: str | None = None,
    worker: bool | None = None,
    worker_options: dict[str, Any] | None = None,
) -> FastAPI:
    """The API app. ``worker`` runs jobs inside this process too (the default for one-person use;
    set EASYCHAIN_WORKER=off when separate ``easychain worker`` processes do the work)."""
    home_path = Path(home or os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    workspace_path = Path(workspace or os.environ.get("EASYCHAIN_WORKSPACE") or home_path / "flows")
    flows = FlowStore(workspace_path)
    vault = SecretStore(home_path)
    web_dir = Path(static_dir or os.environ.get("EASYCHAIN_WEB_DIST") or STATIC_DIR)
    db_url = database_url or default_database_url(home_path)
    inline_worker = (
        worker if worker is not None else os.environ.get("EASYCHAIN_WORKER", "inline") != "off"
    )

    def prepare_samples() -> None:
        """The sample shop database and help-centre Knowledge Base the templates use."""
        try:
            ensure_samples(home_path)
            ensure_sample_knowledge()
        except Exception as exc:  # samples are a convenience; never block startup
            log.warning("Couldn't prepare the template samples: %s", exc)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        use_database(db_url)
        await asyncio.to_thread(prepare_samples)
        db = await Database.connect(db_url)
        resources = await open_resources(checkpoint_url(db_url))
        app.state.hub = Hub(db, resources, flows, vault)
        stop = asyncio.Event()
        task = None
        if inline_worker:
            runner = Worker(app.state.hub, **(worker_options or {}))
            app.state.worker = runner
            task = asyncio.create_task(runner.run(stop))
        try:
            yield
        finally:
            stop.set()
            if task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, 15)
            await resources.aclose()
            await db.close()

    app = FastAPI(
        title="Easy Chain",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.flows, app.state.vault = flows, vault
    add_knowledge_routes(app, home_path)
    origins = os.environ.get(
        "EASYCHAIN_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o for o in origins.split(",") if o],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── meta ────────────────────────────────────────────────────────────────

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        hub_ = getattr(app.state, "hub", None)
        return {
            "ok": True,
            "version": __version__,
            "spec_version": SPEC_VERSION,
            "workspace": str(workspace_path),
            "database": hub_.db.dialect if hub_ else None,
            "worker": "inline" if inline_worker else "external",
        }

    def providers_payload() -> list[dict[str, Any]]:
        status = key_status()
        return [
            {
                "id": p.id,
                "label": p.label,
                "key_env": p.key_env,
                "key_label": p.key_label,
                "key_url": p.key_url,
                "key_set": status[p.id],
                "key_source": vault.source(p.key_env) if p.key_env else None,
                "installed": p.installed(),
                "install": (f"pip install 'easychain[{p.extra}]'" if p.extra else None),
                "credentials": p.credentials,
                "settings": [
                    {"env": env, "label": label, "set": bool(os.environ.get(env))}
                    for env, label in p.settings_env
                ],
                "models": [
                    {
                        "id": f"{p.id}:{m.id}",
                        "label": m.label,
                        "input_per_m": m.input_per_m,
                        "output_per_m": m.output_per_m,
                    }
                    for m in p.models
                ],
            }
            for p in PROVIDERS.values()
        ]

    @app.get("/api/catalog")
    def catalog() -> dict[str, Any]:
        return {
            **step_catalog(),
            "providers": providers_payload(),
            "embedding_models": [
                {"id": m.id, "label": m.label, "dims": m.dims, "provider": m.provider}
                for m in EMBEDDING_MODELS.values()
            ],
            "spec_version": SPEC_VERSION,
            "field_types": [
                "text",
                "number",
                "yes_no",
                "list",
                "object",
                "file",
                "messages",
                "any",
            ],
            "update_rules": ["replace", "append", "merge", "add", "custom"],
        }

    @app.get("/api/schema")
    def schema() -> dict[str, Any]:
        return flow_json_schema()

    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        return providers_payload()

    @app.get("/api/templates")
    def templates() -> list[dict[str, Any]]:
        status = key_status()
        return [
            {**t, "keys": [{**k, "set": status.get(k["provider"], False)} for k in t["keys"]]}
            for t in list_templates()
        ]

    # ── flows ───────────────────────────────────────────────────────────────

    @app.get("/api/flows")
    def list_flows() -> list[dict[str, Any]]:
        return flows.list()

    @app.post("/api/flows", status_code=201)
    def create_flow(req: CreateFlowRequest) -> dict[str, Any]:
        if req.template:
            try:
                spec = load_template(req.template)
            except KeyError:
                raise HTTPException(
                    404, detail={"message": f"No template called '{req.template}'."}
                ) from None
            if req.name:
                spec = spec.model_copy(update={"name": req.name})
        elif req.yaml:
            try:
                spec = loads_spec(req.yaml)
            except SpecError as exc:
                raise HTTPException(
                    422,
                    detail={"message": "That file isn't a valid flow.", "problems": exc.problems},
                ) from exc
        elif req.spec:
            spec = _spec_or_422(req.spec)
        else:
            spec = blank_flow(req.name or "My flow")
        flow_id = flows.create(spec)
        return {"id": flow_id, "spec": spec_json(spec)}

    def resolve_flow(flow_id: str) -> FlowSpec | None:
        """Flows used as Sub-flows come from the workspace."""
        try:
            return flows.get(flow_id)
        except (FlowNotFound, SpecError):
            return None

    def _get(flow_id: str) -> FlowSpec:
        try:
            return flows.get(flow_id)
        except FlowNotFound:
            raise HTTPException(
                404, detail={"message": "That flow doesn't exist (any more)."}
            ) from None
        except SpecError as exc:
            raise HTTPException(
                422,
                detail={"message": "The saved flow file has problems.", "problems": exc.problems},
            ) from exc

    @app.get("/api/flows/{flow_id}")
    def get_flow(flow_id: str) -> dict[str, Any]:
        return {"id": flow_id, "spec": spec_json(_get(flow_id))}

    @app.put("/api/flows/{flow_id}")
    async def save_flow(flow_id: str, spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        try:
            flows.save(flow_id, parsed)
        except FlowNotFound:
            raise HTTPException(404, detail={"message": "Bad flow id."}) from None
        version_id = None
        hub_ = getattr(app.state, "hub", None)
        if hub_ is not None:
            version_id = await hub_.db.save_version(flow_id, hub_.bundle(parsed), note="saved")
        return {"id": flow_id, "saved": True, "version_id": version_id}

    @app.delete("/api/flows/{flow_id}")
    def delete_flow(flow_id: str) -> dict[str, Any]:
        try:
            flows.delete(flow_id)
        except FlowNotFound:
            raise HTTPException(404, detail={"message": "That flow doesn't exist."}) from None
        return {"deleted": True}

    @app.get("/api/flows/{flow_id}/yaml", response_class=PlainTextResponse)
    def flow_yaml(flow_id: str) -> str:
        try:
            return flows.yaml(flow_id)
        except FlowNotFound:
            raise HTTPException(404, detail={"message": "That flow doesn't exist."}) from None

    @app.get("/api/flows/{flow_id}/export")
    def export_saved(flow_id: str) -> Response:
        return _export(_get(flow_id), flow_id)

    # ── checks, code, export ───────────────────────────────────────────────

    def knowledge_issues(spec: FlowSpec) -> list[Any]:
        """Knowledge Base search steps: the base exists here and was built with the same model."""
        steps = [
            s for s in spec.steps if s.type == "knowledge_search" and s.settings.knowledge_base
        ]
        if not steps:
            return []
        from ..knowledge.store import KnowledgeStore

        try:
            store = KnowledgeStore().setup()
        except Exception:  # the knowledge database isn't reachable: the run will say so
            return []
        issues = []
        for step in steps:
            base = store.get_base(step.settings.knowledge_base)
            if base is None:
                issues.append(
                    warning(
                        "knowledge_base_missing",
                        f"There's no Knowledge Base “{step.settings.knowledge_base}” here yet.",
                        step=step.id,
                        setting="knowledge_base",
                        hint="Make it on the Knowledge page, or pick another one.",
                    )
                )
            elif base["embedding_model"] != step.settings.embedding_model:
                issues.append(
                    warning(
                        "knowledge_model_mismatch",
                        f"This step searches with {step.settings.embedding_model}, but the "
                        f"Knowledge Base was built with {base['embedding_model']}.",
                        step=step.id,
                        setting="knowledge_base",
                        fix=Fix(
                            "set_setting",
                            f"Use {base['embedding_model']}",
                            {"key": "embedding_model", "value": base["embedding_model"]},
                        ),
                    )
                )
        return issues

    def runtime_issues(spec: FlowSpec) -> list[Any]:
        """Checks that depend on this machine: missing API keys and secrets."""
        issues = []
        status = key_status()
        for step in spec.steps:
            model = getattr(step.settings, "model", None)
            if not model or (step.type == "decision" and step.settings.mode != "ai"):
                continue
            provider = PROVIDERS.get(split_model(model)[0] or "")
            if provider and not status.get(provider.id, True):
                issues.append(
                    warning(
                        "missing_key",
                        f"Add your {provider.key_label} to run this step (or use the stand-in AI).",
                        step=step.id,
                        setting="model",
                        fix=Fix(
                            "add_key",
                            "Add your API key",
                            {"provider": provider.id, "env": provider.key_env},
                        ),
                    )
                )
        issues += knowledge_issues(spec)
        for step in spec.steps:
            if step.type == "sql_query":
                texts = [step.settings.connection]
            elif step.type == "http_request":
                texts = [step.settings.url, step.settings.body, *step.settings.headers.values()]
            else:
                continue
            for name in sorted({n for t in texts for n in template_secrets(t)}):
                if not vault.has(name):
                    issues.append(
                        warning(
                            "missing_secret",
                            f"The secret {name} isn't set yet.",
                            step=step.id,
                            setting="connection" if step.type == "sql_query" else "headers",
                            fix=Fix("add_secret", f"Add {name}", {"name": name}),
                        )
                    )
        return issues

    @app.post("/api/check")
    def check(spec: dict[str, Any] = Body(...), flow_id: str | None = None) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        an = FlowAnalysis(parsed, resolve=resolve_flow, flow_id=flow_id)
        issues = validate(parsed, analysis=an) + runtime_issues(parsed)
        return {"issues": [i.to_dict() for i in issues], "analysis": an.summary()}

    @app.post("/api/compile")
    def compile_endpoint(
        spec: dict[str, Any] = Body(...), flow_id: str | None = None
    ) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        try:
            compiled = compile_flow(
                parsed, allow_errors=True, resolve=resolve_flow, flow_id=flow_id
            )
        except Exception as exc:  # code can't be shown until the errors are fixed
            an = FlowAnalysis(parsed, resolve=resolve_flow, flow_id=flow_id)
            issues = [i.to_dict() for i in validate(parsed, analysis=an)]
            return {
                "source": None,
                "snippets": {},
                "requirements": [],
                "issues": issues,
                "error": str(exc),
            }
        return {
            "source": compiled.source,
            "module_name": compiled.module_name,
            "snippets": compiled.snippets,
            "requirements": compiled.requirements,
            "issues": [i.to_dict() for i in compiled.issues],
        }

    def _export(spec: FlowSpec, flow_id: str | None = None) -> Response:
        try:
            data = export_zip(spec, resolve_flow, flow_id)
        except CompileError as exc:
            raise HTTPException(
                422,
                detail={
                    "message": "Fix the flow's errors before exporting.",
                    "issues": [i.to_dict() for i in exc.issues],
                },
            ) from exc
        from ..compiler import module_name

        filename = f"{module_name(spec.name)}.zip"
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/export")
    def export(spec: dict[str, Any] = Body(...), flow_id: str | None = None) -> Response:
        return _export(_spec_or_422(spec), flow_id)

    # ── runs ────────────────────────────────────────────────────────────────

    def hub() -> Hub:
        found = getattr(app.state, "hub", None)
        if found is None:
            raise HTTPException(503, detail={"message": "The server is still starting."})
        return found

    add_integration_routes(app, hub)

    def sse(event: dict[str, Any]) -> str:
        data = json.dumps(event, ensure_ascii=False, default=str)
        return f"id: {event.get('event_id', '')}\nevent: {event.get('type', 'message')}\ndata: {data}\n\n"

    def stream(
        run_id: str, after: int = 0, headers: dict[str, str] | None = None
    ) -> StreamingResponse:
        async def events():
            async for event in hub().tail(run_id, after):
                yield sse(event)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Run-Id": run_id,
                **(headers or {}),
            },
        )

    async def last_event_id(run_id: str) -> int:
        events = await hub().db.events_after(run_id, 0, limit=100000)
        return events[-1]["event_id"] if events else 0

    def hub_errors(exc: Exception) -> HTTPException:
        if isinstance(exc, Busy):
            return HTTPException(409, detail={"message": str(exc), "kind": "busy"})
        if isinstance(exc, NotFound):
            return HTTPException(404, detail={"message": str(exc)})
        return HTTPException(409, detail={"message": str(exc)})

    def run_summary(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": run["id"],
            "flow_id": run["flow_id"],
            "flow": run["flow_name"],
            "version_id": run["version_id"],
            "thread_id": run["thread_id"],
            "status": run["status"],
            "trigger": run["trigger"],
            "parent_run_id": run["parent_run_id"],
            "inputs": run["inputs"],
            "options": run["options"],
            "output": run["output"],
            "error": run["error"],
            "usage": run["usage"],
            "cost": run["cost"],
            "pending": run["pending"],
            "checkpoint_id": run["checkpoint_id"],
            "started": run["started_at"] or run["created_at"],
            "created": run["created_at"],
            "finished": run["finished_at"],
            "duration_ms": run["duration_ms"],
        }

    @app.post("/api/runs")
    async def start_run(req: RunRequest) -> Response:
        spec = _spec_or_422(req.spec) if req.spec is not None else _get(req.flow_id or "")
        try:
            run_id = await hub().start_run(
                spec,
                flow_id=req.flow_id,
                inputs=req.inputs,
                thread_id=req.thread_id,
                stand_in=req.stand_in,
                pause_before=req.pause_before,
                pause_after=req.pause_after,
                trigger=req.trigger or "manual",
            )
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        if req.background:
            run = await hub().db.get_run(run_id)
            return JSONResponse({"run_id": run_id, "thread_id": run["thread_id"]}, status_code=202)
        return stream(run_id)

    @app.get("/api/runs")
    async def list_runs(
        flow_id: str | None = None,
        thread_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = await hub().db.list_runs(
            flow_id=flow_id, thread_id=thread_id, status=status, limit=limit
        )
        return [run_summary(r) for r in rows]

    async def _run_or_404(run_id: str) -> dict[str, Any]:
        run = await hub().db.get_run(run_id)
        if run is None:
            raise HTTPException(404, detail={"message": "That run doesn't exist."})
        return run

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = await _run_or_404(run_id)
        events = await hub().db.events_after(run_id, 0, limit=100000, include_tokens=False)
        return {**run_summary(run), "events": events}

    @app.get("/api/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request, after: int = 0) -> StreamingResponse:
        await _run_or_404(run_id)
        last = request.headers.get("last-event-id")
        if last and last.isdigit():
            after = max(after, int(last))
        return stream(run_id, after)

    @app.websocket("/api/runs/{run_id}/ws")
    async def run_socket(socket: WebSocket, run_id: str, after: int = 0) -> None:
        await socket.accept()
        if await hub().db.get_run(run_id) is None:
            await socket.send_json({"type": "error", "message": "That run doesn't exist."})
            await socket.close(code=4404)
            return

        gone = asyncio.Event()

        async def listen() -> None:
            try:
                while True:
                    message = await socket.receive_json()
                    if message.get("type") == "cancel":
                        await hub().cancel(run_id)
            except (WebSocketDisconnect, RuntimeError, ValueError):
                gone.set()

        listener = asyncio.create_task(listen())
        try:
            async for event in hub().tail(run_id, after, until="end", stop=gone):
                await socket.send_text(json.dumps(event, ensure_ascii=False, default=str))
        except WebSocketDisconnect:
            pass
        finally:
            listener.cancel()
            with contextlib.suppress(Exception):
                await socket.close()

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str) -> dict[str, Any]:
        await _run_or_404(run_id)
        return {"run_id": run_id, "status": await hub().cancel(run_id)}

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str, req: ResumeRequest) -> Response:
        run = await _run_or_404(run_id)
        answers = dict(req.answers or {})
        if req.answer is not None or not answers:
            waiting = (run.get("pending") or {}).get("interrupts") or []
            if len(waiting) != 1:
                raise HTTPException(
                    409, detail={"message": "Say which waiting step each answer is for (answers)."}
                )
            answers = {waiting[0]["id"]: req.answer}
        after = await last_event_id(run_id)
        try:
            for interrupt_id in answers:
                for item in await hub().db.list_inbox(status="open"):
                    if item["run_id"] == run_id and item["interrupt_id"] == interrupt_id:
                        await hub().db.answer_inbox(item["id"], answers[interrupt_id], req.by)
            await hub().resume(run_id, answers)
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        if req.background:
            return JSONResponse({"run_id": run_id, "status": "queued"}, status_code=202)
        return stream(run_id, after)

    @app.post("/api/runs/{run_id}/continue")
    async def continue_run(run_id: str, background: bool = False, step: bool = False) -> Response:
        """Carry on; ``step=true`` runs only the next step, then pauses again (step over)."""
        await _run_or_404(run_id)
        after = await last_event_id(run_id)
        try:
            await hub().continue_run(run_id, step=step)
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        if background:
            return JSONResponse({"run_id": run_id, "status": "queued"}, status_code=202)
        return stream(run_id, after)

    @app.post("/api/runs/{run_id}/fork")
    async def fork_run(run_id: str, req: ForkRequest) -> Response:
        await _run_or_404(run_id)
        try:
            new_id = await hub().fork(
                run_id, req.checkpoint_id, req.update, req.pause_before, req.pause_after
            )
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        if req.background:
            return JSONResponse({"run_id": new_id}, status_code=202)
        return stream(new_id)

    @app.get("/api/runs/{run_id}/savepoints")
    async def save_points(run_id: str) -> list[dict[str, Any]]:
        await _run_or_404(run_id)
        try:
            return await hub().save_points(run_id)
        except NotFound as exc:
            raise hub_errors(exc) from exc

    @app.get("/api/threads")
    async def list_threads(flow_id: str | None = None) -> list[dict[str, Any]]:
        return await hub().db.list_threads(flow_id)

    # ── the Inbox ───────────────────────────────────────────────────────────

    @app.get("/api/inbox")
    async def list_inbox(status: str | None = "open") -> list[dict[str, Any]]:
        return await hub().db.list_inbox(status or None)

    @app.get("/api/inbox/{item_id}")
    async def get_inbox(item_id: str) -> dict[str, Any]:
        item = await hub().db.get_inbox(item_id)
        if item is None:
            raise HTTPException(404, detail={"message": "That Inbox item doesn't exist."})
        return item

    @app.post("/api/inbox/{item_id}/answer")
    async def answer_inbox(item_id: str, req: AnswerRequest) -> dict[str, Any]:
        answer: dict[str, Any] = {"action": req.action}
        if req.value is not None:
            answer["value"] = req.value
        if req.comment:
            answer["comment"] = req.comment
        try:
            item = await hub().answer(item_id, answer, req.by)
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        return {"answered": True, "run_id": item["run_id"]}

    # ── triggers ────────────────────────────────────────────────────────────

    def trigger_view(trig: dict[str, Any]) -> dict[str, Any]:
        out = dict(trig)
        cfg = out.get("config") or {}
        if trig["kind"] in ("webhook", "upload"):
            suffix = "/upload" if trig["kind"] == "upload" else ""
            out["url"] = f"/api/hooks/{trig['id']}{suffix}"
        if trig["kind"] == "schedule":
            out["describe"] = describe_cron(cfg.get("cron", ""))
        if trig["kind"] == "email":
            out["config"] = {**cfg, "password": "••••••" if cfg.get("password") else ""}
            out["describe"] = f"New mail for {cfg.get('username')} on {cfg.get('host')}"
        return out

    async def _validated_trigger(
        req: TriggerRequest, flow_id: str
    ) -> tuple[dict[str, Any], float | None]:
        _get(flow_id)
        cfg = dict(req.config or {})
        next_at = None
        if req.kind == "schedule":
            try:
                next_at = next_fire(cfg.get("cron", ""), cfg.get("timezone"), time.time())
            except CronError as exc:
                raise HTTPException(422, detail={"message": str(exc)}) from exc
        elif req.kind == "after_flow":
            source = cfg.get("source_flow_id")
            if not source:
                raise HTTPException(422, detail={"message": "Pick the flow that comes first."})
            _get(source)
            if source == flow_id:
                raise HTTPException(
                    422, detail={"message": "A flow can't start itself when it finishes."}
                )
        elif req.kind == "upload" and not cfg.get("field"):
            raise HTTPException(422, detail={"message": "Pick the input field that gets the file."})
        elif req.kind == "email":
            if not cfg.get("host") or not cfg.get("username"):
                raise HTTPException(
                    422, detail={"message": "Give the mail server and the user name."}
                )
            next_at = time.time()
        return cfg, next_at

    @app.get("/api/triggers")
    async def list_triggers(flow_id: str | None = None) -> list[dict[str, Any]]:
        return [trigger_view(t) for t in await hub().db.list_triggers(flow_id)]

    @app.post("/api/triggers", status_code=201)
    async def create_trigger(req: TriggerRequest) -> dict[str, Any]:
        cfg, next_at = await _validated_trigger(req, req.flow_id)
        trig = await hub().db.create_trigger(req.flow_id, req.kind, cfg, req.name or "", next_at)
        return trigger_view(trig)

    @app.patch("/api/triggers/{trigger_id}")
    async def update_trigger(trigger_id: str, req: TriggerUpdate) -> dict[str, Any]:
        trig = await hub().db.get_trigger(trigger_id)
        if trig is None:
            raise HTTPException(404, detail={"message": "That trigger doesn't exist."})
        values: dict[str, Any] = {}
        if req.enabled is not None:
            values["enabled"] = req.enabled
        if req.name is not None:
            values["name"] = req.name
        if req.config is not None:
            cfg, next_at = await _validated_trigger(
                TriggerRequest(flow_id=trig["flow_id"], kind=trig["kind"], config=req.config),
                trig["flow_id"],
            )
            values.update(config=cfg, next_fire_at=next_at)
        if values:
            await hub().db.update_trigger(trigger_id, **values)
        return trigger_view(await hub().db.get_trigger(trigger_id))  # type: ignore[arg-type]

    @app.delete("/api/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str) -> dict[str, Any]:
        if not await hub().db.delete_trigger(trigger_id):
            raise HTTPException(404, detail={"message": "That trigger doesn't exist."})
        return {"deleted": True}

    async def _hook(trigger_id: str, kind: str, request: Request) -> dict[str, Any]:
        trig = await hub().db.get_trigger(trigger_id)
        token = request.headers.get("x-easychain-token") or request.query_params.get("token")
        if (
            trig is None
            or trig["kind"] != kind
            or not secrets_module.compare_digest(str(token or ""), trig["token"])
        ):
            raise HTTPException(404, detail={"message": "No trigger here, or the token is wrong."})
        if not trig["enabled"]:
            raise HTTPException(409, detail={"message": "This trigger is switched off."})
        return trig

    @app.post("/api/hooks/{trigger_id}", status_code=202)
    async def webhook(trigger_id: str, request: Request) -> dict[str, Any]:
        trig = await _hook(trigger_id, "webhook", request)
        raw = await request.body()
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            data = {"body": raw.decode("utf-8", "replace")}
        if not isinstance(data, dict):
            data = {"body": data}
        try:
            run_id = await fire_trigger(hub(), trig, data)
        except (Busy, NotFound, Invalid) as exc:
            raise hub_errors(exc) from exc
        return {"run_id": run_id}

    @app.post("/api/hooks/{trigger_id}/upload", status_code=202)
    async def upload(
        trigger_id: str, request: Request, filename: str = "upload.bin"
    ) -> dict[str, Any]:
        trig = await _hook(trigger_id, "upload", request)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:120] or "upload.bin"
        folder = home_path / "uploads" / uuid.uuid4().hex
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / safe
        target.write_bytes(await request.body())
        cfg = trig.get("config") or {}
        data = {cfg["field"]: str(target), "filename": safe}
        run_id = await fire_trigger(
            hub(), {**trig, "config": {**cfg, "inputs": cfg.get("inputs")}}, data
        )
        return {"run_id": run_id, "file": str(target)}

    # ── settings: notifications ─────────────────────────────────────────────

    @app.get("/api/settings/notifications")
    async def get_notifications() -> dict[str, Any]:
        return notify.merged(await hub().db.get_setting("notifications", {}))

    @app.put("/api/settings/notifications")
    async def set_notifications(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
        value = notify.merged(body)
        await hub().db.set_setting("notifications", value)
        return value

    @app.post("/api/settings/notifications/test")
    async def test_notifications() -> dict[str, Any]:
        config = await hub().db.get_setting("notifications", {})
        sample = {
            "id": "test",
            "run_id": "test",
            "flow_id": None,
            "flow_name": "Easy Chain",
            "step": "test",
            "request": {"question": "This is a test notification."},
        }
        return {"results": await notify.send_all(config, sample)}

    # ── flow versions ───────────────────────────────────────────────────────

    @app.get("/api/flows/{flow_id}/versions")
    async def list_versions(flow_id: str) -> list[dict[str, Any]]:
        return await hub().db.list_versions(flow_id)

    @app.get("/api/versions/{version_id}")
    async def get_version(version_id: str) -> dict[str, Any]:
        version = await hub().db.get_version(version_id)
        if version is None:
            raise HTTPException(404, detail={"message": "That version doesn't exist."})
        return version

    # ── secrets ─────────────────────────────────────────────────────────────

    @app.get("/api/secrets")
    def list_secrets() -> list[dict[str, Any]]:
        names = {s["name"]: s for s in vault.names()}
        for p in PROVIDERS.values():
            if p.key_env and p.key_env not in names and os.environ.get(p.key_env):
                names[p.key_env] = {"name": p.key_env, "source": "environment"}
        return sorted(names.values(), key=lambda s: s["name"])

    @app.put("/api/secrets/{name}")
    def set_secret(name: str, body: SecretValue) -> dict[str, Any]:
        try:
            vault.set(name, body.value.strip())
        except ValueError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        return {"name": name, "saved": True}

    @app.delete("/api/secrets/{name}")
    def delete_secret(name: str) -> dict[str, Any]:
        if not vault.delete(name):
            raise HTTPException(404, detail={"message": "No secret with that name is saved here."})
        return {"name": name, "deleted": True}

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        return JSONResponse(detail, status_code=exc.status_code)

    # ── the web app ─────────────────────────────────────────────────────────

    index = web_dir / "index.html"
    if index.exists():
        if (web_dir / "assets").exists():
            app.mount("/assets", StaticFiles(directory=web_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Response:
            if path.startswith("api/"):
                raise HTTPException(404, detail={"message": "Unknown API route."})
            candidate = (web_dir / path).resolve()
            if path and candidate.is_file() and web_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index)
    else:

        @app.get("/", include_in_schema=False)
        def no_web() -> PlainTextResponse:
            return PlainTextResponse(
                "Easy Chain API is running. The web app isn't built yet: run `make web` "
                "(or `pnpm --dir apps/web build`), or use `make dev` for the dev server.\n"
            )

    return app
