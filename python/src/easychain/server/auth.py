"""Who is asking, and what they may do (Phase 5.6).

**Sign-in** is on or off for the whole server (``EASYCHAIN_AUTH=on|off``). Left unset, it is off
when the server only answers this computer (``easychain dev``, which listens on 127.0.0.1, as
before), and on when it answers the network (the Docker image listens on 0.0.0.0). With
sign-in off, everyone who can reach the server is the workspace's Owner, as in Phase 4; turning
it off on a server that answers the network needs ``EASYCHAIN_AUTH=off`` said out loud.

With sign-in on, a request is signed in by a session cookie (the browser) or an API key
(``Authorization: Bearer eck_…``, for scripts and other apps), and each person has a **role**
in the workspace:

=========  ===================================================================================
viewer     sees flows, runs, the Inbox, Knowledge Bases and skills
runner     and starts and stops runs, and answers the Inbox
editor     and changes flows, Knowledge Bases, triggers and skills, and uses what fetches or
           calls models (the copilot, importing an API, previews)
owner      and secrets, server settings, people
=========  ===================================================================================

Every route is in ``PERMISSIONS`` with the least role that may use it; ``authorize`` (run for
every route) checks it, and ``tests/test_auth.py`` fails if a route is missing, so a new
endpoint can't be left open by accident.

**Workspaces** (Phase 5.6d, docs/workspaces.md): a request names its workspace in the
``X-EasyChain-Workspace`` header (or ``?workspace=``, for WebSockets), and the role is the
person's role there. Without one, it is the default workspace, or the first the person is in.
An API key belongs to the workspace it was made in. Owners of the default workspace are the
server's Owners: only they change what every workspace shares (the sandbox, the network,
single sign-on) and make workspaces.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, replace
from typing import Any

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

ROLES = ("viewer", "runner", "editor", "owner")
ROLE_LABELS = {"viewer": "Viewer", "runner": "Runner", "editor": "Editor", "owner": "Owner"}
DEFAULT_WORKSPACE = "default"
WORKSPACE_HEADER = "x-easychain-workspace"
SESSION_COOKIE = "easychain_session"
SESSION_DAYS = 14
MIN_PASSWORD = 10
LOOPBACK = {"127.0.0.1", "localhost", "::1", ""}


def rank(role: str) -> int:
    return ROLES.index(role) if role in ROLES else -1


def auth_mode() -> str:
    """'on' or 'off' (see the module's docstring)."""
    chosen = os.environ.get("EASYCHAIN_AUTH", "").strip().lower()
    if chosen in ("on", "off"):
        return chosen
    host = os.environ.get("EASYCHAIN_HOST", "127.0.0.1").strip().lower()
    return "off" if host in LOOPBACK else "on"


# ── passwords and tokens ─────────────────────────────────────────────────────

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        # Do the same work when there is no hash, so a wrong email takes as long as a wrong
        # password.
        hashlib.scrypt(password.encode(), salt=b"0" * 16, dklen=32, **_SCRYPT)
        return False
    _, n, r, p, salt, digest = stored.split("$")
    got = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p)
    )
    return hmac.compare_digest(got.hex(), digest)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters for the password."
    if len(set(password)) < 4:
        return "That password is too easy to guess; mix in more different characters."
    return None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_api_key() -> tuple[str, str, str]:
    """(key id, secret, the key as it is shown once: eck_<id>_<secret>)."""
    key_id = secrets.token_hex(6)
    secret = secrets.token_urlsafe(30)
    return key_id, secret, f"eck_{key_id}_{secret}"


def split_api_key(key: str) -> tuple[str, str] | None:
    if not key.startswith("eck_"):
        return None
    parts = key[4:].split("_", 1)
    return (parts[0], parts[1]) if len(parts) == 2 and parts[0] and parts[1] else None


