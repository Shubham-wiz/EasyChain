"""Searching a Knowledge Base: by meaning (vectors) and by words (full text), merged.

These functions are the single implementation of search. Easy Chain calls them when
you try a search, and the compiler copies their source into generated code (see
``compiler.helpers``), so exported flows search the same way with no Easy Chain
runtime. They may only use the standard library, SQLAlchemy, numpy and
langchain-core, and the module-level names listed in ``HELPER_GLOBALS``.

Where the Knowledge Bases live: ``EASYCHAIN_KNOWLEDGE_URL``, else
``EASYCHAIN_DATABASE_URL``, else the default Easy Chain database.
"""

from __future__ import annotations

import os
import re
from typing import Any

import numpy as np
import sqlalchemy as sa

# Shared caches (copied into generated code with the functions).
KNOWLEDGE_ENGINES: dict[str, Any] = {}
KNOWLEDGE_VECTORS: dict[tuple[str, str], Any] = {}


def knowledge_engine() -> sa.Engine:
    """The database that holds the Knowledge Bases (a synchronous SQLAlchemy engine)."""
    url = os.environ.get("EASYCHAIN_KNOWLEDGE_URL") or os.environ.get("EASYCHAIN_DATABASE_URL")
    if not url:
        home = os.environ.get("EASYCHAIN_HOME") or os.path.expanduser("~/.easychain")
        url = f"sqlite:///{os.path.join(home, 'easychain.db')}"
    if url not in KNOWLEDGE_ENGINES:
        scheme, _, rest = url.partition("://")
        if scheme.split("+")[0] in ("postgresql", "postgres"):
            engine = sa.create_engine(f"postgresql+psycopg://{rest}", pool_pre_ping=True)
        else:
            engine = sa.create_engine(f"sqlite://{rest}", connect_args={"timeout": 30})
        KNOWLEDGE_ENGINES[url] = engine
    return KNOWLEDGE_ENGINES[url]


def knowledge_words(query: str) -> list[str]:
    """The words of a question, for full-text search."""
    return [w for w in re.findall(r"[A-Za-z0-9]+", query.lower()) if len(w) > 1][:30]


def knowledge_by_meaning(conn: Any, base: str, vector: list[float], limit: int) -> list[str]:
    """Chunk ids whose embeddings are closest to the question's (cosine)."""
    if conn.dialect.name == "postgresql":
        kind = conn.execute(
            sa.text(
                "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'kb_chunks'::regclass AND attname = 'embedding'"
            )
        ).scalar()
        if kind and kind.startswith("vector"):
            dims = len(vector)
            sql = (
                f"SELECT id FROM kb_chunks WHERE kb_id = :kb AND embedding IS NOT NULL "
                f"ORDER BY (embedding::vector({dims})) <=> CAST(:q AS vector({dims})) LIMIT :n"
            )
            q = "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
            return [r[0] for r in conn.execute(sa.text(sql), {"kb": base, "q": q, "n": limit})]
    # Without pgvector: compare against every chunk with numpy (fine for thousands of chunks).
    stamp = conn.execute(
        sa.text("SELECT updated_at FROM kb_bases WHERE id = :kb"), {"kb": base}
    ).scalar()
    key = (str(conn.engine.url), base)
    cached = KNOWLEDGE_VECTORS.get(key)
    if cached is None or cached[0] != stamp:
        rows = conn.execute(
            sa.text(
                "SELECT id, embedding FROM kb_chunks WHERE kb_id = :kb AND embedding IS NOT NULL"
            ),
            {"kb": base},
        ).all()
        ids = [r[0] for r in rows]
        matrix = (
            np.vstack([np.frombuffer(bytes(r[1]), dtype=np.float32) for r in rows])
            if rows
            else np.zeros((0, len(vector)), dtype=np.float32)
        )
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        matrix = matrix / np.where(norms == 0, 1, norms)
        cached = KNOWLEDGE_VECTORS[key] = (stamp, ids, matrix)
    _, ids, matrix = cached
    if not ids or matrix.shape[1] != len(vector):
        return []
    q = np.asarray(vector, dtype=np.float32)
    q = q / (np.linalg.norm(q) or 1.0)
    scores = matrix @ q
    order = np.argsort(-scores)[:limit]
    return [ids[i] for i in order]


