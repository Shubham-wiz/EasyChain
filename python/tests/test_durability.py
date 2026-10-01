"""Phase 2 "Done when": killing a worker mid-run and restarting resumes from the last step
with no duplicate side effects, and a paused approval can be resumed a day later.

Workers run as real processes (``easychain worker``) and are killed with SIGKILL. Each test
runs against SQLite and, when available, Postgres.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from easychain.runtime.resources import open_resources
from easychain.server.app import create_app
from easychain.server.db import Database, checkpoint_url
from easychain.server.hub import Hub
from easychain.server.store import FlowStore
from easychain.spec import dumps_spec, parse_spec

from . import pg

CHAOS = {
    "name": "Chaos",
    "steps": [
        {
            "id": "start",
            "type": "input",
            "settings": {"fields": [{"name": "base"}, {"name": "marker"}]},
        },
        {
            "id": "send_first",
            "type": "http_request",
            "settings": {
                "method": "POST",
                "url": "{base}/effects",
                "body": '{"message": "first"}',
                "response": "json",
                "save_as": "first",
            },
        },
        {
            "id": "slow",
            "type": "code",
            "settings": {
                "code": "import time\n\n"
                "def run(data):\n"
                "    with open(data['marker'], 'a') as f:\n"
                "        f.write('slow\\n')\n"
                "    time.sleep(4)\n"
                "    return {'waited': True}\n"
            },
        },
        {
            "id": "send_second",
            "type": "http_request",
            "settings": {
                "method": "POST",
                "url": "{base}/effects",
                "body": '{"message": "second"}',
                "response": "json",
                "save_as": "second",
            },
        },
        {"id": "finish", "type": "output", "settings": {"fields": ["first", "waited", "second"]}},
    ],
    "connections": [
        {"from": "start", "to": "send_first"},
        {"from": "send_first", "to": "slow"},
        {"from": "slow", "to": "send_second"},
        {"from": "send_second", "to": "finish"},
    ],
}

# The side effect happens, then the worker dies before the step can finish.
CRASH_INSIDE = {
    "name": "Crash inside",
    "steps": [
        {
            "id": "start",
            "type": "input",
            "settings": {"fields": [{"name": "base"}, {"name": "marker"}]},
        },
        {
            "id": "pay",
            "type": "code",
            "settings": {
                "side_effect": True,
                "code": "import time\n\nimport httpx\n\n"
                "def run(data):\n"
                "    sent = httpx.post(\n"
                "        data['base'] + '/effects',\n"
                "        json={'pay': 10},\n"
                "        headers={'Idempotency-Key': idempotency_key()},\n"
                "    )\n"
                "    with open(data['marker'], 'a') as f:\n"
                "        f.write('paid\\n')\n"
                "    time.sleep(4)\n"
                "    return {'receipt': sent.json()}\n",
            },
        },
        {"id": "finish", "type": "output", "settings": {"fields": ["receipt"]}},
    ],
    "connections": [{"from": "start", "to": "pay"}, {"from": "pay", "to": "finish"}],
}

APPROVAL = {
    "name": "Approval",
    "steps": [
        {"id": "start", "type": "input", "settings": {"fields": [{"name": "draft"}]}},
        {
            "id": "approve",
            "type": "ask_human",
            "settings": {
                "kind": "edit",
                "question": "Send “{draft}”?",
                "field": "draft",
                "show": ["draft"],
            },
        },
        {
            "id": "send",
            "type": "code",
            "settings": {"code": "def run(data):\n    return {'sent': 'SENT: ' + data['draft']}\n"},
        },
        {"id": "finish", "type": "output", "settings": {"fields": ["sent", "human_answer"]}},
    ],
    "connections": [
        {"from": "start", "to": "approve"},
        {"from": "approve", "to": "send", "exit": "Approved"},
        {"from": "approve", "to": "finish", "exit": "Rejected"},
        {"from": "send", "to": "finish"},
    ],
}


@pytest.fixture(scope="module")
def pg_base() -> Iterator[str | None]:
    if not pg.available():
        yield None
        return
    with pg.server() as url:
        yield url


@pytest.fixture(params=["sqlite", "postgres"])
def database_url(request: pytest.FixtureRequest, tmp_path: Path, pg_base: str | None) -> str:
    if request.param == "sqlite":
        return f"sqlite:///{tmp_path / 'easychain.db'}"
    if pg_base is None:
        pytest.skip("Postgres isn't available here")
    return pg.new_database(pg_base)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    folder = tmp_path / "flows"
    folder.mkdir()
    for flow_id, data in (("chaos", CHAOS), ("crash-inside", CRASH_INSIDE), ("approval", APPROVAL)):
        (folder / f"{flow_id}.flow.yaml").write_text(dumps_spec(parse_spec(data)))
    return folder


class Workers:
    """Starts ``easychain worker`` processes and kills them."""

    def __init__(self, url: str, home: Path, workspace: Path):
        self.url, self.home, self.workspace = url, home, workspace
        self.procs: list[subprocess.Popen[bytes]] = []

    def start(self) -> subprocess.Popen[bytes]:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "easychain.cli",
                "worker",
                "--database-url",
                self.url,
                "--home",
                str(self.home),
                "--workspace",
                str(self.workspace),
                "--lease",
                "2",
                "--poll",
                "0.1",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self.procs.append(proc)
        return proc

    def kill(self, proc: subprocess.Popen[bytes]) -> None:
        proc.send_signal(signal.SIGKILL)
        proc.wait(10)

    def stop_all(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(15)
                except subprocess.TimeoutExpired:
                    proc.kill()


@pytest.fixture
def workers(database_url: str, tmp_path: Path, workspace: Path) -> Iterator[Workers]:
    w = Workers(database_url, tmp_path / "home", workspace)
    yield w
    w.stop_all()


async def _hub(url: str, workspace: Path) -> Hub:
    db = await Database.connect(url)
    resources = await open_resources(checkpoint_url(url))
    return Hub(db, resources, FlowStore(workspace))


async def _close(hub: Hub) -> None:
    await hub.resources.aclose()
    await hub.db.close()


async def _wait_for(check: Any, timeout: float = 40, every: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = await check()
        if result:
            return result
        await asyncio.sleep(every)
    raise AssertionError("timed out waiting")


async def _events(hub: Hub, run_id: str) -> list[dict[str, Any]]:
    return await hub.db.events_after(run_id, 0, limit=100000, include_tokens=False)


def _effects(fake: Any) -> dict[str, Any]:
    return httpx.get(f"{fake.url}/effects").json()


async def test_killed_worker_resumes_from_the_last_step_without_repeating_side_effects(
    database_url: str, workspace: Path, workers: Workers, fake_server: Any, tmp_path: Path
):
    httpx.post(f"{fake_server.url}/effects/reset")
    marker = tmp_path / "marker.txt"
    hub = await _hub(database_url, workspace)
    try:
        first = workers.start()
        spec = hub.flows.get("chaos")
        run_id = await hub.start_run(
            spec, flow_id="chaos", inputs={"base": fake_server.url, "marker": str(marker)}
        )

        async def slow_started() -> bool:
            return marker.exists() and marker.read_text().count("slow") == 1

        await _wait_for(slow_started)
        events = await _events(hub, run_id)
        assert [e["step"] for e in events if e["type"] == "step_finished"] == [
            "start",
            "send_first",
        ]
        workers.kill(first)  # mid-run: send_first is done, slow is half-way
        assert len(_effects(fake_server)["applied"]) == 1

        workers.start()

        async def finished() -> dict[str, Any] | None:
            run = await hub.db.get_run(run_id)
            return run if run and run["status"] in ("ok", "error", "cancelled") else None

        run = await _wait_for(finished)
        assert run["status"] == "ok", run
        events = await _events(hub, run_id)
        started = [e["step"] for e in events if e["type"] == "step_started"]
        assert started.count("send_first") == 1, "a finished step never runs again"
        assert started.count("slow") == 2, "the interrupted step runs again"
        assert started.count("send_second") == 1
        assert marker.read_text().count("slow") == 2
        log = _effects(fake_server)
        assert [a["body"]["message"] for a in log["applied"]] == ["first", "second"]
        assert len(log["calls"]) == 2, "no request was sent twice"
        assert run["output"]["waited"] is True
        assert run["output"]["first"]["id"] != run["output"]["second"]["id"]
    finally:
        await _close(hub)


async def test_side_effect_interrupted_by_a_crash_is_not_applied_twice(
    database_url: str, workspace: Path, workers: Workers, fake_server: Any, tmp_path: Path
):
    httpx.post(f"{fake_server.url}/effects/reset")
    marker = tmp_path / "paid.txt"
    hub = await _hub(database_url, workspace)
    try:
        first = workers.start()
        run_id = await hub.start_run(
            hub.flows.get("crash-inside"),
            flow_id="crash-inside",
            inputs={"base": fake_server.url, "marker": str(marker)},
        )

        async def paid() -> bool:
            return marker.exists()

        await _wait_for(paid)
        workers.kill(first)  # the payment went out, but the step never finished
        workers.start()

        async def finished() -> dict[str, Any] | None:
            run = await hub.db.get_run(run_id)
            return run if run and run["status"] in ("ok", "error") else None

        run = await _wait_for(finished)
        assert run["status"] == "ok", run
        log = _effects(fake_server)
        assert len(log["calls"]) == 2, "the step ran again after the crash"
        assert len({c["key"] for c in log["calls"]}) == 1, "with the same Idempotency-Key"
        assert len(log["applied"]) == 1, "so the payment happened once"
        assert run["output"]["receipt"]["replayed"] is True
    finally:
        await _close(hub)


async def test_paused_approval_is_resumed_a_day_later(
    database_url: str, workspace: Path, workers: Workers, tmp_path: Path
):
    hub = await _hub(database_url, workspace)
    try:
        worker = workers.start()
        run_id = await hub.start_run(
            hub.flows.get("approval"), flow_id="approval", inputs={"draft": "Hello"}
        )

        async def paused() -> dict[str, Any] | None:
            run = await hub.db.get_run(run_id)
            return run if run and run["status"] == "paused" else None

        run = await _wait_for(paused)
        assert run["pending"]["reason"] == "ask_human"
        # Everything stops: the worker goes away and nothing is left in memory.
        worker.send_signal(signal.SIGTERM)
        worker.wait(15)
        # A day passes.
        day = 24 * 3600
        await hub.db.update_run(
            run_id, created_at=run["created_at"] - day, started_at=run["started_at"] - day
        )
        for item in await hub.db.list_inbox():
            from easychain.server.db import inbox

            async with hub.db.engine.begin() as conn:
                await conn.execute(
                    inbox.update()
                    .where(inbox.c.id == item["id"])
                    .values(created_at=item["created_at"] - day)
                )
    finally:
        await _close(hub)

    # A new API server (no worker inside) and a new worker process.
    app = create_app(
        home=tmp_path / "home",
        workspace=workspace,
        static_dir=tmp_path / "no-web",
        database_url=database_url,
        worker=False,
    )
    with TestClient(app) as client:
        [item] = client.get("/api/inbox").json()
        assert item["run_id"] == run_id
        assert time.time() - item["created_at"] > 23 * 3600
        assert item["request"]["question"] == "Send “Hello”?"
        answer = {"action": "approve", "value": "Hello again", "comment": "Looks good"}
        assert client.post(f"/api/inbox/{item['id']}/answer", json=answer).json()["answered"]
        assert client.post(f"/api/inbox/{item['id']}/answer", json=answer).status_code == 409
        workers.start()
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if run["status"] not in ("queued", "running", "paused"):
                break
            time.sleep(0.2)
        assert run["status"] == "ok", run
        assert run["output"] == {"sent": "SENT: Hello again", "human_answer": "Approved"}
        assert client.get("/api/inbox").json() == []
