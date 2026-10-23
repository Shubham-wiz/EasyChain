"""On Postgres, other processes are woken at once (LISTEN/NOTIFY, server/listen.py): a run's
new events wake a follower in another process, a queued job wakes a waiting worker, and a
listener whose connection drops reconnects (polling covers the gap). Two Database objects stand
for two processes."""

from __future__ import annotations

import asyncio
import statistics
import time

import pytest
import sqlalchemy as sa

from easychain.server.db import Database
from easychain.server.hub import EventBus
from easychain.server.listen import Listener, listen

from . import pg


@pytest.fixture(scope="module")
def pg_base():
    if not pg.available():
        pytest.skip("Postgres isn't available here")
    with pg.server() as url:
        yield url


async def _pair(base: str) -> tuple[Database, Database, EventBus, Listener]:
    url = pg.new_database(base)
    writer, reader = await Database.connect(url), await Database.connect(url)
    bus = EventBus()
    listener = listen(url, bus)
    assert listener is not None
    await asyncio.wait_for(listener.listening.wait(), 10)
    return writer, reader, bus, listener


async def _close(*things: object) -> None:
    for thing in things:
        if isinstance(thing, Listener):
            await thing.aclose()
        elif isinstance(thing, Database):
            await thing.close()


async def _run(db: Database) -> str:
    return await db.create_run(
        flow_id="f", flow_name="F", version_id="v", thread_id="t", inputs={}, options={}
    )


async def test_events_from_another_process_wake_a_follower_at_once(pg_base: str):
    writer, reader, bus, listener = await _pair(pg_base)
    try:
        run_id = await _run(writer)
        delays = []
        for n in range(20):
            # A follower that would otherwise poll only every 5 seconds.
            waiting = asyncio.create_task(bus.wait(run_id, 5))
            await asyncio.sleep(0.02)
            start = time.perf_counter()
            await writer.add_events(run_id, [{"type": "progress", "n": n}])
            await waiting
            delays.append(time.perf_counter() - start)
            assert (await reader.events_after(run_id))[-1]["n"] == n, "it finds what woke it"
        median = statistics.median(delays)
        print(f"\nevent wake-up between processes: median {median * 1000:.1f} ms")
        assert median < 0.1 and max(delays) < 1.0
    finally:
        await _close(listener, writer, reader)


async def test_a_queued_job_wakes_a_waiting_worker_in_another_process(pg_base: str):
    writer, reader, bus, listener = await _pair(pg_base)
    try:
        bus.jobs.clear()
        start = time.perf_counter()
        await _run(writer)
        await asyncio.wait_for(bus.jobs.wait(), 1.0)
        assert time.perf_counter() - start < 1.0
    finally:
        await _close(listener, writer, reader)


async def test_a_listener_whose_connection_drops_comes_back(pg_base: str):
    writer, reader, bus, listener = await _pair(pg_base)
    try:
        async with writer.engine.begin() as conn:
            killed = await conn.execute(
                sa.text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE query LIKE 'LISTEN easychain_jobs%' AND pid <> pg_backend_pid()"
                )
            )
            assert killed.fetchall(), "the listener's connection was found"
        await asyncio.sleep(0.2)
        await asyncio.wait_for(listener.listening.wait(), 15)
        run_id = await _run(writer)
        waiting = asyncio.create_task(bus.wait(run_id, 5))
        await asyncio.sleep(0.02)
        start = time.perf_counter()
        await writer.add_events(run_id, [{"type": "progress"}])
        await waiting
        assert time.perf_counter() - start < 1.0
    finally:
        await _close(listener, writer, reader)


def test_sqlite_has_no_listener():
    assert listen("sqlite:///x.db", EventBus()) is None
