"""Several processes starting at once against one Postgres set up the tables without racing."""

from __future__ import annotations

import asyncio

import pytest

from easychain.runtime.resources import open_resources
from easychain.server.db import Database, checkpoint_url

from . import pg


@pytest.fixture(scope="module")
def pg_base():
    if not pg.available():
        pytest.skip("Postgres isn't available here")
    with pg.server() as url:
        yield url


async def test_many_processes_can_start_at_once(pg_base: str):
    url = pg.new_database(pg_base)

    async def start() -> None:
        db = await Database.connect(url)
        res = await open_resources(checkpoint_url(url))
        await res.aclose()
        await db.close()

    await asyncio.gather(*(start() for _ in range(6)))
