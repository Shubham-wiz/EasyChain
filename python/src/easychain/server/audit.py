"""The audit log (Phase 5.6c): who changed what, and when (docs/audit.md).

Every route that changes something is in ``AUDITED`` with the action it records; routes that
only read or work something out are in ``NOT_AUDITED`` with the reason. ``tests/test_audit.py``
fails if a route that changes something is in neither, so a new one can't be missed.

``AuditMiddleware`` writes the entry once the response starts with a success status: who (the
principal ``authorize`` found), the action, the target (the route's first path parameter, or what
the route says with ``note``) and the request's address. Request bodies are never recorded, so
secret values and webhook addresses can't end up in the log. Failures aren't changes and aren't
recorded, except signing in, which the sign-in routes record themselves (``record``).

Saving a flow is recorded at most once every ``SAVE_EVERY`` seconds per person and flow
(the editor saves as you type); every saved version is kept in the flow's history.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .auth import DEFAULT_WORKSPACE, Principal

log = logging.getLogger("easychain.audit")

SAVE_EVERY = 300

# (method, route path) → the action recorded.
AUDITED: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/setup"): "auth.setup",
    ("POST", "/api/auth/login"): "auth.sign_in",
    ("POST", "/api/auth/logout"): "auth.sign_out",
    ("POST", "/api/auth/password"): "auth.password",
    ("POST", "/api/hooks/{trigger_id}"): "trigger.fired",
    ("POST", "/api/hooks/{trigger_id}/upload"): "trigger.fired",
    ("POST", "/api/people"): "person.add",
    ("PATCH", "/api/people/{user_id}"): "person.change",
    ("DELETE", "/api/people/{user_id}"): "person.remove",
    ("POST", "/api/keys"): "key.create",
    ("DELETE", "/api/keys/{key_id}"): "key.revoke",
    ("POST", "/api/runs"): "run.start",
    ("POST", "/api/runs/{run_id}/cancel"): "run.stop",
    ("POST", "/api/runs/{run_id}/resume"): "run.answer",
    ("POST", "/api/runs/{run_id}/continue"): "run.continue",
    ("POST", "/api/runs/{run_id}/fork"): "run.fork",
    ("POST", "/api/inbox/{item_id}/answer"): "inbox.answer",
    ("POST", "/api/flows"): "flow.create",
    ("PUT", "/api/flows/{flow_id}"): "flow.save",
    ("DELETE", "/api/flows/{flow_id}"): "flow.delete",
    ("POST", "/api/flows/{flow_id}/tests"): "flow.tests",
    ("POST", "/api/triggers"): "trigger.create",
    ("PATCH", "/api/triggers/{trigger_id}"): "trigger.change",
    ("DELETE", "/api/triggers/{trigger_id}"): "trigger.delete",
    ("POST", "/api/knowledge"): "knowledge.create",
    ("PATCH", "/api/knowledge/{kb_id}"): "knowledge.change",
    ("DELETE", "/api/knowledge/{kb_id}"): "knowledge.delete",
    ("POST", "/api/knowledge/{kb_id}/files"): "knowledge.add",
    ("POST", "/api/knowledge/{kb_id}/urls"): "knowledge.add",
    ("POST", "/api/knowledge/{kb_id}/text"): "knowledge.add",
    ("DELETE", "/api/knowledge/{kb_id}/documents/{doc_id}"): "knowledge.remove",
    ("PUT", "/api/skills/{name}"): "skill.save",
    ("DELETE", "/api/skills/{name}"): "skill.delete",
    ("PUT", "/api/settings/copilot"): "settings.copilot",
    ("PUT", "/api/secrets/{name}"): "secret.set",
    ("DELETE", "/api/secrets/{name}"): "secret.delete",
    ("PUT", "/api/settings/mcp"): "settings.mcp",
    ("PUT", "/api/settings/sandbox"): "settings.sandbox",
    ("POST", "/api/sandbox/prepare"): "settings.sandbox_prepare",
    ("PUT", "/api/settings/notifications"): "settings.notifications",
    ("PUT", "/api/settings/network"): "settings.network",
    ("POST", "/api/settings/notifications/test"): "settings.notifications_test",
}

# Routes that use POST but change nothing kept by Easy Chain, and why.
NOT_AUDITED: dict[tuple[str, str], str] = {
    ("POST", "/api/check"): "checks a flow",
    ("POST", "/api/compile"): "shows a flow's code",
    ("POST", "/api/export"): "makes a download",
    ("POST", "/api/knowledge/{kb_id}/search"): "searches",
    ("POST", "/api/knowledge-preview"): "previews how a document would be split",
    ("POST", "/api/copilot"): "proposes changes; applying them is a flow save",
    ("POST", "/api/mcp/tools"): "lists an MCP server's tools",
    ("POST", "/api/openapi/inspect"): "reads an API description",
    ("POST", "/api/openapi/steps"): "makes steps to add; adding them is a flow save",
}

# How each action reads in the log ("Ann saved flow support-bot").
LABELS: dict[str, str] = {
    "auth.setup": "made the first account",
    "auth.sign_in": "signed in",
    "auth.sign_in_failed": "failed to sign in as",
    "auth.setup_failed": "gave a wrong first-account code",
    "auth.setup_code": "made a new code for",
    "auth.sign_out": "signed out",
    "auth.password": "changed their password",
    "trigger.fired": "started a run with webhook",
    "person.add": "added",
    "person.change": "changed",
    "person.remove": "removed",
    "key.create": "made a key for scripts",
    "key.revoke": "revoked key",
    "run.start": "started a run of",
    "run.stop": "stopped run",
    "run.answer": "answered run",
    "run.continue": "carried on run",
    "run.fork": "re-ran from a Save Point of run",
    "inbox.answer": "answered Inbox item",
    "flow.create": "made flow",
    "flow.save": "saved flow",
    "flow.delete": "deleted flow",
    "flow.tests": "added test cases to flow",
    "trigger.create": "added a trigger to",
    "trigger.change": "changed trigger",
    "trigger.delete": "removed trigger",
    "knowledge.create": "made Knowledge Base",
    "knowledge.change": "changed Knowledge Base",
    "knowledge.delete": "deleted Knowledge Base",
    "knowledge.add": "added documents to",
    "knowledge.remove": "removed a document from",
    "skill.save": "saved skill",
    "skill.delete": "deleted skill",
    "settings.copilot": "changed the copilot's settings",
    "secret.set": "set secret",
    "secret.delete": "deleted secret",
    "settings.mcp": "changed the MCP servers",
    "settings.sandbox": "changed the sandbox settings",
    "settings.sandbox_prepare": "prepared the sandbox",
    "settings.notifications": "changed the notifications",
    "settings.network": "changed which addresses Easy Chain may reach",
    "settings.notifications_test": "sent test notifications",
}

KINDS = {
    "flow.": "Flows",
    "run.": "Runs",
    "inbox.": "Inbox",
    "trigger.": "Triggers",
    "knowledge.": "Knowledge Bases",
    "skill.": "Skills",
    "secret.": "Secrets",
    "settings.": "Settings",
    "person.": "People",
    "key.": "Keys for scripts",
    "auth.": "Signing in",
}


def note(
    connection: HTTPConnection,
    target: str | None = None,
    *,
    actor: Principal | None = None,
    skip: bool = False,
    **detail: Any,
) -> None:
    """Inside a route: what its entry should name (a new flow's id, a person's email), who did
    it when ``authorize`` couldn't know (signing in), or ``skip`` to leave it out."""
    audit = connection.scope.setdefault("state", {}).setdefault("audit", {})
    if target is not None:
        audit["target"] = str(target)
    if actor is not None:
        audit["actor"] = actor
    if skip:
        audit["skip"] = True
    audit.setdefault("detail", {}).update(detail)


