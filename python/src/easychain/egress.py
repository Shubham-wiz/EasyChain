"""Which addresses Easy Chain may reach on a server (Phase 5.6f, docs/network.md).

On a shared server, the web addresses people give Easy Chain (a Knowledge Base page, an
OpenAPI document, a Web request step, an MCP server) could point inside the network: the
cloud's metadata service (169.254.169.254), a database, an admin page. The **egress policy**
says what may be reached:

=============  ==============================================================================
``open``       anything (the default with sign-in off: one person on their own computer)
``public``     public addresses, and internal ones only if they're on the allow-list (the
               default with sign-in on)
``allow-list`` only what's on the allow-list
=============  ==============================================================================

The allow-list holds host names (``docs.example.com``), name suffixes (``*.example.com``),
addresses and networks (``10.1.2.3``, ``10.0.0.0/8``). A name on the list is allowed whatever
it resolves to.

Checks happen twice: on the URL of every request and redirect (``check_url``), and, for direct
connections, on the address actually connected to (``GuardedBackend``), so a name that
resolves to a public address when checked and an internal one when connecting (DNS
rebinding) is still caught. Through a proxy, only the first check applies.

The policy of the work in hand is a context variable: ``using(policy)`` around it. With none
set, nothing is checked. Calls to AI model providers aren't checked: their addresses come from
the server's own settings, not from flows.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpcore
import httpx

MODES = ("open", "public", "allow-list")
Address = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class EgressBlocked(PermissionError):
    """Easy Chain didn't connect, because the address isn't allowed; the message says why."""


@dataclass(frozen=True)
class EgressPolicy:
    mode: str = "open"
    allow: tuple[str, ...] = ()
    # Where the policy came from: "settings", "environment" or "default".
    source: str = "default"
    _names: frozenset[str] = field(default=frozenset(), compare=False, repr=False)
    _suffixes: tuple[str, ...] = field(default=(), compare=False, repr=False)
    _networks: tuple[Network, ...] = field(default=(), compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"The network mode must be one of: {', '.join(MODES)}.")
        names, suffixes, networks = set(), [], []
        for entry in self.allow:
            kind, value = parse_entry(entry)
            if kind == "network":
                networks.append(value)
            elif kind == "suffix":
                suffixes.append(value)
            else:
                names.add(value)
        object.__setattr__(self, "_names", frozenset(names))
        object.__setattr__(self, "_suffixes", tuple(suffixes))
        object.__setattr__(self, "_networks", tuple(networks))

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "allow": list(self.allow), "source": self.source}

    def name_allowed(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        return host in self._names or any(
            host == s or host.endswith("." + s) for s in self._suffixes
        )

    def address_allowed(self, address: Address) -> bool:
        address = _plain(address)
        if any(address in network for network in self._networks):
            return True
        return self.mode == "public" and address.is_global

    def check(self, host: str, addresses: list[Address]) -> None:
        """Raise EgressBlocked unless ``host`` (resolving to ``addresses``) may be reached."""
        if self.mode == "open" or self.name_allowed(host):
            return
        for address in addresses:
            if not self.address_allowed(address):
                raise EgressBlocked(self._why(host, address))

    def _why(self, host: str, address: Address) -> str:
        shown = str(address) if host == str(address) else f"{host} ({address})"
        if self.mode == "allow-list":
            reason = "this server only reaches the sites on its allow-list"
        else:
            reason = (
                "it's an internal address, and this server only reaches internal addresses on "
                "its allow-list"
            )
        return (
            f"Easy Chain didn't connect to {shown}: {reason}. An Owner can add it under "
            "Settings → Network."
        )


def parse_entry(entry: str) -> tuple[str, Any]:
    """("network", ip_network) · ("suffix", "example.com") · ("name", "docs.example.com")."""
    text = entry.strip().lower()
    if not text:
        raise ValueError("An allow-list entry is empty.")
    try:
        return "network", ipaddress.ip_network(text, strict=False)
    except ValueError:
        pass
    if text.startswith("*."):
        text, kind = text[2:], "suffix"
    else:
        kind = "name"
    text = text.rstrip(".")  # a fully qualified name (docs.example.com.) is the same host
    if "/" in text or ":" in text or " " in text or not text or not all(text.split(".")):
        raise ValueError(
            f"“{entry}” isn't a host name (docs.example.com), a name ending (*.example.com), an "
            "address (10.1.2.3) or a network (10.0.0.0/8)."
        )
    return kind, text


def _plain(address: Address) -> Address:
    """An IPv4 address written as IPv6 (::ffff:10.0.0.1) is judged as the IPv4 one."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def resolve(host: str, port: int) -> list[Address]:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []  # the connection fails on its own, with the usual message
    return list(dict.fromkeys(ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos))


# ── the policy in force ──────────────────────────────────────────────────────

_policy: ContextVar[EgressPolicy | None] = ContextVar("easychain_egress", default=None)


def current() -> EgressPolicy | None:
    return _policy.get()


@contextlib.contextmanager
def using(policy: EgressPolicy | None) -> Iterator[None]:
    token = _policy.set(policy)
    try:
        yield
    finally:
        _policy.reset(token)


def use(policy: EgressPolicy | None) -> Any:
    return _policy.set(policy)


def reset(token: Any) -> None:
    _policy.reset(token)


