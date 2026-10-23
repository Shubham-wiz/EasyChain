"""Secrets for the work in hand, without putting them in the process's environment
(Phase 5.6d).

Generated code, LangChain's model providers and Easy Chain's own helpers all read secrets with
``os.environ`` (``OPENAI_API_KEY``, ``{secret:NAME}``). One process runs work for several
workspaces, so the secrets can't simply be exported: workspace B would read workspace A's.

``install()`` replaces ``os.environ`` with a ``ScopedEnviron``. That reads a **layer** first:
the secrets of the workspace whose work is in hand, a context variable set around a request or
a run (``using``). Then it reads the real environment, where keys the server was started with
apply to every workspace. Writes go to the real environment.

Child processes started without an explicit ``env`` get only the real environment, never a
workspace's secrets. When no layer is set (the command line, and anything outside a request
or a run), the default workspace's secrets apply (``set_default``), as they always have.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator, Mapping, MutableMapping
from contextvars import ContextVar
from typing import Any

_layer: ContextVar[Mapping[str, str] | None] = ContextVar("easychain_secrets", default=None)
_default: Callable[[], Mapping[str, str]] | None = None
_EMPTY: Mapping[str, str] = {}


class ScopedEnviron(MutableMapping[str, str]):
    """``os.environ`` with the secrets in hand laid over it."""

    def __init__(self, base: MutableMapping[str, str]):
        self.base = base

    def _overlay(self) -> Mapping[str, str]:
        layer = _layer.get()
        if layer is not None:
            return layer
        return _default() if _default is not None else _EMPTY

    def __getitem__(self, key: str) -> str:
        overlay = self._overlay()
        if key in overlay:
            return overlay[key]
        return self.base[key]

    def __setitem__(self, key: str, value: str) -> None:
        self.base[key] = value

    def __delitem__(self, key: str) -> None:
        if key not in self.base and key in self._overlay():
            return  # a secret of the work in hand: it isn't the process's to remove
        del self.base[key]

    def __contains__(self, key: object) -> bool:
        return key in self._overlay() or key in self.base

    def __iter__(self) -> Iterator[str]:
        overlay = self._overlay()
        yield from overlay
        yield from (k for k in self.base if k not in overlay)

    def __len__(self) -> int:
        return len(set(self.base) | set(self._overlay()))

    def copy(self) -> dict[str, str]:
        return dict(self)

    def __repr__(self) -> str:
        return f"ScopedEnviron({len(self)} names)"

    # os._Environ's extras, for code that uses them
    def setdefault(self, key: str, default: str = "") -> str:  # type: ignore[override]
        if key not in self:
            self.base[key] = default
        return self[key]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.base, name)


def install() -> None:
    """Put a ScopedEnviron in place of ``os.environ`` (once per process)."""
    if not isinstance(os.environ, ScopedEnviron):
        # Deliberate: it wraps the real environment, which stays as it is underneath.
        os.environ = ScopedEnviron(os.environ)  # type: ignore[assignment]  # noqa: B003


def real() -> MutableMapping[str, str]:
    """The process's own environment, without any workspace's secrets."""
    return os.environ.base if isinstance(os.environ, ScopedEnviron) else os.environ


def set_default(source: Callable[[], Mapping[str, str]] | None) -> None:
    """The secrets used when no layer is set: the default workspace's."""
    global _default
    _default = source


def current() -> Mapping[str, str]:
    layer = _layer.get()
    if layer is not None:
        return layer
    return _default() if _default is not None else _EMPTY


def use(layer: Mapping[str, str] | None) -> Any:
    return _layer.set(layer)


def reset(token: Any) -> None:
    _layer.reset(token)


@contextlib.contextmanager
def using(layer: Mapping[str, str] | None) -> Iterator[None]:
    token = _layer.set(layer)
    try:
        yield
    finally:
        _layer.reset(token)
