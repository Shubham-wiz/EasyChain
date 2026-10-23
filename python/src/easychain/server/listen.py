"""Instant wake-ups between processes on Postgres (LISTEN/NOTIFY).

A worker writes a run's events and the API server streams them to the browser; the API server
queues a job and a worker picks it up. In one process the EventBus wakes the waiting side at
once. Between processes, the waiting side polls the database (every 0.25 s for events, 0.5 s
for jobs). On Postgres, the writing side also sends a NOTIFY (``Database._wake``), and this
listener, one connection per process, turns it into the same EventBus wake-up. Polling stays
as the fallback: if the listener's connection drops, nothing is lost, only slower until it
reconnects. SQLite has no NOTIFY, so there it is polling only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from .db import EVENTS_CHANNEL, JOBS_CHANNEL

if TYPE_CHECKING:
    from .hub import EventBus

log = logging.getLogger("easychain.listen")


class Listener:
    def __init__(self, url: str, bus: EventBus):
        self.conninfo = url.replace("postgresql+psycopg://", "postgresql://", 1)
        self.bus = bus
        # Set while it is listening (the tests wait for it).
        self.listening = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> Listener:
        self._task = asyncio.create_task(self._run(), name="easychain-listen")
        return self

    async def _run(self) -> None:
        import psycopg

        delay = 1.0
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(
                    self.conninfo, autocommit=True
                ) as conn:
                    await conn.execute(f"LISTEN {EVENTS_CHANNEL}")
                    await conn.execute(f"LISTEN {JOBS_CHANNEL}")
                    self.listening.set()
                    delay = 1.0
                    async for note in conn.notifies():
                        if note.channel == EVENTS_CHANNEL:
                            self.bus.notify(note.payload)
                        else:
                            self.bus.job_added()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.listening.clear()
                log.warning(
                    "Lost the database connection that wakes this process (%s); polling until "
                    "it's back.",
                    exc,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)

    async def aclose(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None


def listen(url: str, bus: EventBus) -> Listener | None:
    """Start a listener for a Postgres database (None for SQLite)."""
    if not url.startswith(("postgresql", "postgres:")):
        return None
    return Listener(url, bus).start()