def policy_from(saved: dict[str, Any] | None, signed_in: bool) -> EgressPolicy:
    """The policy: EASYCHAIN_EGRESS / EASYCHAIN_EGRESS_ALLOW when set, else what Settings say,
    else ``public`` with sign-in on and ``open`` with it off."""
    mode = os.environ.get("EASYCHAIN_EGRESS", "").strip().lower()
    if mode:
        allow = [a for a in os.environ.get("EASYCHAIN_EGRESS_ALLOW", "").split(",") if a.strip()]
        return EgressPolicy(mode, tuple(a.strip() for a in allow), "environment")
    saved = saved or {}
    if saved.get("mode") in MODES:
        try:
            return EgressPolicy(saved["mode"], tuple(saved.get("allow") or ()), "settings")
        except ValueError:
            pass  # a saved list that no longer checks out: the safe default below
    return EgressPolicy("public" if signed_in else "open", (), "default")


def check_url(url: str | httpx.URL, policy: EgressPolicy | None = None) -> None:
    """Raise EgressBlocked unless the policy in force (or ``policy``) lets ``url`` be reached."""
    policy = policy or current()
    if policy is None or policy.mode == "open":
        return
    parts = urlsplit(str(url))
    host = (parts.hostname or "").lower()
    if not host:
        return
    port = parts.port or (443 if parts.scheme in ("https", "wss") else 80)
    policy.check(host, resolve(host, port))


# ── connections ──────────────────────────────────────────────────────────────


class GuardedBackend(httpcore.NetworkBackend):
    """Checks the address actually connected to, and connects to exactly that address."""

    def __init__(self, inner: httpcore.NetworkBackend, policy: EgressPolicy):
        self.inner, self.policy = inner, policy

    def connect_tcp(self, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        return self.inner.connect_tcp(_pinned(self.policy, host, port), port, *args, **kwargs)

    def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.connect_unix_socket(*args, **kwargs)

    def sleep(self, seconds: float) -> None:
        self.inner.sleep(seconds)


class AsyncGuardedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend, policy: EgressPolicy):
        self.inner, self.policy = inner, policy

    async def connect_tcp(self, host: str, port: int, *args: Any, **kwargs: Any) -> Any:
        import anyio

        address = await anyio.to_thread.run_sync(_pinned, self.policy, host, port)
        return await self.inner.connect_tcp(address, port, *args, **kwargs)

    async def connect_unix_socket(self, *args: Any, **kwargs: Any) -> Any:
        return await self.inner.connect_unix_socket(*args, **kwargs)

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def _pinned(policy: EgressPolicy, host: str, port: int) -> str:
    """The address to connect to for ``host``: checked, so it can't change in between."""
    if policy.mode == "open":
        return host
    addresses = resolve(host, port)
    if not addresses:
        return host
    if policy.name_allowed(host):
        return str(addresses[0])
    for address in addresses:
        if policy.address_allowed(address):
            return str(address)
    raise EgressBlocked(policy._why(host, addresses[0]))


def _request_hook(policy: EgressPolicy) -> Any:
    def hook(request: httpx.Request) -> None:
        check_url(request.url, policy)

    return hook


def _async_request_hook(policy: EgressPolicy) -> Any:
    async def hook(request: httpx.Request) -> None:
        import anyio

        await anyio.to_thread.run_sync(check_url, request.url, policy)

    return hook


def client(policy: EgressPolicy | None = None, **kwargs: Any) -> httpx.Client:
    """An httpx Client that keeps to the policy (the one in force, if none is given)."""
    policy = policy or current()
    if policy is None or policy.mode == "open":
        return httpx.Client(**kwargs)
    transport = httpx.HTTPTransport(
        **{k: kwargs.pop(k) for k in ("verify", "cert", "trust_env", "retries") if k in kwargs}
    )
    pool = transport._pool  # httpx 0.28 (pinned): the backend is httpcore's
    pool._network_backend = GuardedBackend(pool._network_backend, policy)
    hooks = kwargs.pop("event_hooks", None) or {}
    hooks = {**hooks, "request": [_request_hook(policy), *hooks.get("request", [])]}
    return httpx.Client(transport=transport, event_hooks=hooks, **kwargs)


def async_client(policy: EgressPolicy | None = None, **kwargs: Any) -> httpx.AsyncClient:
    policy = policy or current()
    if policy is None or policy.mode == "open":
        return httpx.AsyncClient(**kwargs)
    transport = httpx.AsyncHTTPTransport(
        **{k: kwargs.pop(k) for k in ("verify", "cert", "trust_env", "retries") if k in kwargs}
    )
    pool = transport._pool
    pool._network_backend = AsyncGuardedBackend(pool._network_backend, policy)
    hooks = kwargs.pop("event_hooks", None) or {}
    hooks = {**hooks, "request": [_async_request_hook(policy), *hooks.get("request", [])]}
    return httpx.AsyncClient(transport=transport, event_hooks=hooks, **kwargs)


class GuardedHttpx:
    """Stands in for the ``httpx`` module inside flows run by Easy Chain (runtime/loader.py):
    ``httpx.request(…)`` keeps to the run's policy; everything else is httpx itself. Exported
    code uses plain httpx."""

    _CLIENT_ONLY = ("verify", "cert", "trust_env", "proxy", "timeout", "follow_redirects")

    def __getattr__(self, name: str) -> Any:
        return getattr(httpx, name)

    def request(self, method: str, url: Any, **kwargs: Any) -> httpx.Response:
        policy = current()
        if policy is None or policy.mode == "open":
            return httpx.request(method, url, **kwargs)
        options = {k: kwargs.pop(k) for k in self._CLIENT_ONLY if k in kwargs}
        with client(policy, **options) as c:
            return c.request(method, url, **kwargs)

    def get(self, url: Any, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: Any, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)


guarded_httpx = GuardedHttpx()
