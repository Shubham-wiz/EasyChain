"""Single sign-on with OpenID Connect (Phase 5.6e, docs/sso.md).

People sign in with their company's identity provider (Okta, Google, Microsoft Entra, Keycloak,
…) instead of an Easy Chain password. It uses the authorization code flow with PKCE:

1. ``GET /api/auth/sso/{id}`` sends the browser to the provider. It sets a short-lived cookie
   holding a random ``state``, and keeps that state's nonce and PKCE verifier in the database
   (so any API server can finish it).
2. The provider sends the browser back to ``/api/auth/sso/{id}/callback`` with a code. Easy
   Chain checks:
   - the state matches the cookie, so nobody else's sign-in can be slipped into this browser;
   - the code, exchanged with the client secret and the PKCE verifier;
   - the ID token: its signature (against the provider's published keys), issuer, audience,
     expiry and nonce;
   - that the email is verified.
3. The person is the account linked to their provider id (``user_identities``), else the
   account with their email (now linked), else a new account with the provider's role for new
   people, if their email's domain is allowed. Otherwise they are told to ask an Owner.

Providers come from Settings → Single sign-on, or from ``EASYCHAIN_SSO`` (a JSON list), which
wins. A client secret is kept in the secrets vault, never in settings.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .audit import record
from .auth import DEFAULT_WORKSPACE, ROLE_LABELS, ROLES, Principal, auth_mode
from .auth_api import set_session_cookie
from .secrets import NAME_RE, masked

log = logging.getLogger("easychain.sso")

STATE_COOKIE = "easychain_sso"
STATE_MINUTES = 10
PENDING = "sso_pending:"
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
# Only signatures with a public key: never "none", never a shared secret.
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"]
DISCOVERY_FOR = 3600.0


class SsoError(ValueError):
    """Signing in didn't work; the message is shown on the sign-in page."""


@dataclass
class Provider:
    id: str
    name: str
    issuer: str
    client_id: str
    # "{secret:NAME}" (a secret in the vault, or the server's environment)
    client_secret: str = ""
    # Accounts made for people who sign in for the first time: a role, or "none" (only people
    # an Owner added can sign in).
    new_people: str = "none"
    # Only these email domains (empty: any, for an existing account).
    domains: list[str] = field(default_factory=list)
    scopes: str = "openid email profile"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Provider:
        found = cls(
            id=str(raw.get("id", "")).strip().lower(),
            name=str(raw.get("name") or "").strip() or str(raw.get("id", "")),
            issuer=str(raw.get("issuer", "")).strip().rstrip("/"),
            client_id=str(raw.get("client_id", "")).strip(),
            client_secret=str(raw.get("client_secret") or ""),
            new_people=str(raw.get("new_people") or "none"),
            domains=[
                str(d).strip().lower().lstrip("@")
                for d in raw.get("domains") or []
                if str(d).strip()
            ],
            scopes=str(raw.get("scopes") or "openid email profile"),
        )
        found.check()
        return found

    def check(self) -> None:
        if not ID_RE.match(self.id):
            raise SsoError("A provider's id is lowercase letters, digits, - or _ (like okta).")
        if not re.match(r"^https://", self.issuer) and not re.match(
            r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/|$)", self.issuer
        ):
            raise SsoError(f"The issuer of “{self.name}” should be an https:// address.")
        if not self.client_id:
            raise SsoError(f"“{self.name}” needs its client id.")
        if self.new_people not in ("none", *ROLES):
            raise SsoError("New people get a role (viewer, runner, editor or owner), or none.")

    def secret(self) -> str:
        match = re.fullmatch(r"\{secret:([A-Za-z_][A-Za-z0-9_]*)\}", self.client_secret.strip())
        return os.environ.get(match.group(1), "") if match else self.client_secret

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}

    def shown(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "issuer": self.issuer,
            "client_id": self.client_id,
            "client_secret": masked(self.client_secret),
            "new_people": self.new_people,
            "domains": self.domains,
            "scopes": self.scopes,
        }


