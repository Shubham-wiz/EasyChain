"""Where runs keep their Save Points, side-effect records and step cache.

Three backends, chosen by the database URL:

- in memory (tests and ``easychain run``): lost when the process ends;
- SQLite (``sqlite:///path/to/easychain.db``): the local default for ``easychain dev``;
- Postgres (``postgresql://…``): for Docker Compose and production, shared by every worker.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.cache.memory import InMemoryCache
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore


@dataclass
class Resources:
    """A checkpointer (Save Points), a store (side effects run once) and a cache (cached steps)."""

    checkpointer: Any
    store: Any
    cache: Any
    kind: str = "memory"
    # Anything that must stay open as long as the resources are used.
    _stack: contextlib.AsyncExitStack | None = field(default=None, repr=False)

    @property
    def durable(self) -> bool:
        return self.kind != "memory"

    async def aclose(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


_memory: Resources | None = None


def memory_resources() -> Resources:
    """The process-wide in-memory resources (Save Points last as long as the process)."""
    global _memory
    if _memory is None:
        _memory = Resources(InMemorySaver(), InMemoryStore(), InMemoryCache())
    return _memory


async def open_resources(url: str | None) -> Resources:
    """Open resources for a database URL (None or "memory" for in memory)."""
    if not url or url == "memory":
        return memory_resources()
    stack = contextlib.AsyncExitStack()
    try:
        if url.startswith("sqlite"):
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
            from langgraph.store.sqlite.aio import AsyncSqliteStore

            path = sqlite_file(url)
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(path))
            await saver.setup()
            store = await stack.enter_async_context(AsyncSqliteStore.from_conn_string(path))
            await store.setup()
            return Resources(saver, store, InMemoryCache(), "sqlite", stack)
        if url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://")):
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres.aio import AsyncPostgresStore

            conn = libpq_url(url)
            saver = await stack.enter_async_context(AsyncPostgresSaver.from_conn_string(conn))
            await saver.setup()
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(conn))
            await store.setup()
            return Resources(saver, store, InMemoryCache(), "postgres", stack)
    except BaseException:
        await stack.aclose()
        raise
    await stack.aclose()
    raise ValueError(f"Unsupported database URL: {url}")


@contextlib.asynccontextmanager
async def resources_for(url: str | None) -> AsyncIterator[Resources]:
    res = await open_resources(url)
    try:
        yield res
    finally:
        await res.aclose()


def sqlite_file(url: str) -> str:
    """The file path in a sqlite:/// URL (sqlite:////abs/path or sqlite:///relative/path)."""
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


def libpq_url(url: str) -> str:
    """A postgres URL psycopg understands (drops SQLAlchemy's +driver part)."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1).replace(
        "postgres://", "postgresql://", 1
    )
