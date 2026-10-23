"""Signing in, the first account, people, workspaces and API keys (server/auth.py has the
rules)."""

from __future__ import annotations

import re
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .accounts import AccountError
from .audit import note, record
from .auth import (
    DEFAULT_WORKSPACE,
    LOCAL_OWNER,
    ROLE_LABELS,
    ROLES,
    SESSION_COOKIE,
    SESSION_DAYS,
    Principal,
    auth_mode,
    password_problem,
    principal_of,
    rank,
)
from .spaces import ID_RE

# Wrong passwords: after MAX_FAILURES for one email in WINDOW seconds, signing in to it waits
# until the window has passed. One address gets more (people behind one proxy or office share
# it), and the same count covers wrong first-account codes.
MAX_FAILURES = 5
MAX_FAILURES_PER_ADDRESS = 30
WINDOW = 15 * 60


class SetupRequest(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    password: str = Field(min_length=1, max_length=500)


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=500)


class PasswordRequest(BaseModel):
    current: str = Field(default="", max_length=500)
    new: str = Field(min_length=1, max_length=500)


class PersonRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    role: str = "viewer"
    # A first password for someone new; someone who already has an account keeps theirs.
    password: str | None = Field(default=None, max_length=500)


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    id: str | None = Field(default=None, max_length=40)


class WorkspaceUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class PersonUpdate(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    name: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, max_length=500)


class KeyRequest(BaseModel):
    name: str = Field(default="API key", max_length=200)
    role: str | None = None


def _bad(message: str, status: int = 422) -> HTTPException:
    return HTTPException(status, detail={"message": message})


def _person(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "role": row["role"],
        "role_label": ROLE_LABELS.get(row["role"], row["role"]),
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "last_login_at": row.get("last_login_at"),
    }


def _workspace_id(name: str) -> str:
    """An id for a workspace called ``name``: "Sales team" → "sales-team"."""
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:36].strip("-")
    if not base or not base[0].isalpha():
        base = f"ws-{base}".strip("-")
    return base if len(base) >= 2 else f"{base}-1"


def _workspace_of(request: Request) -> str:
    principal = principal_of(request)
    return principal.workspace if principal else DEFAULT_WORKSPACE


def set_session_cookie(request: Request, response: Response, token: str) -> None:
    """The signed-in browser's cookie: HttpOnly, SameSite=Lax, Secure over HTTPS."""
    secure = request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_DAYS * 86400,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _as(person: dict[str, Any], role: str) -> Principal:
    """Who signed in, for the audit log (the request had no sign-in yet)."""
    return Principal(
        role=role, user_id=person["id"], email=person["email"], name=person["name"], via="session"
    )