def _who(principal: Principal | None) -> dict[str, Any]:
    if principal is None:
        return {"actor_id": None, "actor": "someone not signed in", "via": "none"}
    # A person's name or email, or a trigger's name.
    actor = "the local user (sign-in off)" if principal.via == "local" else principal.label
    return {"actor_id": principal.user_id, "actor": actor, "via": principal.via}


async def record(
    connection: HTTPConnection, action: str, target: str | None = None, **detail: Any
) -> None:
    """Write one entry now, for what the middleware doesn't see (a failed sign-in)."""
    principal: Principal | None = getattr(connection.state, "principal", None)
    await connection.app.state.hub.db.add_audit(
        workspace_id=principal.workspace if principal else DEFAULT_WORKSPACE,
        action=action,
        target=target,
        detail=detail,
        address=connection.client.host if connection.client else None,
        **_who(principal),
    )


WEBHOOK = Principal(role="runner", name="a webhook", via="trigger")


class AuditMiddleware:
    """Records each successful request to an audited route, as its response starts."""

    def __init__(self, app: ASGIApp):
        self.app = app
        self._saved: dict[tuple[str, str], float] = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        done = False

        async def sending(message: Message) -> None:
            nonlocal done
            if message["type"] == "http.response.start" and not done:
                done = True
                if message["status"] < 400:
                    try:
                        await self._write(scope)
                    except Exception:  # the change happened; a log problem mustn't hide it
                        log.exception("Couldn't write the audit log entry")
            await send(message)

        await self.app(scope, receive, sending)

    async def _write(self, scope: Scope) -> None:
        route = scope.get("route")
        action = AUDITED.get((scope["method"], getattr(route, "path", "")))
        state = scope.get("state") or {}
        noted = state.get("audit") or {}
        if action is None or noted.get("skip"):
            return
        params = {k: str(v) for k, v in (scope.get("path_params") or {}).items()}
        target = noted.get("target") or next(iter(params.values()), None)
        principal: Principal | None = noted.get("actor") or state.get("principal")
        if action == "flow.save":
            key = (principal.user_id if principal and principal.user_id else "", target or "")
            now = time.time()
            if now - self._saved.get(key, 0) < SAVE_EVERY:
                return
            self._saved[key] = now
        app = scope["app"]
        client = scope.get("client")
        await app.state.hub.db.add_audit(
            workspace_id=principal.workspace if principal else DEFAULT_WORKSPACE,
            action=action,
            target=target,
            detail={**params, **(noted.get("detail") or {})},
            address=client[0] if client else None,
            **_who(principal),
        )


def describe(entry: dict[str, Any]) -> str:
    """One line: "Ann saved flow support-bot"."""
    label = LABELS.get(entry["action"], entry["action"])
    return " ".join(part for part in (entry["actor"], label, entry.get("target") or "") if part)


def add_audit_routes(app: Any) -> None:
    @app.get("/api/audit")
    async def audit_log(
        person: str | None = None,
        kind: str | None = None,
        target: str | None = None,
        since: float | None = None,
        before: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Owners: what everyone changed, newest first. ``kind`` is an action or its start
        ("flow."); ``person`` an account id; ``before`` the ``next`` of the last page."""
        entries = await app.state.hub.db.list_audit(
            actor_id=person, action=kind, target=target, since=since, before=before, limit=limit
        )
        return {
            "entries": [{**e, "text": describe(e)} for e in entries],
            "next": entries[-1]["id"] if len(entries) == max(1, min(limit, 1000)) else None,
            "kinds": [{"id": k, "label": v} for k, v in KINDS.items()],
        }
