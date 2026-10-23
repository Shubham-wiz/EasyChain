"""A small OpenID Connect provider for tests and e2e (single sign-on, Phase 5.6e).

It does what Easy Chain needs from Okta, Google or Microsoft Entra:

- discovery: ``/.well-known/openid-configuration``;
- its signing keys: ``/jwks``;
- an authorize page, a form that asks for an email. ``login_hint`` skips the form;
- a token endpoint that checks the client secret, the redirect address and PKCE, and
  returns an RS256 ID token.

Run it on its own with::

    python -m easychain.testing.oidc_provider --port 8127 --client-id easychain --client-secret s3cret

Tests can make it misbehave, to check that Easy Chain refuses what it should
(``provider.misbehave = "wrong_nonce"``: see ``_claims``).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class TestProvider:
    """``with TestProvider() as idp:`` serves on 127.0.0.1 (``idp.issuer``)."""

    __test__ = False  # not a pytest class

    def __init__(
        self,
        client_id: str = "easychain",
        client_secret: str = "s3cret",
        port: int = 0,
        users: dict[str, dict[str, Any]] | None = None,
    ):
        self.client_id, self.client_secret = client_id, client_secret
        self.users = users or {}
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = secrets.token_hex(4)
        self.codes: dict[str, dict[str, Any]] = {}
        self.misbehave: str | None = None
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self.server.provider = self  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def issuer(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> TestProvider:
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()

    def serve_forever(self) -> None:
        self.server.serve_forever()

    # ── what it hands out ────────────────────────────────────────────────────

    def jwks(self) -> dict[str, Any]:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def person(self, email: str) -> dict[str, Any]:
        known = self.users.get(email.lower(), {})
        return {
            "sub": known.get("sub")
            or "u-" + hashlib.sha256(email.lower().encode()).hexdigest()[:12],
            "email": email.lower(),
            "email_verified": known.get("email_verified", True),
            "name": known.get("name", email.split("@")[0].title()),
        }

    def _claims(self, grant: dict[str, Any]) -> dict[str, Any]:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": grant["nonce"],
            **self.person(grant["email"]),
        }
        if self.misbehave == "wrong_nonce":
            claims["nonce"] = "not-the-one-sent"
        elif self.misbehave == "wrong_audience":
            claims["aud"] = "another-app"
        elif self.misbehave == "wrong_issuer":
            claims["iss"] = "https://someone-else.example.com"
        elif self.misbehave == "expired":
            claims["iat"], claims["exp"] = now - 3600, now - 1800
        elif self.misbehave == "unverified_email":
            claims["email_verified"] = False
        return claims

    def id_token(self, grant: dict[str, Any]) -> str:
        key: Any = self.key
        if self.misbehave == "wrong_key":
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        return jwt.encode(self._claims(grant), key, algorithm="RS256", headers={"kid": self.kid})


class _Handler(BaseHTTPRequestHandler):
    server: Any

    def log_message(self, *args: Any) -> None:
        pass

    @property
    def idp(self) -> TestProvider:
        return self.server.provider

    def _send(self, status: int, body: Any, content_type: str = "application/json") -> None:
        data = (
            body
            if isinstance(body, bytes)
            else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.path == "/.well-known/openid-configuration":
            issuer = self.idp.issuer
            self._send(
                200,
                {
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/token",
                    "jwks_uri": f"{issuer}/jwks",
                    "response_types_supported": ["code"],
                    "subject_types_supported": ["public"],
                    "id_token_signing_alg_values_supported": ["RS256"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        elif parts.path == "/jwks":
            self._send(200, self.idp.jwks())
        elif parts.path == "/authorize":
            if query.get("login_hint"):
                self._authorize(query, query["login_hint"])
                return
            fields = "".join(
                f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                for k, v in query.items()
            )
            self._send(
                200,
                "<!doctype html><title>Test sign-in</title><main><h1>Test identity provider</h1>"
                f'<form method="post" action="/authorize">{fields}'
                '<label>Email <input name="email" type="email" autofocus></label> '
                "<button>Sign in</button></form></main>",
                "text/html; charset=utf-8",
            )
        else:
            self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        path = urlsplit(self.path).path
        if path == "/authorize":
            self._authorize(form, form.get("email", ""))
        elif path == "/token":
            self._token(form)
        else:
            self._send(404, {"error": "not_found"})

    def _authorize(self, query: dict[str, str], email: str) -> None:
        if query.get("client_id") != self.idp.client_id or not email:
            self._send(400, {"error": "invalid_request"})
            return
        if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
            self._send(400, {"error": "invalid_request", "error_description": "PKCE is required"})
            return
        code = secrets.token_urlsafe(16)
        self.idp.codes[code] = {
            "email": email,
            "nonce": query.get("nonce", ""),
            "redirect_uri": query.get("redirect_uri", ""),
            "challenge": query["code_challenge"],
        }
        back = {"code": code, "state": query.get("state", "")}
        if self.idp.misbehave == "deny":
            back = {"error": "access_denied", "state": query.get("state", "")}
        self._redirect(f"{query.get('redirect_uri', '')}?{urlencode(back)}")

    def _token(self, form: dict[str, str]) -> None:
        basic = self.headers.get("Authorization", "")
        client_id, client_secret = form.get("client_id"), form.get("client_secret")
        if basic.startswith("Basic "):
            client_id, _, client_secret = base64.b64decode(basic[6:]).decode().partition(":")
        if (client_id, client_secret) != (self.idp.client_id, self.idp.client_secret):
            self._send(401, {"error": "invalid_client"})
            return
        grant = self.idp.codes.pop(form.get("code", ""), None)
        if grant is None or grant["redirect_uri"] != form.get("redirect_uri"):
            self._send(400, {"error": "invalid_grant"})
            return
        digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant["challenge"]:
            self._send(400, {"error": "invalid_grant", "error_description": "PKCE check failed"})
            return
        self._send(
            200,
            {
                "access_token": secrets.token_urlsafe(16),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.idp.id_token(grant),
            },
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8127)
    parser.add_argument("--client-id", default="easychain")
    parser.add_argument("--client-secret", default="s3cret")
    args = parser.parse_args(argv)
    provider = TestProvider(args.client_id, args.client_secret, args.port)
    print(f"Test identity provider at {provider.issuer}", flush=True)
    provider.serve_forever()


if __name__ == "__main__":
    main()
