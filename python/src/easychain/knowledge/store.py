"""Knowledge Base storage: bases, documents and their chunks, in the Easy Chain database.

Chunks keep their text, where they came from (title, source, page, heading) and an
embedding. On Postgres the embedding is a pgvector ``vector`` when the extension is
available (with an HNSW index per Knowledge Base, at half precision for more than
2,000 dimensions), and full-text search uses a
generated ``tsvector`` column. On SQLite embeddings are float32 blobs searched with
numpy, and full-text search uses FTS5. ``knowledge.search`` reads all of this.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import Engine, text

from .search import knowledge_engine, knowledge_vector_kind

log = logging.getLogger("easychain.knowledge")
_setup_lock = threading.Lock()
_ready: set[str] = set()


@dataclass
class ChunkDraft:
    text: str
    position: int
    title: str = ""
    source: str = ""
    page: int | None = None
    heading: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _now() -> float:
    return time.time()


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:40] or "knowledge"
    return s if s[0].isalpha() else f"kb_{s}"


def vector_index_sql(kb_id: str, dims: int) -> str | None:
    """The statement that makes one Knowledge Base's HNSW index on pgvector, or None.

    Vectors of up to 2,000 numbers are indexed as they are; up to 4,000 at half precision
    (halfvec), the most pgvector's HNSW index takes. Search orders by the same expression
    (``knowledge_vector_kind``), so the index is used. Bigger vectors get no index.
    """
    kind = knowledge_vector_kind(int(dims))
    if dims <= 0 or kind is None or not re.fullmatch(r"[a-z][a-z0-9_]*", kb_id):
        return None
    return (
        f"CREATE INDEX IF NOT EXISTS kb_hnsw_{kb_id} ON kb_chunks USING hnsw "
        f"((embedding::{kind}({int(dims)})) {kind}_cosine_ops) WHERE kb_id = '{kb_id}'"
    )


class KnowledgeStore:
    """Synchronous access to the Knowledge Base tables (run it in a thread from async code)."""

    def __init__(self, engine: Engine | None = None):
        self.engine = engine or knowledge_engine()
        self.dialect = self.engine.dialect.name
        self.pgvector = False

    # ── schema ───────────────────────────────────────────────────────────────

    def setup(self) -> KnowledgeStore:
        key = str(self.engine.url)
        with _setup_lock:
            if key in _ready:
                self.pgvector = self._has_pgvector()
                return self
            if self.dialect == "postgresql":
                self._setup_postgres()
            else:
                self._setup_sqlite()
            _ready.add(key)
        self.pgvector = self._has_pgvector()
        return self

    def _common_tables(self, conn: Any) -> None:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS kb_bases ("
                " id VARCHAR(64) PRIMARY KEY, name TEXT NOT NULL, description TEXT DEFAULT '',"
                " embedding_model TEXT NOT NULL, dims INTEGER NOT NULL,"
                " chunk_size INTEGER NOT NULL, chunk_overlap INTEGER NOT NULL,"
                " created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS kb_documents ("
                " id VARCHAR(64) PRIMARY KEY, kb_id VARCHAR(64) NOT NULL, title TEXT NOT NULL,"
                " source TEXT NOT NULL, kind VARCHAR(16) NOT NULL, status VARCHAR(16) NOT NULL,"
                " error TEXT, chunks INTEGER DEFAULT 0, chars INTEGER DEFAULT 0,"
                " created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL)"
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS kb_documents_kb ON kb_documents (kb_id)"))

    def _setup_sqlite(self) -> None:
        with self.engine.begin() as conn:
            self._common_tables(conn)
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS kb_chunks ("
                    " id VARCHAR(64) PRIMARY KEY, kb_id VARCHAR(64) NOT NULL,"
                    " doc_id VARCHAR(64) NOT NULL, position INTEGER NOT NULL, text TEXT NOT NULL,"
                    " title TEXT, source TEXT, page INTEGER, heading TEXT, meta TEXT,"
                    " embedding BLOB)"
                )
            )
            conn.execute(text("CREATE INDEX IF NOT EXISTS kb_chunks_kb ON kb_chunks (kb_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS kb_chunks_doc ON kb_chunks (doc_id)"))
            conn.execute(
                text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS kb_chunks_fts USING fts5("
                    "chunk_id UNINDEXED, kb_id UNINDEXED, text, tokenize='porter unicode61')"
                )
            )

    def _setup_postgres(self) -> None:
        with self.engine.begin() as conn:
            # One process creates the tables when several start at once.
            conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('easychain_knowledge'))"))
            vector = False
            try:
                with conn.begin_nested():
                    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                vector = True
            except Exception:
                vector = False  # no pgvector on this server: embeddings are stored as bytes
            self._common_tables(conn)
            exists = conn.execute(text("SELECT to_regclass('kb_chunks')")).scalar()
            if not exists:
                column = "vector" if vector else "bytea"
                conn.execute(
                    text(
                        "CREATE TABLE kb_chunks ("
                        " id VARCHAR(64) PRIMARY KEY, kb_id VARCHAR(64) NOT NULL,"
                        " doc_id VARCHAR(64) NOT NULL, position INTEGER NOT NULL,"
                        " text TEXT NOT NULL, title TEXT, source TEXT, page INTEGER,"
                        f" heading TEXT, meta TEXT, embedding {column},"
                        " tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED)"
                    )
                )
                conn.execute(text("CREATE INDEX kb_chunks_kb ON kb_chunks (kb_id)"))
                conn.execute(text("CREATE INDEX kb_chunks_doc ON kb_chunks (doc_id)"))
                conn.execute(text("CREATE INDEX kb_chunks_tsv ON kb_chunks USING gin (tsv)"))

    def _has_pgvector(self) -> bool:
        if self.dialect != "postgresql":
            return False
        with self.engine.connect() as conn:
            kind = conn.execute(
                text(
                    "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                    "WHERE attrelid = 'kb_chunks'::regclass AND attname = 'embedding'"
                )
            ).scalar()
        return bool(kind and kind.startswith("vector"))

    # ── bases ────────────────────────────────────────────────────────────────

    def list_bases(self) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT b.*, (SELECT COUNT(*) FROM kb_documents d WHERE d.kb_id = b.id) AS documents,"
                    " (SELECT COALESCE(SUM(d.chunks), 0) FROM kb_documents d WHERE d.kb_id = b.id)"
                    " AS chunks FROM kb_bases b ORDER BY b.updated_at DESC"
                )
            ).mappings()
            return [dict(r) for r in rows]

    def get_base(self, kb_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(text("SELECT * FROM kb_bases WHERE id = :id"), {"id": kb_id})
            found = row.mappings().first()
            return dict(found) if found else None

    def create_base(
        self,
        name: str,
        embedding_model: str,
        dims: int,
        *,
        description: str = "",
        chunk_size: int = 1000,
        chunk_overlap: int = 150,
        kb_id: str | None = None,
    ) -> dict[str, Any]:
        base_id = kb_id or slug(name)
        with self.engine.begin() as conn:
            taken = {
                r[0]
                for r in conn.execute(
                    text("SELECT id FROM kb_bases WHERE id LIKE :p"), {"p": f"{base_id}%"}
                )
            }
            if kb_id is None:
                candidate, n = base_id, 2
                while candidate in taken:
                    candidate, n = f"{base_id}_{n}", n + 1
                base_id = candidate
            elif base_id in taken:
                raise ValueError(f"A Knowledge Base called {base_id} already exists.")
            now = _now()
            conn.execute(
                text(
                    "INSERT INTO kb_bases (id, name, description, embedding_model, dims, chunk_size,"
                    " chunk_overlap, created_at, updated_at) VALUES (:id, :name, :description,"
                    " :model, :dims, :size, :overlap, :now, :now)"
                ),
                {
                    "id": base_id,
                    "name": name,
                    "description": description,
                    "model": embedding_model,
                    "dims": dims,
                    "size": chunk_size,
                    "overlap": chunk_overlap,
                    "now": now,
                },
            )
            if dims:
                self._vector_index(conn, base_id, dims)
        return self.get_base(base_id) or {}

    def _vector_index(self, conn: Any, kb_id: str, dims: int) -> None:
        """An HNSW index for one Knowledge Base's vectors (they all have the same size).

        The index only makes search faster, so a Knowledge Base works without it: when it
        can't be made (an older pgvector without halfvec, say), it is left out."""
        sql = vector_index_sql(kb_id, dims) if self.pgvector else None
        if sql is None:
            return
        try:
            with conn.begin_nested():
                conn.execute(text(sql))
        except Exception as exc:
            log.warning("No search index for the Knowledge Base %s (%s dims): %s", kb_id, dims, exc)

    def ensure_dims(self, kb_id: str, dims: int) -> None:
        """Record the embedding size on first use; refuse vectors of another size."""
        base = self.get_base(kb_id)
        if base is None:
            raise KeyError(kb_id)
        if base["dims"] == dims:
            return
        with self.engine.begin() as conn:
            count = conn.execute(
                text("SELECT COUNT(*) FROM kb_chunks WHERE kb_id = :id"), {"id": kb_id}
            ).scalar()
            if count:
                raise ValueError(
                    f"This Knowledge Base holds {base['dims']}-number embeddings, but "
                    f"{base['embedding_model']} gave {dims}. Did the embedding model change? "
                    "Make a new Knowledge Base for the new model."
                )
            conn.execute(
                text("UPDATE kb_bases SET dims = :dims WHERE id = :id"), {"dims": dims, "id": kb_id}
            )
            if self.pgvector and re.fullmatch(r"[a-z][a-z0-9_]*", kb_id):
                conn.execute(text(f"DROP INDEX IF EXISTS kb_hnsw_{kb_id}"))
            self._vector_index(conn, kb_id, dims)

    def update_base(self, kb_id: str, **changes: Any) -> dict[str, Any] | None:
        allowed = {
            k: v for k, v in changes.items() if k in ("name", "description") and v is not None
        }
        if allowed:
            sets = ", ".join(f"{k} = :{k}" for k in allowed)
            with self.engine.begin() as conn:
                conn.execute(
                    text(f"UPDATE kb_bases SET {sets}, updated_at = :now WHERE id = :id"),
                    {**allowed, "now": _now(), "id": kb_id},
                )
        return self.get_base(kb_id)

    def delete_base(self, kb_id: str) -> None:
        with self.engine.begin() as conn:
            if self.dialect == "sqlite":
                conn.execute(text("DELETE FROM kb_chunks_fts WHERE kb_id = :id"), {"id": kb_id})
            conn.execute(text("DELETE FROM kb_chunks WHERE kb_id = :id"), {"id": kb_id})
            conn.execute(text("DELETE FROM kb_documents WHERE kb_id = :id"), {"id": kb_id})
            conn.execute(text("DELETE FROM kb_bases WHERE id = :id"), {"id": kb_id})
            if self.pgvector and re.fullmatch(r"[a-z][a-z0-9_]*", kb_id):
                conn.execute(text(f"DROP INDEX IF EXISTS kb_hnsw_{kb_id}"))

    def _touch(self, conn: Any, kb_id: str) -> None:
        conn.execute(
            text("UPDATE kb_bases SET updated_at = :now WHERE id = :id"),
            {"now": _now(), "id": kb_id},
        )

    # ── documents ────────────────────────────────────────────────────────────

    def add_document(self, kb_id: str, title: str, source: str, kind: str) -> dict[str, Any]:
        doc = {
            "id": uuid.uuid4().hex,
            "kb_id": kb_id,
            "title": title[:500] or source,
            "source": source[:2000],
            "kind": kind,
            "status": "queued",
            "error": None,
            "chunks": 0,
            "chars": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO kb_documents (id, kb_id, title, source, kind, status, error, chunks,"
                    " chars, created_at, updated_at) VALUES (:id, :kb_id, :title, :source, :kind,"
                    " :status, :error, :chunks, :chars, :created_at, :updated_at)"
                ),
                doc,
            )
        return doc

    def set_document(self, doc_id: str, **changes: Any) -> None:
        allowed = {
            k: v for k, v in changes.items() if k in ("status", "error", "chunks", "chars", "title")
        }
        sets = ", ".join(f"{k} = :{k}" for k in allowed)
        with self.engine.begin() as conn:
            conn.execute(
                text(f"UPDATE kb_documents SET {sets}, updated_at = :now WHERE id = :id"),
                {**allowed, "now": _now(), "id": doc_id},
            )

    def fail_unfinished(self, message: str) -> int:
        """Mark documents that were still being read as failed (the server that read them
        stopped). Returns how many there were."""
        with self.engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE kb_documents SET status = 'error', error = :message, updated_at = :now"
                    " WHERE status IN ('queued', 'processing')"
                ),
                {"message": message, "now": _now()},
            )
            return result.rowcount or 0

    def list_documents(self, kb_id: str) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT * FROM kb_documents WHERE kb_id = :id ORDER BY created_at"),
                {"id": kb_id},
            ).mappings()
            return [dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict[str, Any] | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT * FROM kb_documents WHERE id = :id"), {"id": doc_id}
            ).mappings()
            found = row.first()
            return dict(found) if found else None

    def delete_document(self, doc_id: str) -> None:
        doc = self.get_document(doc_id)
        with self.engine.begin() as conn:
            if self.dialect == "sqlite":
                conn.execute(
                    text(
                        "DELETE FROM kb_chunks_fts WHERE chunk_id IN "
                        "(SELECT id FROM kb_chunks WHERE doc_id = :id)"
                    ),
                    {"id": doc_id},
                )
            conn.execute(text("DELETE FROM kb_chunks WHERE doc_id = :id"), {"id": doc_id})
            conn.execute(text("DELETE FROM kb_documents WHERE id = :id"), {"id": doc_id})
            if doc:
                self._touch(conn, doc["kb_id"])

    def write_chunks(
        self, kb_id: str, doc_id: str, chunks: list[ChunkDraft], vectors: list[list[float]]
    ) -> None:
        """Replace a document's chunks (with their embeddings)."""
        rows = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            rows.append(
                {
                    "id": uuid.uuid4().hex,
                    "kb_id": kb_id,
                    "doc_id": doc_id,
                    "position": chunk.position,
                    "text": chunk.text,
                    "title": chunk.title,
                    "source": chunk.source,
                    "page": chunk.page,
                    "heading": chunk.heading,
                    "meta": json.dumps(chunk.meta) if chunk.meta else None,
                    "embedding": self._vector_value(vector),
                }
            )
        embed = "CAST(:embedding AS vector)" if self.pgvector else ":embedding"
        with self.engine.begin() as conn:
            if self.dialect == "sqlite":
                conn.execute(
                    text(
                        "DELETE FROM kb_chunks_fts WHERE chunk_id IN "
                        "(SELECT id FROM kb_chunks WHERE doc_id = :id)"
                    ),
                    {"id": doc_id},
                )
            conn.execute(text("DELETE FROM kb_chunks WHERE doc_id = :id"), {"id": doc_id})
            if rows:
                conn.execute(
                    text(
                        "INSERT INTO kb_chunks (id, kb_id, doc_id, position, text, title, source,"
                        " page, heading, meta, embedding) VALUES (:id, :kb_id, :doc_id, :position,"
                        f" :text, :title, :source, :page, :heading, :meta, {embed})"
                    ),
                    rows,
                )
                if self.dialect == "sqlite":
                    conn.execute(
                        text(
                            "INSERT INTO kb_chunks_fts (chunk_id, kb_id, text) VALUES "
                            "(:id, :kb_id, :text)"
                        ),
                        rows,
                    )
            self._touch(conn, kb_id)

    def _vector_value(self, vector: list[float]) -> Any:
        if self.pgvector:
            return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
        return np.asarray(vector, dtype=np.float32).tobytes()

    def list_chunks(self, doc_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, position, text, title, source, page, heading FROM kb_chunks"
                    " WHERE doc_id = :id ORDER BY position LIMIT :n"
                ),
                {"id": doc_id, "n": limit},
            ).mappings()
            return [dict(r) for r in rows]
