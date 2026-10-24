"""The workspace of the work in hand (Phase 5.6d): a context variable, set for each request
(server/spaces.py ``in_workspace``) and each job (the worker).

The run database stamps what it writes with it and lists only its rows (server/db.py), and
the runtime keeps each workspace's Knowledge Bases and long-term memory apart with it
(runtime/loader.py). server/spaces.py says what else belongs to a workspace.
"""

from __future__ import annotations

from contextvars import ContextVar

DEFAULT = "default"

workspace: ContextVar[str] = ContextVar("easychain_workspace", default=DEFAULT)


def current() -> str:
    return workspace.get()


# Who started the work in hand, as (user id, how they show up), for the runs it makes and the
# model calls they pay for (Phase 5.5): set for each request by ``in_workspace``. None: a
# trigger, a schedule or the server itself.
person: ContextVar[tuple[str | None, str] | None] = ContextVar("easychain_person", default=None)
