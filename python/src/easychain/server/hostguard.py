"""Keep web pages on other sites away from the local API.

Easy Chain has no login yet (Phase 5), so the API trusts whoever reaches it. Binding to
127.0.0.1 keeps other machines out, but a web page open in the user's own browser can still
try to reach it:

- **DNS rebinding**: a page on ``evil.example`` re-points its own name at 127.0.0.1, then calls
  the API as if it were same-origin. The request carries ``Host: evil.example``, so only known
  host names are accepted.
- **Cross-site requests**: a page can POST a form or open a WebSocket to
  ``http://127.0.0.1:8000`` without CORS stepping in. Browsers send an ``Origin`` header with
  those, so requests that change things are refused when they come from another site.

Requests without an ``Origin`` (curl, webhooks from other services, the CLI) are not affected by
the second rule. ``EASYCHAIN_ALLOWED_HOSTS`` adds host names (comma-separated, ``*.example.com``
for sub-domains, ``*`` for any), for example when the app sits behind a reverse proxy.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "*.localhost")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def allowed_hosts(extra: Iterable[str] = ()) -> list[str]:
    """The host names this server answers to, from the defaults and the environment."""
    hosts = list(LOCAL_HOSTS)
    hosts += [h.strip().lower() for h in os.environ.get("EASYCHAIN_ALLOWED_HOSTS", "").split(",")]
    for url in (
        os.environ.get("EASYCHAIN_PUBLIC_URL"),
        *os.environ.get("EASYCHAIN_CORS_ORIGINS", "").split(","),
    ):
        if url and urlsplit(url.strip()).hostname:
            hosts.append(urlsplit(url.strip()).hostname)
    bind = os.environ.get("EASYCHAIN_HOST", "")
    if bind and bind not in ("0.0.0.0", "::"):
        hosts.append(bind.lower())
    hosts += list(extra)
    return sorted({h for h in hosts if h})


def _host_name(value: str) -> str:
    """The name part of a Host header or an origin (no port, no brackets, lower case)."""
    if "://" in value:
        return (urlsplit(value).hostname or "").lower()
    value = value.strip().lower()
    if value.startswith("["):  # [::1]:8000
        return value[1:].split("]")[0]
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def _matches(name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == "*" or pattern == name:
            return True
        if pattern.startswith("*.") and name.endswith(pattern[1:]):
            return True
    return False


class HostGuard:
    """ASGI middleware applying the two rules above."""

    def __init__(self, app: Any, hosts: list[str]):
        self.app = app
        self.hosts = hosts

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        host = _host_name(headers.get("host", ""))
        if not _matches(host, self.hosts):
            await self._refuse(
                scope,
                send,
                400,
                f"Easy Chain doesn't answer to the host name “{host}”. If you reach it by this "
                "name on purpose, add it to EASYCHAIN_ALLOWED_HOSTS.",
            )
            return
        origin = headers.get("origin")
        changes = scope["type"] == "websocket" or scope.get("method") not in SAFE_METHODS
        if scope.get("path", "").startswith("/api/hooks/"):
            changes = False  # trigger addresses check their own secret token, from any site
        if origin and changes and origin != "null" and not _matches(_host_name(origin), self.hosts):
            await self._refuse(
                scope,
                send,
                403,
                f"Requests from the web page at {origin} aren't allowed. If that is your own "
                "site, add its host name to EASYCHAIN_ALLOWED_HOSTS.",
            )
            return
        if origin == "null" and changes:
            await self._refuse(scope, send, 403, "Requests from sandboxed pages aren't allowed.")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _refuse(scope: dict[str, Any], send: Any, status: int, message: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"message": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