# ── who is asking ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Principal:
    role: str
    user_id: str | None = None
    email: str | None = None
    name: str = ""
    via: str = "local"  # local (sign-in off), session, key
    key_id: str | None = None
    workspace: str = DEFAULT_WORKSPACE
    # An Owner of the default workspace, so of the server (see the module's docstring).
    server_owner: bool = False

    def at_least(self, role: str) -> bool:
        return rank(self.role) >= rank(role)

    @property
    def label(self) -> str:
        """How the person shows up in the Inbox, the audit log and runs."""
        return self.name or self.email or "you"

    def public(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "via": self.via,
            "workspace": self.workspace,
            "server_owner": self.server_owner,
        }


LOCAL_OWNER = Principal(role="owner", name="", via="local", server_owner=True)


class WorkspaceRefused(Exception):
    """A request names a workspace that its person isn't in, or that doesn't exist."""

    def __init__(self, workspace: str):
        super().__init__(workspace)
        self.workspace = workspace


def asked_workspace(connection: HTTPConnection) -> str | None:
    """The workspace a request names, if it names one."""
    asked = connection.headers.get(WORKSPACE_HEADER) or connection.query_params.get("workspace")
    return asked.strip() or None if asked else None


# ── what each route needs ────────────────────────────────────────────────────

PUBLIC, SIGNED_IN, SERVER_OWNER = "public", "signed_in", "server_owner"