def providers(saved: dict[str, Any] | None) -> tuple[list[Provider], str]:
    """(the providers, where they come from: "environment" or "settings")."""
    env = os.environ.get("EASYCHAIN_SSO", "").strip()
    if env:
        try:
            return [Provider.from_dict(p) for p in json.loads(env)], "environment"
        except (ValueError, TypeError, SsoError) as exc:
            log.error("EASYCHAIN_SSO isn't usable (%s); single sign-on is off.", exc)
            return [], "environment"
    found = []
    for raw in (saved or {}).get("providers") or []:
        try:
            found.append(Provider.from_dict(raw))
        except SsoError as exc:
            log.warning("Skipping a single sign-on provider: %s", exc)
    return found, "settings"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _safe_next(value: str | None) -> str:
    """Where to go after signing in: only a page of this app."""
    if value and (value == "/" or value.startswith("/#/")):
        return value
    return "/"


class SsoSettings(BaseModel):
    providers: list[dict[str, Any]] = Field(default_factory=list, max_length=20)


def add_sso_routes(app: FastAPI) -> None:
    discovered: dict[str, tuple[float, dict[str, Any]]] = {}

    async def known(request: Request) -> tuple[list[Provider], str]:
        return providers(await app.state.hub.db.get_setting("sso", {}))

    async def find(request: Request, provider_id: str) -> Provider:
        found = next((p for p in (await known(request))[0] if p.id == provider_id), None)
        if found is None or auth_mode() != "on":
            raise HTTPException(404, detail={"message": "There's no such single sign-on here."})
        return found

    async def discovery(provider: Provider) -> dict[str, Any]:
        cached = discovered.get(provider.issuer)
        if cached and time.monotonic() - cached[0] < DISCOVERY_FOR:
            return cached[1]
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(f"{provider.issuer}/.well-known/openid-configuration")
            response.raise_for_status()
            doc = response.json()
        if doc.get("issuer", "").rstrip("/") != provider.issuer:
            raise SsoError(
                f"{provider.name} says its issuer is {doc.get('issuer')}, not {provider.issuer}."
            )
        discovered[provider.issuer] = (time.monotonic(), doc)
        return doc

    def callback_url(request: Request, provider: Provider) -> str:
        base = os.environ.get("EASYCHAIN_PUBLIC_URL", "").rstrip("/") or str(
            request.base_url
        ).rstrip("/")
        return f"{base}/api/auth/sso/{provider.id}/callback"

    def back_to_sign_in(message: str) -> RedirectResponse:
        response = RedirectResponse(f"/?sso_error={quote(message)}", status_code=303)
        response.delete_cookie(STATE_COOKIE, path="/api/auth/sso")
        return response

    @app.get("/api/auth/sso/{provider_id}")
    async def sso_start(
        provider_id: str, request: Request, next: str | None = None
    ) -> RedirectResponse:
        """Send the browser to the identity provider."""
        provider = await find(request, provider_id)
        try:
            doc = await discovery(provider)
        except (httpx.HTTPError, ValueError, SsoError) as exc:
            return back_to_sign_in(
                f"Couldn't reach {provider.name} to sign in ({exc}). Try again, or sign in with a password."
            )
        state, nonce, verifier = (
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(24),
            secrets.token_urlsafe(48),
        )
        db = app.state.hub.db
        # Sign-ins that were never finished are forgotten once they could no longer be.
        for key, value in (await db.settings_starting(PENDING)).items():
            if time.time() - float((value or {}).get("at", 0)) > STATE_MINUTES * 60:
                await db.delete_setting(key)
        await db.set_setting(
            PENDING + state,
            {
                "provider": provider.id,
                "nonce": nonce,
                "verifier": verifier,
                "next": _safe_next(next),
                "at": time.time(),
            },
        )
        query = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": callback_url(request, provider),
            "scope": provider.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
        }
        response = RedirectResponse(
            f"{doc['authorization_endpoint']}?{urlencode(query)}", status_code=303
        )
        secure = (
            request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https"
        )
        response.set_cookie(
            STATE_COOKIE,
            state,
            max_age=STATE_MINUTES * 60,
            httponly=True,
            samesite="lax",
            secure=secure,
            path="/api/auth/sso",
        )
        return response

    @app.get("/api/auth/sso/{provider_id}/callback")
    async def sso_callback(
        provider_id: str,
        request: Request,
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
        error_description: str | None = None,
    ) -> RedirectResponse:
        provider = await find(request, provider_id)
        db = app.state.hub.db
        pending = await db.get_setting(PENDING + state, None) if state else None
        if state:
            await db.delete_setting(PENDING + state)
        try:
            if error:
                raise SsoError(
                    f"{provider.name} didn't sign you in ({error_description or error})."
                )
            if not state or request.cookies.get(STATE_COOKIE) != state or not pending:
                raise SsoError(
                    "That sign-in didn't start in this browser, or took too long. Start again."
                )
            if (
                pending["provider"] != provider.id
                or time.time() - pending["at"] > STATE_MINUTES * 60
            ):
                raise SsoError("That sign-in took too long. Start again.")
            if not code:
                raise SsoError(f"{provider.name} didn't send a sign-in code back.")
            claims = await _exchange(
                provider, await discovery(provider), code, pending, callback_url(request, provider)
            )
            person, made = await _account(app, provider, claims)
        except SsoError as exc:
            await record(
                request, "auth.sso_failed", None, provider=provider.id, reason=str(exc)[:300]
            )
            return back_to_sign_in(str(exc))
        except (httpx.HTTPError, jwt.PyJWTError, KeyError, ValueError) as exc:
            log.warning("Single sign-on with %s failed: %s", provider.id, exc)
            message = (
                f"Couldn't finish signing in with {provider.name} ({type(exc).__name__}: {exc})."
            )
            await record(
                request, "auth.sso_failed", None, provider=provider.id, reason=message[:300]
            )
            return back_to_sign_in(message)
        accounts = app.state.hub.accounts
        member = await accounts.member(person["id"])
        who = Principal(
            role=(member or {}).get("role", "viewer"),
            user_id=person["id"],
            email=person["email"],
            name=person["name"],
            via="session",
        )
        if made:
            await record(
                request,
                "person.add",
                person["email"],
                principal=who,
                role=who.role,
                by=f"single sign-on ({provider.name})",
            )
        await record(request, "auth.sign_in", person["email"], principal=who, provider=provider.id)
        response = RedirectResponse(pending["next"], status_code=303)
        set_session_cookie(request, response, await accounts.start_session(person["id"]))
        response.delete_cookie(STATE_COOKIE, path="/api/auth/sso")
        return response

    @app.get("/api/settings/sso")
    async def get_sso(request: Request) -> dict[str, Any]:
        found, source = await known(request)
        return {
            "providers": [{**p.shown(), "callback": callback_url(request, p)} for p in found],
            "source": source,
            "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES],
        }

    @app.put("/api/settings/sso")
    async def set_sso(body: SsoSettings, request: Request) -> dict[str, Any]:
        if os.environ.get("EASYCHAIN_SSO", "").strip():
            raise HTTPException(
                409,
                detail={
                    "message": "The server's environment sets this (EASYCHAIN_SSO), so it can't be changed here."
                },
            )
        saved = {
            p["id"]: p
            for p in ((await app.state.hub.db.get_setting("sso", {})) or {}).get("providers") or []
        }
        kept = []
        for raw in body.providers:
            try:
                provider = Provider.from_dict(raw)
            except SsoError as exc:
                raise HTTPException(422, detail={"message": str(exc)}) from exc
            value = str(raw.get("client_secret") or "")
            if (
                value == masked(saved.get(provider.id, {}).get("client_secret", "x"))
                and provider.id in saved
            ):
                value = saved[provider.id].get("client_secret", "")
            elif value and not re.fullmatch(r"\{secret:[A-Za-z_][A-Za-z0-9_]*\}", value.strip()):
                # A secret typed in goes to the vault; settings keep only its name.
                name = f"SSO_{provider.id.upper().replace('-', '_')}_CLIENT_SECRET"
                assert NAME_RE.match(name)
                app.state.vault.set(name, value)
                value = "{secret:" + name + "}"
            kept.append({**provider.shown(), "client_secret": value})
        await app.state.hub.db.set_setting("sso", {"providers": kept})
        return await get_sso(request)


