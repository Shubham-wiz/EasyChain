"""The Easy Chain API server (FastAPI).

REST for flows, the step catalog, checks, code and exports; Server-Sent Events
for runs. It also serves the built web app, so one process is the whole app.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Request
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
from ..providers import PROVIDERS, split_model
from ..runtime import RunOptions, key_status, stream_run
from ..spec import FlowSpec, SpecError, loads_spec, parse_spec, spec_json
from ..spec.models import SPEC_VERSION
from ..spec.schema import flow_json_schema
from ..steps import catalog as step_catalog
from ..templates import list_templates, load_template
from .secrets import SecretStore
from .store import FlowNotFound, FlowStore, RunStore

STATIC_DIR = Path(__file__).parent / "static"


class RunRequest(BaseModel):
    flow_id: str | None = None
    spec: dict[str, Any] | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = None
    stand_in: bool = False


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


def create_app(
    workspace: str | Path | None = None,
    home: str | Path | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    home_path = Path(home or os.environ.get("EASYCHAIN_HOME") or Path.home() / ".easychain")
    workspace_path = Path(workspace or os.environ.get("EASYCHAIN_WORKSPACE") or home_path / "flows")
    flows = FlowStore(workspace_path)
    runs = RunStore()
    vault = SecretStore(home_path)
    web_dir = Path(static_dir or os.environ.get("EASYCHAIN_WEB_DIST") or STATIC_DIR)

    app = FastAPI(
        title="Easy Chain",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.flows, app.state.runs, app.state.vault = flows, runs, vault
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
        return {
            "ok": True,
            "version": __version__,
            "spec_version": SPEC_VERSION,
            "workspace": str(workspace_path),
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
            "update_rules": ["replace", "append", "merge", "add"],
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
    def save_flow(flow_id: str, spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        try:
            flows.save(flow_id, parsed)
        except FlowNotFound:
            raise HTTPException(404, detail={"message": "Bad flow id."}) from None
        return {"id": flow_id, "saved": True}

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
        return _export(_get(flow_id))

    # ── checks, code, export ───────────────────────────────────────────────

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
        for step in spec.steps:
            if step.type != "http_request":
                continue
            texts = [step.settings.url, step.settings.body, *step.settings.headers.values()]
            for name in sorted({n for t in texts for n in template_secrets(t)}):
                if not vault.has(name):
                    issues.append(
                        warning(
                            "missing_secret",
                            f"The secret {name} isn't set yet.",
                            step=step.id,
                            setting="headers",
                            fix=Fix("add_secret", f"Add {name}", {"name": name}),
                        )
                    )
        return issues

    @app.post("/api/check")
    def check(spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        an = FlowAnalysis(parsed)
        issues = validate(parsed, analysis=an) + runtime_issues(parsed)
        return {"issues": [i.to_dict() for i in issues], "analysis": an.summary()}

    @app.post("/api/compile")
    def compile_endpoint(spec: dict[str, Any] = Body(...)) -> dict[str, Any]:
        parsed = _spec_or_422(spec)
        try:
            compiled = compile_flow(parsed, allow_errors=True)
        except Exception as exc:  # code can't be shown until the errors are fixed
            issues = [i.to_dict() for i in validate(parsed)]
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

    def _export(spec: FlowSpec) -> Response:
        try:
            data = export_zip(spec)
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
    def export(spec: dict[str, Any] = Body(...)) -> Response:
        return _export(_spec_or_422(spec))

    # ── runs ────────────────────────────────────────────────────────────────

    @app.post("/api/runs")
    async def start_run(req: RunRequest, request: Request) -> StreamingResponse:
        spec = _spec_or_422(req.spec) if req.spec is not None else _get(req.flow_id or "")
        run_id = uuid.uuid4().hex
        runs.start(run_id, req.flow_id, spec.name, req.inputs)
        options = RunOptions(
            thread_id=req.thread_id, run_id=run_id, stand_in=req.stand_in, redact=vault.values()
        )

        async def events():
            async for event in stream_run(spec, req.inputs, options):
                runs.add(run_id, event)
                yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Run-Id": run_id},
        )

    @app.get("/api/runs")
    def list_runs(flow_id: str | None = None) -> list[dict[str, Any]]:
        return runs.list(flow_id)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, detail={"message": "That run isn't kept any more."})
        return run

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
