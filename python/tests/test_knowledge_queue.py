"""Knowledge Base documents are read by workers from the job queue (Phase 5.8): they wait for a
worker and survive a restart, a worker needs no disk shared with the API server, a worker that
dies mid-document is replaced without doubling the chunks, a document removed before it is read
leaves nothing behind, and an unexpected problem is shown on the document, not retried for
ever."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from easychain.knowledge import search as search_module
from easychain.knowledge.store import KnowledgeStore
from easychain.server.app import create_app

NOTES = b"""# Shipping

Orders ship within two working days.

## Returns

Unopened items can be returned within 30 days of delivery.
"""


@pytest.fixture
def make_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("EASYCHAIN_KNOWLEDGE_URL", raising=False)
    url = f"sqlite:///{tmp_path / 'app.db'}"

    def make(*, worker: bool, home: str = "api") -> Any:
        # Each app gets its own home folder: a worker reads files from the database, not from
        # the API server's disk.
        return create_app(
            workspace=tmp_path / "flows",
            home=tmp_path / home,
            static_dir=tmp_path / "web",
            database_url=url,
            worker=worker,
            worker_options={"poll": 0.05, "schedules": False},
        )

    make.url = url  # type: ignore[attr-defined]
    yield make
    search_module.KNOWLEDGE_ENGINES.clear()


def _rows(url: str, sql: str) -> list[dict[str, Any]]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(sa.text(sql))]
    finally:
        engine.dispose()


def _docs(client: TestClient, kb_id: str) -> dict[str, dict[str, Any]]:
    # By source: a document's title becomes the one its file gives once it's read.
    return {d["source"]: d for d in client.get(f"/api/knowledge/{kb_id}").json()["documents"]}


def _wait(client: TestClient, kb_id: str, done: set[str]) -> dict[str, dict[str, Any]]:
    for _ in range(200):
        docs = _docs(client, kb_id)
        if docs and all(d["status"] in done for d in docs.values()):
            return docs
        time.sleep(0.05)
    raise AssertionError(f"documents didn't finish: {_docs(client, kb_id)}")


def test_documents_wait_for_a_worker_survive_a_restart_and_need_no_shared_disk(
    make_app, fake_server
):
    with TestClient(make_app(worker=False)) as api:
        kb = api.post("/api/knowledge", json={"name": "Shop", "chunk_size": 200}).json()
        api.post(
            f"/api/knowledge/{kb['id']}/files",
            files=[("files", ("notes.md", NOTES, "text/markdown"))],
        )
        api.post(
            f"/api/knowledge/{kb['id']}/urls", json={"urls": [f"{fake_server.url}/pages/bees"]}
        )
        api.post(f"/api/knowledge/{kb['id']}/text", json={"title": "Hours", "text": "9 to 5."})
        time.sleep(0.3)
        assert {d["status"] for d in _docs(api, kb["id"]).values()} == {"queued"}
    assert len(_rows(make_app.url, "SELECT id FROM uploads")) == 1

    # The API server restarts with no worker: the documents still wait, they aren't failed.
    with TestClient(make_app(worker=False)) as api:
        assert {d["status"] for d in _docs(api, kb["id"]).values()} == {"queued"}

    # A worker elsewhere (its own home folder) reads them all.
    with TestClient(make_app(worker=True, home="worker")) as app:
        docs = _wait(app, kb["id"], {"ready"})
        assert docs["notes.md"]["chunks"] >= 2 and docs["notes.md"]["title"] == "Shipping"
        assert docs[f"{fake_server.url}/pages/bees"]["title"] == "Honey bees"
        hits = app.post(f"/api/knowledge/{kb['id']}/search", json={"query": "returns"}).json()
        assert "30 days" in hits["hits"][0]["text"]
    assert _rows(make_app.url, "SELECT id FROM uploads") == [], "a read file isn't kept"
    jobs = _rows(make_app.url, "SELECT thread_id, status FROM jobs WHERE action = 'ingest'")
    assert {(j["thread_id"], j["status"]) for j in jobs} == {(f"kb:{kb['id']}", "done")}


def test_a_worker_that_dies_mid_document_is_replaced_without_doubling_chunks(make_app):
    with TestClient(make_app(worker=False)) as api:
        kb = api.post("/api/knowledge", json={"name": "Shop", "chunk_size": 200}).json()
        api.post(
            f"/api/knowledge/{kb['id']}/files",
            files=[("files", ("notes.md", NOTES, "text/markdown"))],
        )
        doc = next(iter(_docs(api, kb["id"]).values()))
    # A worker leased the job, wrote some chunks, and died: its lease has run out.
    engine = sa.create_engine(make_app.url)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE jobs SET status = 'leased', lease_owner = 'gone', attempts = 1,"
                " lease_expires = :past WHERE action = 'ingest'"
            ),
            {"past": time.time() - 60},
        )
    engine.dispose()
    store = KnowledgeStore().setup()
    store.set_document(doc["id"], status="processing")

    with TestClient(make_app(worker=True, home="worker")) as app:
        ready = _wait(app, kb["id"], {"ready", "error"})["notes.md"]
        assert ready["status"] == "ready"
        chunks = app.get(f"/api/knowledge/{kb['id']}/documents/{doc['id']}/chunks").json()
    positions = [c["position"] for c in chunks]
    assert len(positions) == ready["chunks"] == len(set(positions)), "no chunk twice"
    job = _rows(make_app.url, "SELECT status, attempts, lease_owner FROM jobs")[0]
    assert job["status"] == "done" and job["attempts"] == 2 and job["lease_owner"] != "gone"


def test_a_document_removed_before_it_is_read_leaves_nothing(make_app):
    with TestClient(make_app(worker=False)) as api:
        kb = api.post("/api/knowledge", json={"name": "Shop"}).json()
        api.post(
            f"/api/knowledge/{kb['id']}/files",
            files=[("files", ("notes.md", NOTES, "text/markdown"))],
        )
        doc = next(iter(_docs(api, kb["id"]).values()))
        assert api.delete(f"/api/knowledge/{kb['id']}/documents/{doc['id']}").status_code == 200
    with TestClient(make_app(worker=True, home="worker")) as app:
        for _ in range(100):
            if _rows(make_app.url, "SELECT id FROM jobs WHERE status = 'done'"):
                break
            time.sleep(0.05)
        assert _docs(app, kb["id"]) == {}
    assert _rows(make_app.url, "SELECT id FROM uploads") == []
    assert _rows(make_app.url, f"SELECT id FROM kb_chunks WHERE doc_id = '{doc['id']}'") == []


def test_an_unexpected_problem_shows_on_the_document_and_is_not_retried(
    make_app, monkeypatch: pytest.MonkeyPatch
):
    import easychain.knowledge.ingest as ingest_module

    def broken(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("the disk caught fire")

    monkeypatch.setattr(ingest_module, "read_document", broken)
    with TestClient(make_app(worker=True)) as app:
        kb = app.post("/api/knowledge", json={"name": "Shop"}).json()
        app.post(f"/api/knowledge/{kb['id']}/text", json={"title": "Hours", "text": "9 to 5."})
        doc = _wait(app, kb["id"], {"error"})["Hours"]
        assert "unexpected problem" in doc["error"] and "add it again" in doc["error"]
        time.sleep(0.3)
    jobs = _rows(make_app.url, "SELECT status, attempts FROM jobs WHERE action = 'ingest'")
    assert jobs == [{"status": "failed", "attempts": 1}]