async def _exchange(
    provider: Provider, doc: dict[str, Any], code: str, pending: dict[str, Any], redirect_uri: str
) -> dict[str, Any]:
    """Swap the code for an ID token and check the token; return its claims."""
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": provider.client_id,
                "client_secret": provider.secret(),
                "code_verifier": pending["verifier"],
            },
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            try:
                reason = response.json().get("error_description") or response.json().get("error")
            except ValueError:
                reason = response.text[:200]
            raise SsoError(f"{provider.name} refused the sign-in code ({reason}).")
        token = response.json().get("id_token")
        if not token:
            raise SsoError(f"{provider.name} didn't send an ID token.")
        keys = (await client.get(doc["jwks_uri"])).json()
    kid = jwt.get_unverified_header(token).get("kid")
    candidates = [k for k in keys.get("keys", []) if not kid or k.get("kid") == kid]
    if not candidates:
        raise SsoError(f"{provider.name}'s sign-in was signed with a key it doesn't publish.")
    key = jwt.PyJWK(candidates[0])
    try:
        claims = jwt.decode(
            token,
            key=key.key,
            algorithms=ALGORITHMS,
            audience=provider.client_id,
            issuer=provider.issuer,
            leeway=60,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.InvalidSignatureError as exc:
        raise SsoError(
            f"The sign-in from {provider.name} has a signature that doesn't check out."
        ) from exc
    except jwt.ExpiredSignatureError as exc:
        raise SsoError(f"The sign-in from {provider.name} has expired. Start again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise SsoError(
            f"The sign-in from {provider.name} was meant for another app (check the client id)."
        ) from exc
    except jwt.InvalidIssuerError as exc:
        raise SsoError(
            f"The sign-in didn't come from {provider.name} (its issuer is wrong)."
        ) from exc
    if not secrets.compare_digest(str(claims.get("nonce", "")), pending["nonce"]):
        raise SsoError(
            f"The sign-in from {provider.name} doesn't belong to this attempt. Start again."
        )
    return claims


async def _account(
    app: FastAPI, provider: Provider, claims: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """(the account to sign in, whether it was just made)."""
    accounts = app.state.hub.accounts
    email = str(claims.get("email") or "").strip().lower()
    if claims.get("email_verified") is False:
        raise SsoError(
            f"{provider.name} hasn't checked the email {email}, so Easy Chain can't use it."
        )
    person = await accounts.by_identity(provider.id, str(claims["sub"]))
    made = False
    if person is None:
        if not email:
            raise SsoError(f"{provider.name} didn't say your email (ask for the email scope).")
        domain = email.rsplit("@", 1)[-1]
        if provider.domains and domain not in provider.domains:
            raise SsoError(
                f"Single sign-on with {provider.name} is only for {', '.join('@' + d for d in provider.domains)} addresses."
            )
        person = await accounts.by_email(email)
        if person is None:
            if provider.new_people == "none":
                raise SsoError(
                    f"There's no Easy Chain account for {email}. Ask an Owner to add you."
                )
            name = str(claims.get("name") or claims.get("preferred_username") or "")
            person = await accounts.create(
                email, name, None, provider.new_people, DEFAULT_WORKSPACE
            )
            made = True
    if person.get("disabled"):
        raise SsoError(f"The account {person['email']} is switched off. Ask an Owner.")
    await accounts.link_identity(
        person["id"], provider.id, str(claims["sub"]), email or person["email"]
    )
    return person, made
