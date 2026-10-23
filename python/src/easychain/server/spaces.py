"""Workspaces (Phase 5.6d, docs/workspaces.md): each has its own flows, runs, Inbox, triggers,
Knowledge Bases, skills, settings and secrets vault, and its own people with their roles.

A **Space** is where one workspace keeps its files:

- ``default`` keeps what Easy Chain always had: the flows folder and ``~/.easychain`` for the
  vault;
- any other workspace keeps everything under ``~/.easychain/workspaces/<id>/``.

Every vault is encrypted with the server's one key. Rows in the run database carry a
``workspace_id``.

The workspace of the work in hand is a context variable. ``enter`` sets it, together with
that workspace's secrets (scoped_env.py) and skills library:

- for every request, by the app-level ``in_workspace`` dependency, after ``authorize``;
- for every job, by the worker.
"""

from __future__ import annotations

import contextlib
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlette.requests import HTTPConnection

from .. import current_workspace as context
from .. import scoped_env
from ..skills import SkillLibrary, reset_library_here, use_library_here
from .auth import DEFAULT_WORKSPACE, principal_of
from .secrets import SecretStore
from .store import FlowStore

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
# Settings kept per workspace; the others (sandbox, network, single sign-on) are the server's.
WORKSPACE_SETTINGS = ("mcp", "notifications", "copilot")

_current = context.workspace


def current() -> str:
    """The workspace of the work in hand."""
    return context.current()


@dataclass
class Space:
    id: str
    flows: FlowStore
    vault: Any
    skills: SkillLibrary

    def setting(self, name: str) -> str:
        """The settings key of ``name`` for this workspace (the default keeps the plain one)."""
        if name not in WORKSPACE_SETTINGS or self.id == DEFAULT_WORKSPACE:
            return name
        return f"{name}@{self.id}"


class Spaces:
    def __init__(self, home: Path | None, flows: FlowStore, vault: Any):
        self.home = home
        self._lock = threading.Lock()
        self._spaces = {
            DEFAULT_WORKSPACE: Space(
                DEFAULT_WORKSPACE, flows, vault, SkillLibrary(flows.workspace / "skills")
            )
        }

    @property
    def default(self) -> Space:
        return self._spaces[DEFAULT_WORKSPACE]

    def get(self, workspace_id: str | None) -> Space:
        workspace_id = workspace_id or DEFAULT_WORKSPACE
        with self._lock:
            found = self._spaces.get(workspace_id)
            if found is not None:
                return found
            if not ID_RE.match(workspace_id) or self.home is None:
                raise KeyError(workspace_id)
            root = self.home / "workspaces" / workspace_id
            flows = FlowStore(root / "flows")
            vault = SecretStore(root, key_dir=self.home)
            space = Space(workspace_id, flows, vault, SkillLibrary(flows.workspace / "skills"))
            self._spaces[workspace_id] = space
            return space

    @contextlib.contextmanager
    def enter(self, workspace_id: str | None) -> Iterator[Space]:
        """Do the work inside ``with``, as that workspace's."""
        space = self.get(workspace_id)
        tokens = _enter(space)
        try:
            yield space
        finally:
            _leave(tokens)

    def enter_now(self, workspace_id: str | None) -> Space:
        """For a request: the rest of it is that workspace's (the context ends with it)."""
        space = self.get(workspace_id)
        _enter(space)
        return space


def _enter(space: Space) -> tuple[Any, Any, Any]:
    if hasattr(space.vault, "reload"):
        space.vault.reload()  # secrets saved by another process
    return (
        _current.set(space.id),
        scoped_env.use(getattr(space.vault, "mapping", None) or {}),
        use_library_here(space.skills),
    )


def _leave(tokens: tuple[Any, Any, Any]) -> None:
    workspace, layer, skills = tokens
    reset_library_here(skills)
    scoped_env.reset(layer)
    _current.reset(workspace)


async def in_workspace(connection: HTTPConnection) -> None:
    """Run for every route after ``authorize``: the request is its workspace's."""
    principal = principal_of(connection)
    workspace = principal.workspace if principal else DEFAULT_WORKSPACE
    spaces_of(connection).enter_now(workspace)


def spaces_of(connection: HTTPConnection) -> Spaces:
    return connection.app.state.spaces


def space_of(connection: HTTPConnection) -> Space:
    """The Space of a request (``in_workspace`` has entered it)."""
    return spaces_of(connection).get(current())
