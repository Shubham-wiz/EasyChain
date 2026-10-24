"""Publish (Phase 5.4, docs/publish.md): pin a flow version to an environment, and let other
apps (and people, through a chat page) run it.

**Promotion.** A version goes to:

- **dev** as it is;
- **staging** only with a kept Test Run of that version, run in staging, where every case
  passed;
- **production** only when it is the version in staging, and only by an Owner.

Every publish is in the audit log.

**Access keys.** A published flow is run by other apps with a key made for it and one
environment (``Authorization: Bearer epk_<id>_<secret>``), or by people through a **chat link**
(the hosted chat page and the widget; no secret, so it can sit in a web page). Each has a rate
limit: requests per minute, counted in the database so every API server shares it (a chat
link counts per visitor's address too).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import secrets
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..environments import ENVIRONMENTS, LABELS
from ..spec import FlowSpec, SpecError
from .audit import note
from .auth import principal_of, require, token_hash
from .environments_api import known
from .spaces import space_of, spaces_of
from .store import FlowNotFound

# The longest a published run's caller waits for its answer before getting its id to ask
# again.
MAX_WAIT = 120


class PublishRequest(BaseModel):
    environment: str
    # A saved version (the flow as it is saved now, when left out).
    version_id: str | None = Field(default=None, max_length=40)
    # The Test Run that lets it into staging (the newest passing one, when left out).
    test_run_id: str | None = Field(default=None, max_length=40)


class KeyRequest(BaseModel):
    environment: str
    name: str = Field(default="", max_length=200)
    # secret: for other apps (shown once); chat: a link to the hosted chat page and widget
    kind: str = Field(default="secret", pattern="^(secret|chat)$")
    per_minute: int = Field(default=60, ge=1, le=10_000)
    # Sites that may embed the chat page (chat links), like https://shop.example.com
    origins: list[str] = Field(default_factory=list)


class PublishedRun(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = Field(default=None, max_length=100)
    wait: float = Field(default=60, ge=0, le=MAX_WAIT)


class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = Field(default=None, max_length=100)


def new_access_key() -> tuple[str, str, str]:
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(30)
    return key_id, secret, f"epk_{key_id}_{secret}"


def _split(key: str) -> tuple[str, str] | None:
    if not key.startswith("epk_"):
        return None
    parts = key[4:].split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and all(parts) else None


def _key_view(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: row[k] for k in ("id", "flow_id", "environment", "name", "kind", "per_minute")}
    out.update(
        origins=row.get("origins") or [],
        created_by=row.get("created_by"),
        created_at=row.get("created_at"),
        last_used_at=row.get("last_used_at"),
    )
    if row["kind"] == "chat":
        out["chat_path"] = f"/chat/{row['id']}"
    return out


def add_publish_routes(app: FastAPI) -> None:
    def hub() -> Any:
        return app.state.hub

    def _flow(request: Request, flow_id: str) -> FlowSpec:
        try:
            return space_of(request).flows.get(flow_id)
        except FlowNotFound:
            raise HTTPException(
                404, detail={"message": "That flow doesn't exist (any more)."}
            ) from None
        except SpecError as exc:
            raise HTTPException(
                422,
                detail={"message": "The saved flow file has problems.", "problems": exc.problems},
            ) from exc

    def _by(request: Request) -> str | None:
        principal = principal_of(request)
        return principal.label if principal is not None and principal.user_id else None

    async def _passing_test_run(
        flow_id: str, version_id: str, environment: str, chosen: str | None
    ) -> dict[str, Any]:
        """The Test Run that lets a version into ``environment``, or a 409 saying what's needed."""
        need = (
            f"Publishing to {LABELS[environment]} needs a kept Test Run of this version, run in "
            f"{LABELS[environment]}, where every case passed. Start one in the Tests tab, under "
            f"Kept Test Runs, with {LABELS[environment]} chosen."
        )
        runs = await hub().db.list_test_runs(flow_id, limit=100)
        if chosen is not None:
            runs = [r for r in runs if r["id"] == chosen]
        for run in runs:
            s = run.get("summary") or {}
            if (
                run["status"] == "done"
                and run["version_id"] == version_id
                and run.get("environment") == environment
                and s.get("total", 0) > 0
                and s.get("failed", 1) == 0
            ):
                return run
        raise HTTPException(409, detail={"message": need, "action": "test_run"})

    @app.get("/api/flows/{flow_id}/publish")
    async def publish_state(flow_id: str, request: Request) -> dict[str, Any]:
        """What is published where, the history, and the flow's access keys."""
        _flow(request, flow_id)
        current = {env: await hub().db.publication(flow_id, env) for env in ENVIRONMENTS}
        return {
            "environments": [
                {"id": env, "label": LABELS[env], "published": current[env]} for env in ENVIRONMENTS
            ],
            "history": await hub().db.publications_of(flow_id),
            "keys": [_key_view(k) for k in await hub().db.access_keys_of(flow_id)],
        }

    @app.post("/api/flows/{flow_id}/publish")
    async def publish(flow_id: str, body: PublishRequest, request: Request) -> dict[str, Any]:
        """Pin a version to an environment, if it may go there (see the module's docstring)."""
        environment = known(body.environment)
        if environment == "production":
            require(request, "owner")
        spec = _flow(request, flow_id)
        version_id = body.version_id or await hub().db.save_version(flow_id, hub().bundle(spec))
        version = await hub().db.get_version(version_id)
        if (
            version is None
            or version.get("flow_id") != flow_id
            or (version.get("workspace_id") or "default") != space_of(request).id
        ):
            raise HTTPException(404, detail={"message": "That version of the flow doesn't exist."})
        test_run_id = None
        if environment == "staging":
            test_run_id = (
                await _passing_test_run(flow_id, version_id, "staging", body.test_run_id)
            )["id"]
        elif environment == "production":
            staging = await hub().db.publication(flow_id, "staging")
            if staging is None or staging["version_id"] != version_id:
                raise HTTPException(
                    409,
                    detail={
                        "message": "Production only takes the version that is in Staging. "
                        "Publish this version to Staging first.",
                        "action": "staging",
                    },
                )
            test_run_id = staging.get("test_run_id")
        made = await hub().db.publish(
            flow_id, environment, version_id, test_run_id=test_run_id, published_by=_by(request)
        )
        note(request, flow_id, environment=environment, version=version_id)
        return made

    @app.delete("/api/flows/{flow_id}/publish/{environment}")
    async def unpublish(flow_id: str, environment: str, request: Request) -> dict[str, Any]:
        """Stop serving the flow from an environment (its keys stop working there)."""
        known(environment)
        if environment == "production":
            require(request, "owner")
        if not await hub().db.unpublish(flow_id, environment):
            raise HTTPException(
                404, detail={"message": f"Nothing is published to {LABELS[environment]}."}
            )
        note(request, flow_id, environment=environment)
        return {"unpublished": environment}

    @app.post("/api/flows/{flow_id}/keys", status_code=201)
    async def create_key(flow_id: str, body: KeyRequest, request: Request) -> dict[str, Any]:
        """A key for other apps (shown once), or a chat link, for one environment."""
        environment = known(body.environment)
        if environment == "production":
            require(request, "owner")
        _flow(request, flow_id)
        key_id, secret, key = new_access_key()
        origins = sorted({o.strip().rstrip("/") for o in body.origins if o.strip()})
        bad = [o for o in origins if not o.startswith(("https://", "http://"))]
        if bad:
            raise HTTPException(
                422,
                detail={
                    "message": f"A site is its address, like https://shop.example.com: {bad[0]}"
                },
            )
        await hub().db.create_access_key(
            id=key_id,
            flow_id=flow_id,
            environment=environment,
            name=body.name.strip() or ("Chat link" if body.kind == "chat" else "Access key"),
            kind=body.kind,
            secret_hash=token_hash(secret) if body.kind == "secret" else None,
            per_minute=body.per_minute,
            origins=origins,
            created_by=_by(request),
        )
        row = await hub().db.access_key(key_id)
        note(request, flow_id, key=key_id, environment=environment, kind=body.kind)
        return {**_key_view(row or {}), **({"key": key} if body.kind == "secret" else {})}

    @app.delete("/api/flows/{flow_id}/keys/{key_id}")
    async def revoke_key(flow_id: str, key_id: str, request: Request) -> dict[str, Any]:
        row = await hub().db.access_key(key_id)
        if row is None or row["flow_id"] != flow_id or row["revoked_at"] is not None:
            raise HTTPException(404, detail={"message": "There's no such key for this flow."})
        if row["environment"] == "production":
            require(request, "owner")
        await hub().db.revoke_access_key(key_id)
        note(request, flow_id, key=key_id)
        return {"revoked": key_id}

    # ── running a published flow ────────────────────────────────────────────

    async def _limit(name: str, per_minute: int) -> None:
        if await hub().db.count_use(name) > per_minute:
            wait = 60 - int(time.time()) % 60
            raise HTTPException(
                429,
                detail={
                    "message": f"Too many requests: this key allows {per_minute} a minute. Try "
                    f"again in {wait} seconds.",
                    "retry_after": wait,
                },
                headers={"Retry-After": str(wait)},
            )

    async def _start(
        row: dict[str, Any], request: Request, inputs: dict[str, Any], thread_id: str | None
    ) -> tuple[str, str]:
        """Start a run of what is published, as the key's workspace and environment."""
        spaces_of(request).enter_now(row["workspace_id"])
        pub = await hub().db.publication(row["flow_id"], row["environment"])
        if pub is None:
            raise HTTPException(
                404,
                detail={
                    "message": f"Nothing is published to {LABELS[row['environment']]} for this "
                    "flow yet."
                },
            )
        spec, _ = await hub().flow_from_version(pub["version_id"])
        # The run says which key started it (its trigger), like a webhook's.
        run_id = await hub().start_run(
            spec,
            flow_id=row["flow_id"],
            inputs=inputs,
            thread_id=thread_id,
            trigger="chat" if row["kind"] == "chat" else "api",
            trigger_id=row["id"],
            version_id=pub["version_id"],
            environment=row["environment"],
        )
        await hub().db.touch_access_key(row["id"])
        run = await hub().db.get_run(run_id)
        return run_id, run["thread_id"]

    async def _answer(run_id: str, wait: float) -> dict[str, Any]:
        async def finished() -> None:
            async for _event in hub().tail(run_id, 0):
                pass

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(finished(), wait)
        run = await hub().db.get_run(run_id) or {}
        out = {"run_id": run_id, "thread_id": run.get("thread_id"), "status": run.get("status")}
        if run.get("status") == "ok":
            out["output"] = run.get("output")
        elif run.get("status") == "error":
            out["error"] = (run.get("error") or {}).get("message") or "The run failed."
        elif run.get("status") == "paused":
            out["waiting"] = "The flow is waiting for someone in the Inbox."
        return out

    async def _secret_key(request: Request, flow_id: str, environment: str) -> dict[str, Any]:
        header = request.headers.get("authorization", "")
        parts = _split(header[7:].strip()) if header.lower().startswith("bearer ") else None
        row = await hub().db.access_key(parts[0]) if parts else None
        if (
            row is None
            or row["kind"] != "secret"
            or row["revoked_at"] is not None
            or not hmac.compare_digest(row["secret_hash"] or "", token_hash(parts[1]))
            or row["flow_id"] != flow_id
            or row["environment"] != environment
        ):
            raise HTTPException(
                401,
                detail={
                    "message": "Send this flow's access key for that environment as "
                    "Authorization: Bearer epk_…. An Editor makes one under Publish.",
                    "action": "sign_in",
                },
            )
        return row

    @app.post("/api/published/{flow_id}/{environment}")
    async def run_published(
        flow_id: str, environment: str, body: PublishedRun, request: Request
    ) -> dict[str, Any]:
        """Run what is published, with an access key; wait (up to ``wait`` seconds) for the
        answer. A run still going comes back with its id, to ask again."""
        row = await _secret_key(request, flow_id, environment)
        await _limit(f"key:{row['id']}", row["per_minute"])
        run_id, _ = await _start(row, request, body.inputs, body.thread_id)
        return await _answer(run_id, body.wait)

    @app.get("/api/published/{flow_id}/{environment}/runs/{run_id}")
    async def published_run(
        flow_id: str, environment: str, run_id: str, request: Request
    ) -> dict[str, Any]:
        """A published run started with this key's flow and environment."""
        row = await _secret_key(request, flow_id, environment)
        run = await hub().db.get_run(run_id)
        if (
            run is None
            or run["flow_id"] != flow_id
            or run.get("environment") != environment
            or run.get("workspace_id") != row["workspace_id"]
        ):
            raise HTTPException(404, detail={"message": "That run doesn't exist."})
        return await _answer(run_id, 0)

    # ── the chat page and widget's API ──────────────────────────────────────

    async def _chat_key(key_id: str) -> dict[str, Any]:
        row = await hub().db.access_key(key_id)
        if row is None or row["kind"] != "chat" or row["revoked_at"] is not None:
            raise HTTPException(404, detail={"message": "This chat link doesn't work any more."})
        return row

    @app.get("/api/chat/{key_id}")
    async def chat_info(key_id: str, request: Request) -> dict[str, Any]:
        """What the chat page shows: the flow's name and whether it is published."""
        row = await _chat_key(key_id)
        spaces_of(request).enter_now(row["workspace_id"])
        pub = await hub().db.publication(row["flow_id"], row["environment"])
        name = row["flow_id"]
        if pub is not None:
            spec, _ = await hub().flow_from_version(pub["version_id"])
            name = spec.name
        return {"name": name, "available": pub is not None, "environment": row["environment"]}

    @app.post("/api/chat/{key_id}")
    async def chat(key_id: str, body: ChatMessage, request: Request) -> dict[str, Any]:
        """One message from the chat page: the flow's answer (the conversation goes on with
        ``thread_id``)."""
        row = await _chat_key(key_id)
        address = request.client.host if request.client else "?"
        await _limit(f"chat:{row['id']}", row["per_minute"])
        await _limit(f"chat:{row['id']}:{address}", max(1, min(row["per_minute"], 20)))
        inputs = {"messages": [{"role": "user", "content": body.message}]}
        run_id, thread_id = await _start(row, request, inputs, body.thread_id)
        found = await _answer(run_id, 60)
        reply = None
        output = found.get("output") or {}
        messages = output.get("messages") if isinstance(output, dict) else None
        if isinstance(messages, list) and messages and isinstance(messages[-1], dict):
            reply = messages[-1].get("content")
        elif isinstance(output, dict) and output:
            reply = next((v for v in output.values() if isinstance(v, str)), None)
        return {
            "thread_id": thread_id,
            "status": found["status"],
            "reply": reply,
            "error": found.get("error"),
            "waiting": found.get("waiting"),
        }

    @app.get("/chat/{key_id}", include_in_schema=False)
    async def chat_page(key_id: str) -> Response:
        """The hosted chat page (the widget shows it in a frame)."""
        from .chat_page import page

        row = await _chat_key(key_id)
        origins = " ".join(row.get("origins") or [])
        return Response(
            page(key_id),
            media_type="text/html",
            headers={
                # Only the sites listed for this link may show it in a frame.
                "Content-Security-Policy": f"frame-ancestors 'self' {origins}".strip(),
                "Cache-Control": "no-store",
            },
        )

    @app.get("/widget.js", include_in_schema=False)
    async def widget() -> Response:
        from .chat_page import WIDGET

        return Response(
            WIDGET, media_type="text/javascript", headers={"Cache-Control": "max-age=300"}
        )