def knowledge_by_words(conn: Any, base: str, query: str, limit: int) -> list[str]:
    """Chunk ids that share the most (and the rarest) words with the question."""
    words = knowledge_words(query)
    if not words:
        return []
    if conn.dialect.name == "postgresql":
        q = " | ".join(words)
        sql = (
            "SELECT id FROM kb_chunks WHERE kb_id = :kb AND tsv @@ to_tsquery('english', :q) "
            "ORDER BY ts_rank_cd(tsv, to_tsquery('english', :q)) DESC LIMIT :n"
        )
        return [r[0] for r in conn.execute(sa.text(sql), {"kb": base, "q": q, "n": limit})]
    match = " OR ".join(f'"{w}"' for w in words)
    sql = (
        "SELECT chunk_id FROM kb_chunks_fts WHERE kb_chunks_fts MATCH :q AND kb_id = :kb "
        "ORDER BY bm25(kb_chunks_fts) LIMIT :n"
    )
    return [r[0] for r in conn.execute(sa.text(sql), {"kb": base, "q": match, "n": limit})]


def search_knowledge(
    base: str, query: str, embeddings: Any, top_k: int = 4, mode: str = "hybrid"
) -> list[dict[str, Any]]:
    """The passages of a Knowledge Base that best match a question, best first.

    mode: "hybrid" (meaning and words, merged by reciprocal rank fusion), "meaning"
    (embeddings only) or "words" (full-text search only).
    """
    if not query.strip():
        return []
    engine = knowledge_engine()
    pool = max(top_k * 4, 20)
    scores: dict[str, float] = {}
    with engine.connect() as conn:
        found: list[list[str]] = []
        if mode in ("hybrid", "meaning"):
            found.append(knowledge_by_meaning(conn, base, embeddings.embed_query(query), pool))
        if mode in ("hybrid", "words"):
            found.append(knowledge_by_words(conn, base, query, pool))
        for ids in found:
            for rank, chunk_id in enumerate(ids, start=1):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (60 + rank)
        best = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:top_k]
        if not best:
            return []
        rows = conn.execute(
            sa.text(
                "SELECT id, doc_id, text, title, source, page, heading FROM kb_chunks "
                "WHERE id IN :ids"
            ).bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": best},
        ).mappings()
        by_id = {row["id"]: dict(row) for row in rows}
    hits = [by_id[cid] | {"score": round(scores[cid], 5)} for cid in best if cid in by_id]
    return [{"n": n, **hit} for n, hit in enumerate(hits, start=1)]


def cite_passages(hits: list[dict[str, Any]]) -> str:
    """Passages numbered for citing: "[1] Title (source, page 3)" then the text."""
    blocks = []
    for hit in hits:
        where = ", ".join(
            part
            for part in (hit.get("source") or "", f"page {hit['page']}" if hit.get("page") else "")
            if part
        )
        title = hit.get("title") or "Untitled"
        heading = hit.get("heading")
        if heading and heading.lower() not in title.lower():
            title = f"{title} › {heading}"
        header = f"[{hit['n']}] {title}" + (f" ({where})" if where else "")
        blocks.append(f"{header}\n{hit['text'].strip()}")
    return "\n\n".join(blocks)


def rerank_passages(
    model: Any, query: str, hits: list[dict[str, Any]], top_k: int
) -> list[dict[str, Any]]:
    """Ask an AI model which passages answer the question best; keep those, best first."""
    if len(hits) <= 1:
        return hits[:top_k]
    listing = "\n\n".join(f"[{hit['n']}] {hit['text'][:800]}" for hit in hits)
    reply = model.invoke(
        [
            (
                "system",
                "Rank the passages by how well they answer the question. Reply with the "
                "passage numbers only, best first, separated by commas.",
            ),
            ("human", f"Question: {query}\n\nPassages:\n\n{listing}"),
        ]
    )
    by_n = {hit["n"]: hit for hit in hits}
    order = [int(n) for n in re.findall(r"\d+", reply.text)]
    picked = [by_n[n] for n in dict.fromkeys(order) if n in by_n]
    picked += [hit for hit in hits if hit not in picked]
    return [{**hit, "n": n} for n, hit in enumerate(picked[:top_k], start=1)]


# What the helper copies into generated code, in order.
HELPER_GLOBALS = ("KNOWLEDGE_ENGINES", "KNOWLEDGE_VECTORS")
HELPER_FUNCTIONS = (
    knowledge_engine,
    knowledge_words,
    knowledge_by_meaning,
    knowledge_by_words,
    search_knowledge,
)
