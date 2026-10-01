"""Where runs keep their Save Points, side-effect records and step cache.

Three backends, chosen by the database URL:

- in memory (tests and ``easychain run``): lost when the process ends;
- SQLite (``sqlite:///path/to/easychain.db``): the local default for ``easychain dev``;
- Postgres (``postgresql://…``): for Docker Compose and production, shared by every worker.
"""

from __future__ import annotations

import asyncio
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
    """Open resources for a database URL (None or "memory" for in memory).

    ``python:package.module:factory`` plugs in another backend: ``factory(url)`` returns (or
    awaits to) a ``Resources`` with any LangGraph checkpointer, store and cache.
    """
    if not url or url == "memory":
        return memory_resources()
    if url.startswith("python:"):
        import importlib
        import inspect

        target = url.removeprefix("python:")
        module_name, _, attr = target.partition(":")
        factory = getattr(importlib.import_module(module_name), attr or "resources")
        made = factory(url)
        return await made if inspect.isawaitable(made) else made
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
            store = await stack.enter_async_context(AsyncPostgresStore.from_conn_string(conn))
            async with _setup_lock(conn):
                await _setup_with_retries(saver.setup)
                await _setup_with_retries(store.setup)
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


@contextlib.asynccontextmanager
async def _setup_lock(conn_str: str, key: int = 0x45415360) -> AsyncIterator[None]:
    """Let one process at a time create LangGraph's tables.

    Waiting polls pg_try_advisory_lock instead of blocking in pg_advisory_lock: the store
    builds an index CONCURRENTLY, which waits for every open transaction, so a waiter
    blocked inside one would deadlock with it.
    """
    import psycopg

    async with await psycopg.AsyncConnection.connect(conn_str, autocommit=True) as conn:
        for _ in range(600):
            row = await (await conn.execute("SELECT pg_try_advisory_lock(%s)", (key,))).fetchone()
            if row and row[0]:
                break
            await asyncio.sleep(0.1)
        try:
            yield
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (key,))


async def _setup_with_retries(setup: Any, attempts: int = 8) -> None:
    """Create LangGraph's tables (idempotent), trying again if another process collides."""
    import psycopg

    races = (
        psycopg.errors.UniqueViolation,
        psycopg.errors.DuplicateTable,
        psycopg.errors.DuplicateObject,
        psycopg.errors.DeadlockDetected,
    )
    for attempt in range(attempts):
        try:
            await setup()
            return
        except races:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(0.2 * (attempt + 1))