def add_auth_routes(app: FastAPI) -> None:
    failures: dict[str, deque[float]] = defaultdict(deque)

    def accounts() -> Any:
        return app.state.hub.accounts

    def _locked(**keys: str) -> bool:
        now = time.time()
        for kind, key in keys.items():
            seen = failures.get(key)
            if seen is None:
                continue
            while seen and now - seen[0] > WINDOW:
                seen.popleft()
            if not seen:
                del failures[key]
            elif len(seen) >= (MAX_FAILURES_PER_ADDRESS if kind == "address" else MAX_FAILURES):
                return True
        return False

    def _failed(*keys: str) -> None:
        for key in keys:
            failures[key].append(time.time())

    def _address(request: Request) -> str:
        return f"ip:{request.client.host if request.client else '?'}"

    def _too_many() -> HTTPException:
        return _bad(
            "Too many wrong tries. Wait 15 minutes, or ask an Owner to set a new password for you.",
            429,
        )

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        """Whether sign-in is on, whether someone is signed in, and whether the first account is
        still to be made."""
        principal = principal_of(request)
        mode = auth_mode()
        from .sso import providers

        sso = providers(await app.state.hub.db.get_setting("sso", {}))[0] if mode == "on" else []
        return {
            "mode": mode,
            "signed_in": principal is not None,
            "setup_needed": mode == "on" and await accounts().count() == 0,
            "me": principal.public() if principal else None,
            "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES],
            # Single sign-on providers to offer on the sign-in page.
            "sso": [p.public() for p in sso],
        }

    @app.post("/api/auth/setup")
    async def setup(req: SetupRequest, request: Request, response: Response) -> dict[str, Any]:
        """Make the first account (an Owner), with the code from the server's log."""
        if auth_mode() != "on":
            raise _bad("Sign-in is off on this server, so there's no account to make.", 409)
        if _locked(address=_address(request)):
            raise _too_many()
        if not await accounts().check_setup_code(req.code):
            _failed(_address(request))
            await record(request, "auth.setup_failed", req.email.strip().lower())
            raise _bad(
                "That code doesn't match. The server prints it in its log when it starts "
                '("enter the code …"); restarting it prints a new one.',
                403,
            )
        if problem := password_problem(req.password):
            raise _bad(problem)
        try:
            person = await accounts().create(req.email, req.name, req.password, "owner", first=True)
        except AccountError as exc:
            raise _bad(str(exc), 409) from exc
        await accounts().forget_setup_code()
        set_session_cookie(request, response, await accounts().start_session(person["id"]))
        note(request, person["email"], actor=_as(person, "owner"))
        return {"me": {**_person(person), "via": "session"}}

    @app.post("/api/auth/login")
    async def login(req: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        if auth_mode() != "on":
            raise _bad("Sign-in is off on this server; there's nothing to sign in to.", 409)
        email = req.email.strip().lower()
        if _locked(email=f"email:{email}", address=_address(request)):
            raise _too_many()
        person = await accounts().check_password(email, req.password)
        if person is None:
            _failed(f"email:{email}", _address(request))
            await record(request, "auth.sign_in_failed", email)
            raise _bad("That email and password don't match an account.", 401)
        failures.pop(f"email:{email}", None)
        who = await accounts().signed_in_as(person["id"])
        if who is None:
            raise _bad("You aren't in any workspace yet. Ask an Owner to add you.", 403)
        set_session_cookie(request, response, await accounts().start_session(person["id"]))
        note(request, email, actor=who)
        return {"me": who.public()}

    @app.post("/api/auth/logout")
    async def logout(request: Request, response: Response) -> dict[str, Any]:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await accounts().end_session(token)
        note(request, skip=principal_of(request) is None or principal_of(request).via != "session")
        response.delete_cookie(SESSION_COOKIE, path="/")
        return {"signed_in": False}

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict[str, Any]:
        """Who is signed in, in which workspace, and the workspaces they can switch to."""
        principal = principal_of(request)
        if principal is None:
            return {}
        return {**principal.public(), "workspaces": await _workspaces_for(principal)}

    @app.post("/api/auth/password")
    async def change_password(req: PasswordRequest, request: Request) -> dict[str, Any]:
        principal = principal_of(request)
        if principal is None or principal.user_id is None:
            raise _bad("Sign-in is off on this server, so there's no password to change.", 409)
        person = await accounts().check_password(principal.email or "", req.current)
        if person is None:
            raise _bad("Your current password isn't right.", 403)
        if problem := password_problem(req.new):
            raise _bad(problem)
        await accounts().set_password(
            principal.user_id, req.new, keep_session=request.cookies.get(SESSION_COOKIE)
        )
        return {"changed": True}

    # ── people (Owners) ──────────────────────────────────────────────────────

    @app.get("/api/people")
    async def list_people(request: Request) -> dict[str, Any]:
        """The people in this workspace."""
        return {"people": [_person(p) for p in await accounts().members(_workspace_of(request))]}

    @app.post("/api/people", status_code=201)
    async def add_person(req: PersonRequest, request: Request) -> dict[str, Any]:
        """Add someone to this workspace: new, with a first password (they change it after
        signing in), or someone who already has an account (they keep their password)."""
        workspace = _workspace_of(request)
        found = await accounts().by_email(req.email)
        try:
            if found is not None:
                person = await accounts().add_member(found["id"], req.role, workspace)
            else:
                if not req.password:
                    raise _bad("Give them a first password; they change it after signing in.")
                if problem := password_problem(req.password):
                    raise _bad(problem)
                person = await accounts().create(
                    req.email, req.name, req.password, req.role, workspace
                )
        except AccountError as exc:
            raise _bad(str(exc), 409) from exc
        note(request, person["email"], role=person["role"])
        return {**_person(person), "existing": found is not None}

    @app.patch("/api/people/{user_id}")
    async def update_person(user_id: str, req: PersonUpdate, request: Request) -> dict[str, Any]:
        workspace = _workspace_of(request)
        principal = principal_of(request) or LOCAL_OWNER
        account_wide = req.disabled is not None or req.name is not None or bool(req.password)
        if account_wide and not principal.server_owner:
            elsewhere = set(await accounts().roles_of(user_id)) - {workspace}
            if elsewhere:
                raise _bad(
                    "This person is in other workspaces too, so only the server's Owners can "
                    "change their account (name, password, or turning it off). You can change "
                    "their role here, or remove them from this workspace.",
                    403,
                )
        try:
            person = await accounts().update(
                user_id, role=req.role, disabled=req.disabled, name=req.name, workspace=workspace
            )
            if req.password:
                if problem := password_problem(req.password):
                    raise _bad(problem)
                await accounts().set_password(user_id, req.password)
        except AccountError as exc:
            raise _bad(str(exc), 409) from exc
        changed = {k: v for k, v in req.model_dump(exclude={"password"}).items() if v is not None}
        note(request, person["email"], **changed, **({"password": "set"} if req.password else {}))
        return _person(person)

    @app.delete("/api/people/{user_id}")
    async def remove_person(user_id: str, request: Request) -> dict[str, Any]:
        """Take someone out of this workspace (their account goes when they're in none)."""
        workspace = _workspace_of(request)
        gone = await accounts().member(user_id, workspace)
        try:
            await accounts().remove(user_id, workspace)
        except AccountError as exc:
            raise _bad(str(exc), 409) from exc
        note(request, gone["email"] if gone else user_id)
        return {"removed": user_id}

    # ── workspaces ───────────────────────────────────────────────────────────

    async def _workspaces_for(principal: Principal | None) -> list[dict[str, Any]]:
        """The workspaces someone can switch to (the server's Owners also see the others)."""
        if principal is None:
            return []
        every = await accounts().workspaces()
        if principal.via == "local":
            roles = {w["id"]: "owner" for w in every}
        elif principal.via == "key":
            roles = {principal.workspace: principal.role}
        else:
            roles = await accounts().roles_of(principal.user_id or "")
        shown = []
        for found in every:
            role = roles.get(found["id"])
            if role is None and not (principal.server_owner and principal.via != "key"):
                continue
            shown.append(
                {
                    "id": found["id"],
                    "name": found["name"],
                    "people": found["people"],
                    "created_at": found["created_at"],
                    "role": role,
                    "role_label": ROLE_LABELS.get(role, "") if role else None,
                    "current": found["id"] == principal.workspace,
                }
            )
        return shown

    @app.get("/api/workspaces")
    async def list_workspaces(request: Request) -> dict[str, Any]:
        principal = principal_of(request)
        return {
            "workspaces": await _workspaces_for(principal),
            "current": principal.workspace if principal else DEFAULT_WORKSPACE,
        }

    @app.post("/api/workspaces", status_code=201)
    async def create_workspace(req: WorkspaceRequest, request: Request) -> dict[str, Any]:
        """A new workspace (server Owners). Whoever makes it is its first Owner."""
        principal = principal_of(request) or LOCAL_OWNER
        if req.id is not None:
            if not ID_RE.match(req.id):
                raise _bad(
                    "A workspace id is 2 to 40 lowercase letters, digits and dashes, starting "
                    "with a letter."
                )
            workspace_id = req.id
        else:
            base = _workspace_id(req.name)
            taken = {w["id"] for w in await accounts().workspaces()}
            workspace_id, n = base, 2
            while workspace_id in taken:
                workspace_id, n = f"{base}-{n}", n + 1
        try:
            made = await accounts().create_workspace(workspace_id, req.name, principal.user_id)
        except AccountError as exc:
            raise _bad(str(exc), 409) from exc
        app.state.hub.spaces.get(workspace_id)  # its folders
        note(request, workspace_id, name=made["name"])
        return {**made, "role": "owner", "people": 1 if principal.user_id else 0}

    @app.patch("/api/workspaces/{workspace_id}")
    async def rename_workspace(
        workspace_id: str, req: WorkspaceUpdate, request: Request
    ) -> dict[str, Any]:
        try:
            changed = await accounts().rename_workspace(workspace_id, req.name)
        except AccountError as exc:
            raise _bad(str(exc), 404) from exc
        note(request, workspace_id, name=changed["name"])
        return changed

    # ── API keys ─────────────────────────────────────────────────────────────

    @app.get("/api/keys")
    async def list_keys(request: Request) -> dict[str, Any]:
        """Your keys (an Owner sees everyone's, to revoke them)."""
        principal = principal_of(request)
        if principal is None or principal.user_id is None:
            return {"keys": [], "note": "Sign-in is off on this server, so keys aren't needed."}
        everyone = principal.role == "owner"
        keys = await accounts().keys(
            None if everyone else principal.user_id, workspace=principal.workspace
        )
        return {"keys": [{**k, "mine": k["user_id"] == principal.user_id} for k in keys]}

    @app.post("/api/keys", status_code=201)
    async def create_key(req: KeyRequest, request: Request) -> dict[str, Any]:
        """Make a key for scripts and other apps. The key is shown once: copy it now."""
        principal = principal_of(request)
        if principal is None or principal.user_id is None:
            raise _bad("Sign-in is off on this server, so keys aren't needed.", 409)
        if principal.via == "key":
            raise _bad("Make keys while signed in, not with another key.", 403)
        role = req.role or principal.role
        if rank(role) > rank(principal.role):
            raise _bad("A key can't have a higher role than yours.", 403)
        try:
            made = await accounts().create_key(
                principal.user_id, req.name, role, principal.workspace
            )
        except AccountError as exc:
            raise _bad(str(exc)) from exc
        note(request, made["name"], key=made["id"], role=role)
        return made

    @app.delete("/api/keys/{key_id}")
    async def revoke_key(key_id: str, request: Request) -> dict[str, Any]:
        principal = principal_of(request)
        if principal is None or principal.user_id is None:
            raise _bad("Sign-in is off on this server, so there are no keys.", 409)
        owner = principal.role == "owner"
        if not await accounts().revoke_key(
            key_id, None if owner else principal.user_id, workspace=principal.workspace
        ):
            raise _bad("There's no such key of yours.", 404)
        return {"revoked": key_id}
