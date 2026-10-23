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
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, WebSocketException
from starlette.requests import HTTPConnection

ROLES = ("viewer", "runner", "editor", "owner")
ROLE_LABELS = {"viewer": "Viewer", "runner": "Runner", "editor": "Editor", "owner": "Owner"}
DEFAULT_WORKSPACE = "default"
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
        }


LOCAL_OWNER = Principal(role="owner", name="", via="local")


# ── what each route needs ────────────────────────────────────────────────────

PUBLIC, SIGNED_IN = "public", "signed_in"

# (method, path) → the least role that may use it. "public" needs no sign-in; "signed_in" needs
# any role. WebSocket routes use the method "WS".
PERMISSIONS: dict[tuple[str, str], str] = {
    # the app itself, health, sign-in
    ("GET", "/{path:path}"): PUBLIC,
    ("GET", "/"): PUBLIC,  # when the web app isn't built: a note saying so
    ("GET", "/api/health"): PUBLIC,
    ("GET", "/api/auth/status"): PUBLIC,
    ("POST", "/api/auth/setup"): PUBLIC,
    ("POST", "/api/auth/login"): PUBLIC,
    ("POST", "/api/auth/logout"): PUBLIC,
    ("GET", "/api/auth/me"): SIGNED_IN,
    ("POST", "/api/auth/password"): SIGNED_IN,
    # webhooks: their own secret token (in the address) is the check
    ("POST", "/api/hooks/{trigger_id}"): PUBLIC,
    ("POST", "/api/hooks/{trigger_id}/upload"): PUBLIC,
    # people and keys
    ("GET", "/api/people"): "owner",
    ("POST", "/api/people"): "owner",
    ("PATCH", "/api/people/{user_id}"): "owner",
    ("DELETE", "/api/people/{user_id}"): "owner",
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
    # editing
    ("POST", "/api/flows"): "editor",
    ("PUT", "/api/flows/{flow_id}"): "editor",
    ("DELETE", "/api/flows/{flow_id}"): "editor",
    ("POST", "/api/flows/{flow_id}/tests"): "editor",
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
    # the server and its secrets
    ("PUT", "/api/secrets/{name}"): "owner",
    ("DELETE", "/api/secrets/{name}"): "owner",
    ("PUT", "/api/settings/mcp"): "owner",
    ("PUT", "/api/settings/sandbox"): "owner",
    ("POST", "/api/sandbox/prepare"): "owner",
    ("PUT", "/api/settings/notifications"): "owner",
    ("POST", "/api/settings/notifications/test"): "owner",
}


def _fail(connection: HTTPConnection, status: int, message: str, action: str) -> Exception:
    if connection.scope["type"] == "websocket":
        return WebSocketException(code=1008, reason=message)
    return HTTPException(status, detail={"message": message, "action": action})


def _too_low(connection: HTTPConnection, principal: Principal, need: str) -> Exception:
    return _fail(
        connection,
        403,
        f"Your role ({ROLE_LABELS[principal.role]}) can't do this; it needs "
        f"{ROLE_LABELS[need]} or above. An Owner can change your role.",
        "role",
    )


async def identify(connection: HTTPConnection) -> Principal | None:
    """The person behind a request (None: nobody signed in). Sign-in off: the Owner."""
    if auth_mode() == "off":
        return LOCAL_OWNER
    accounts = connection.app.state.hub.accounts
    header = connection.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return await accounts.principal_for_key(header[7:].strip())
    token = connection.cookies.get(SESSION_COOKIE)
    return await accounts.principal_for_session(token) if token else None


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
    principal = await identify(connection)
    connection.state.principal = principal
    if need == PUBLIC:
        return
    if principal is None:
        raise _fail(connection, 401, "Sign in first.", "sign_in")
    if need != SIGNED_IN and not principal.at_least(need):
        raise _too_low(connection, principal, need)


def principal_of(connection: HTTPConnection) -> Principal | None:
    return getattr(connection.state, "principal", None)


def require(connection: HTTPConnection, role: str) -> Principal:
    """A check inside a route, for what depends on the request (a flow given in full)."""
    principal = principal_of(connection)
    if principal is None:
        raise _fail(connection, 401, "Sign in first.", "sign_in")
    if not principal.at_least(role):
        raise _too_low(connection, principal, role)
    return principal