# (method, path) → the least role that may use it. "public" needs no sign-in; "signed_in" needs
# any role; "server_owner" an Owner of the default workspace. WebSocket routes use the method
# "WS".
PERMISSIONS: dict[tuple[str, str], str] = {
    # the app itself, health, sign-in
    ("GET", "/{path:path}"): PUBLIC,
    ("GET", "/"): PUBLIC,  # when the web app isn't built: a note saying so
    ("GET", "/api/health"): PUBLIC,
    ("GET", "/api/auth/status"): PUBLIC,
    ("POST", "/api/auth/setup"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    ("GET", "/api/auth/sso/{provider_id}"): PUBLIC,
    ("GET", "/api/auth/sso/{provider_id}/callback"): PUBLIC,
    ("GET", "/api/auth/me"): SIGNED_IN,
    ("POST", "/api/auth/password"): SIGNED_IN,
    # webhooks: their own secret token (in the address) is the check
    ("POST", "/api/hooks/{trigger_id}"): PUBLIC,
    ("POST", "/api/hooks/{trigger_id}/upload"): PUBLIC,
    # people and keys, and what everyone did
    ("GET", "/api/audit"): "owner",
    ("GET", "/api/people"): "owner",
    ("POST", "/api/people"): "owner",
    ("PATCH", "/api/people/{user_id}"): "owner",
    ("DELETE", "/api/people/{user_id}"): "owner",
    ("GET", "/api/workspaces"): SIGNED_IN,
    ("POST", "/api/workspaces"): SERVER_OWNER,
    ("PATCH", "/api/workspaces/{workspace_id}"): SERVER_OWNER,
    ("GET", "/api/keys"): SIGNED_IN,
    ("POST", "/api/keys"): SIGNED_IN,
    ("DELETE", "/api/keys/{key_id}"): SIGNED_IN,
    # reading
    ("GET", "/api/catalog"): "viewer",
    ("GET", "/api/schema"): "viewer",
    ("GET", "/api/providers"): "viewer",
    ("GET", "/api/templates"): "viewer",
    ("GET", "/api/templates/{template_id}"): "viewer",
    ("GET", "/api/flows"): "viewer",
    ("GET", "/api/flows/{flow_id}"): "viewer",
    ("GET", "/api/flows/{flow_id}/yaml"): "viewer",
    ("GET", "/api/flows/{flow_id}/export"): "viewer",
    ("GET", "/api/flows/{flow_id}/versions"): "viewer",
    ("GET", "/api/flows/{flow_id}/tests"): "viewer",
    ("GET", "/api/versions/{version_id}"): "viewer",
    ("POST", "/api/check"): "viewer",
    ("POST", "/api/compile"): "viewer",
    ("POST", "/api/export"): "viewer",
    ("GET", "/api/runs"): "viewer",
    ("GET", "/api/runs/{run_id}"): "viewer",
    ("GET", "/api/runs/{run_id}/events"): "viewer",
    ("WS", "/api/runs/{run_id}/ws"): "viewer",
    ("GET", "/api/runs/{run_id}/savepoints"): "viewer",
    ("GET", "/api/threads"): "viewer",
    ("GET", "/api/inbox"): "viewer",
    ("GET", "/api/inbox/{item_id}"): "viewer",
    ("GET", "/api/triggers"): "viewer",
    ("GET", "/api/knowledge"): "viewer",
    ("GET", "/api/knowledge/{kb_id}"): "viewer",
    ("GET", "/api/knowledge/{kb_id}/documents/{doc_id}/chunks"): "viewer",
    ("POST", "/api/knowledge/{kb_id}/search"): "viewer",
    ("GET", "/api/skills"): "viewer",
    ("GET", "/api/skills/{name}"): "viewer",
    ("GET", "/api/sandbox/status"): "viewer",
    ("GET", "/api/settings/copilot"): "viewer",
    # running (a flow given in full, rather than a saved one, needs an Editor: see the route)
    ("POST", "/api/runs"): "runner",
    ("POST", "/api/runs/{run_id}/cancel"): "runner",
    ("POST", "/api/runs/{run_id}/resume"): "runner",
    ("POST", "/api/runs/{run_id}/continue"): "runner",
    ("POST", "/api/runs/{run_id}/fork"): "runner",
    ("POST", "/api/inbox/{item_id}/answer"): "runner",
    # running a flow's Test Set (a flow given in full needs an Editor: see the route)
    ("POST", "/api/flows/{flow_id}/tests/run"): "runner",
    # editing
    ("POST", "/api/flows"): "editor",
    ("PUT", "/api/flows/{flow_id}"): "editor",
    ("DELETE", "/api/flows/{flow_id}"): "editor",
    ("POST", "/api/flows/{flow_id}/tests"): "editor",
    ("PUT", "/api/flows/{flow_id}/tests"): "editor",
    ("POST", "/api/triggers"): "editor",
    ("PATCH", "/api/triggers/{trigger_id}"): "editor",
    ("DELETE", "/api/triggers/{trigger_id}"): "editor",
    ("POST", "/api/knowledge"): "editor",
    ("PATCH", "/api/knowledge/{kb_id}"): "editor",
    ("DELETE", "/api/knowledge/{kb_id}"): "editor",
    ("POST", "/api/knowledge/{kb_id}/files"): "editor",
    ("POST", "/api/knowledge/{kb_id}/urls"): "editor",
    ("POST", "/api/knowledge/{kb_id}/text"): "editor",
    ("DELETE", "/api/knowledge/{kb_id}/documents/{doc_id}"): "editor",
    ("POST", "/api/knowledge-preview"): "editor",
    ("PUT", "/api/skills/{name}"): "editor",
    ("DELETE", "/api/skills/{name}"): "editor",
    ("POST", "/api/copilot"): "editor",
    ("PUT", "/api/settings/copilot"): "editor",
    ("POST", "/api/mcp/tools"): "editor",
    ("POST", "/api/openapi/inspect"): "editor",
    ("POST", "/api/openapi/steps"): "editor",
    ("GET", "/api/secrets"): "editor",
    ("GET", "/api/settings/mcp"): "editor",
    ("GET", "/api/settings/sandbox"): "editor",
    ("GET", "/api/settings/notifications"): "editor",
    ("GET", "/api/settings/network"): "editor",
    # the server and its secrets
    ("PUT", "/api/secrets/{name}"): "owner",
    ("DELETE", "/api/secrets/{name}"): "owner",
    ("PUT", "/api/settings/mcp"): "owner",
    ("PUT", "/api/settings/notifications"): "owner",
    ("POST", "/api/settings/notifications/test"): "owner",
    # what every workspace shares
    ("PUT", "/api/settings/sandbox"): SERVER_OWNER,
    ("POST", "/api/sandbox/prepare"): SERVER_OWNER,
    ("PUT", "/api/settings/network"): SERVER_OWNER,
    ("GET", "/api/settings/sso"): SERVER_OWNER,
    ("PUT", "/api/settings/sso"): SERVER_OWNER,
}


def _fail(connection: HTTPConnection, status: int, message: str, action: str) -> Exception:
    if connection.scope["type"] == "websocket":
        return WebSocketException(code=1008, reason=message)
    return HTTPException(status, detail={"message": message, "action": action})


def _too_low(connection: HTTPConnection, principal: Principal, need: str) -> Exception:
    if need == SERVER_OWNER:
        return _fail(
            connection,
            403,
            "Only the server's Owners (Owners of the default workspace) can do this, because "
            "it applies to every workspace.",
            "role",
        )
    return _fail(
        connection,
        403,
        f"Your role ({ROLE_LABELS[principal.role]}) can't do this; it needs "
        f"{ROLE_LABELS[need]} or above. An Owner can change your role.",
        "role",
    )


async def identify(connection: HTTPConnection) -> Principal | None:
    """The person behind a request (None: nobody signed in), in the workspace it names. Sign-in
    off: the Owner. Raises WorkspaceRefused for a workspace they can't be in."""
    asked = asked_workspace(connection)
    hub = getattr(connection.app.state, "hub", None)
    if auth_mode() == "off":
        if asked is None or asked == DEFAULT_WORKSPACE:
            return LOCAL_OWNER
        if hub is None or await hub.accounts.workspace(asked) is None:
            raise WorkspaceRefused(asked)
        return replace(LOCAL_OWNER, workspace=asked)
    accounts = hub.accounts
    header = connection.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return await accounts.principal_for_key(header[7:].strip(), asked)
    token = connection.cookies.get(SESSION_COOKIE)
    return await accounts.principal_for_session(token, asked) if token else None


async def authorize(connection: HTTPConnection) -> None:
    """Run for every route: let the request through, or say what's missing."""
    route = connection.scope.get("route")
    method = "WS" if connection.scope["type"] == "websocket" else connection.scope["method"]
    if method == "HEAD":
        method = "GET"
    need = PERMISSIONS.get((method, getattr(route, "path", "")))
    if need is None:
        # A route nobody declared: closed, never open (tests/test_auth.py catches it first).
        raise _fail(connection, 403, "This part of the API isn't open to anyone yet.", "none")
    try:
        principal = await identify(connection)
    except WorkspaceRefused as exc:
        if need != PUBLIC:
            raise _fail(
                connection,
                403,
                f"You aren't in the workspace “{exc.workspace}”, or it doesn't exist any more. "
                "Pick another workspace.",
                "workspace",
            ) from None
        principal = None
    connection.state.principal = principal
    if need == PUBLIC:
        return
    if principal is None:
        raise _fail(connection, 401, "Sign in first.", "sign_in")
    if need == SERVER_OWNER:
        if not (principal.server_owner and principal.at_least("owner")):
            raise _too_low(connection, principal, need)
    elif need != SIGNED_IN and not principal.at_least(need):
        raise _too_low(connection, principal, need)


def principal_of(connection: HTTPConnection) -> Principal | None:
    return getattr(connection.state, "principal", None)


def require(connection: HTTPConnection, role: str) -> Principal:
    """A check inside a route, for what depends on the request (a flow given in full)."""
    principal = principal_of(connection)
    if principal is None:
        raise _fail(connection, 401, "Sign in first.", "sign_in")
    if role == SERVER_OWNER:
        if not (principal.server_owner and principal.at_least("owner")):
            raise _too_low(connection, principal, role)
    elif not principal.at_least(role):
        raise _too_low(connection, principal, role)
    return principal
