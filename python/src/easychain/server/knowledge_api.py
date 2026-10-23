"""The Knowledge Base API: bases, documents (files, pages, text), chunk previews and search.

Documents are read, split and embedded by a worker, from the job queue (``ingest`` jobs, one
Knowledge Base's documents one at a time); their status goes from queued to processing to
ready (or error, with a message for people). A file waits in the database until it has been
read, so a worker needs no disk shared with the API server.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from ..knowledge.ingest import build_embeddings
from ..knowledge.loaders import Loaded, LoadError, Section, load_bytes, load_url, split
from ..knowledge.search import search_knowledge
from ..knowledge.store import KnowledgeStore
from ..providers import EMBEDDING_MODELS, default_embedding_model
from ..runtime.errors import explain
from .audit import note

log = logging.getLogger("easychain.knowledge")

# Shown on documents that were being read by the API server itself (Easy Chain 0.4.0 and
# earlier) when it stopped: nothing will finish them.
CUT_OFF = "The server restarted while reading this. Remove it and add it again."

_ours: str | None = None


def use_database(url: str) -> None:
    """Point Knowledge Base search (in runs too) at this database, unless
    EASYCHAIN_KNOWLEDGE_URL was set by hand."""
    global _ours
    current = os.environ.get("EASYCHAIN_KNOWLEDGE_URL")
    if current is None or current == _ours:
        os.environ["EASYCHAIN_KNOWLEDGE_URL"] = url
        _ours = url


class KnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    embedding_model: str | None = None
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class UrlsRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=100)


class TextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(default=5, ge=1, le=50)
    mode: str = Field(default="hybrid", pattern="^(hybrid|meaning|words)$")


def add_knowledge_routes(app: FastAPI, home: Path) -> None:
    files_dir = home / "knowledge"

    def store() -> KnowledgeStore:
        return KnowledgeStore().setup()

    def _base_or_404(kb_id: str) -> dict[str, Any]:
        base = store().get_base(kb_id)
        if base is None:
            raise HTTPException(404, detail={"message": "That Knowledge Base doesn't exist."})
        return base

    def _with_label(base: dict[str, Any]) -> dict[str, Any]:
        info = EMBEDDING_MODELS.get(base["embedding_model"])
        return {**base, "embedding_label": info.label if info else base["embedding_model"]}

    async def _queue(
        kb_id: str,
        doc_id: str,
        source: dict[str, Any],
        data: bytes | None = None,
        name: str = "",
        content_type: str | None = None,
    ) -> None:
        hub = app.state.hub
        await hub.db.enqueue_ingest(kb_id, doc_id, source, data, name, content_type)
        hub.bus.job_added()

    @app.get("/api/knowledge")
    async def list_knowledge() -> list[dict[str, Any]]:
        return [_with_label(b) for b in await asyncio.to_thread(lambda: store().list_bases())]

    @app.post("/api/knowledge", status_code=201)
    async def create_knowledge(req: KnowledgeBaseRequest, request: Request) -> dict[str, Any]:
        model = req.embedding_model or default_embedding_model()
        if model != "keywords" and ":" not in model:
            raise HTTPException(
                422, detail={"message": f"“{model}” isn't an embedding model I recognise."}
            )
        base = await asyncio.to_thread(
            lambda: store().create_base(
                req.name.strip(),
                model,
                0,
                description=req.description,
                chunk_size=req.chunk_size,
                chunk_overlap=req.chunk_overlap,
            )
        )
        note(request, base["id"], name=base["name"])
        return {**_with_label(base), "documents": []}

    @app.get("/api/knowledge/{kb_id}")
    async def get_knowledge(kb_id: str) -> dict[str, Any]:
        base = await asyncio.to_thread(_base_or_404, kb_id)
        docs = await asyncio.to_thread(lambda: store().list_documents(kb_id))
        return {**_with_label(base), "documents": docs}

    @app.patch("/api/knowledge/{kb_id}")
    async def update_knowledge(kb_id: str, req: KnowledgeBaseUpdate) -> dict[str, Any]:
        await asyncio.to_thread(_base_or_404, kb_id)
        base = await asyncio.to_thread(
            lambda: store().update_base(kb_id, name=req.name, description=req.description)
        )
        return _with_label(base or {})

    @app.delete("/api/knowledge/{kb_id}")
    async def delete_knowledge(kb_id: str) -> dict[str, Any]:
        await asyncio.to_thread(_base_or_404, kb_id)
        await asyncio.to_thread(lambda: store().delete_base(kb_id))
        return {"deleted": kb_id}

    @app.post("/api/knowledge/{kb_id}/files", status_code=202)
    async def add_files(
        kb_id: str, files: Annotated[list[UploadFile], File()]
    ) -> list[dict[str, Any]]:
        await asyncio.to_thread(_base_or_404, kb_id)
        added = []
        for upload in files:
            name = Path(upload.filename or "upload").name
            data = await upload.read()
            content_type = upload.content_type
            doc = await asyncio.to_thread(
                lambda name=name: store().add_document(kb_id, name, name, "file")
            )
            folder = files_dir / kb_id
            folder.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
            (folder / f"{doc['id']}-{safe}").write_bytes(data)  # a copy of the original
            await _queue(kb_id, doc["id"], {"kind": "file"}, data, name, content_type)
            added.append(doc)
        return added

    @app.post("/api/knowledge/{kb_id}/urls", status_code=202)
    async def add_urls(kb_id: str, req: UrlsRequest) -> list[dict[str, Any]]:
        await asyncio.to_thread(_base_or_404, kb_id)
        added = []
        for url in req.urls:
            url = url.strip()
            doc = await asyncio.to_thread(
                lambda url=url: store().add_document(kb_id, url, url, "url")
            )
            await _queue(kb_id, doc["id"], {"kind": "url", "url": url})
            added.append(doc)
        return added

    @app.post("/api/knowledge/{kb_id}/text", status_code=202)
    async def add_text(kb_id: str, req: TextRequest) -> dict[str, Any]:
        await asyncio.to_thread(_base_or_404, kb_id)
        doc = await asyncio.to_thread(
            lambda: store().add_document(kb_id, req.title, req.title, "text")
        )
        await _queue(kb_id, doc["id"], {"kind": "text", "title": req.title, "text": req.text})
        return doc

    @app.get("/api/knowledge/{kb_id}/documents/{doc_id}/chunks")
    async def document_chunks(kb_id: str, doc_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(lambda: store().list_chunks(doc_id))

    @app.delete("/api/knowledge/{kb_id}/documents/{doc_id}")
    async def delete_document(kb_id: str, doc_id: str) -> dict[str, Any]:
        await asyncio.to_thread(lambda: store().delete_document(doc_id))
        return {"deleted": doc_id}

    @app.post("/api/knowledge/{kb_id}/search")
    async def search(kb_id: str, req: SearchRequest) -> dict[str, Any]:
        base = await asyncio.to_thread(_base_or_404, kb_id)

        def run() -> list[dict[str, Any]]:
            store()  # make sure the tables exist
            embeddings = build_embeddings(base["embedding_model"])
            return search_knowledge(kb_id, req.query, embeddings, top_k=req.top_k, mode=req.mode)

        try:
            hits = await asyncio.to_thread(run)
        except Exception as exc:
            info = explain(exc)
            raise HTTPException(
                400, detail={"message": info["message"], "hint": info.get("hint")}
            ) from exc
        return {"hits": hits}

    @app.post("/api/knowledge-preview")
    async def preview(
        file: Annotated[UploadFile | None, File()] = None,
        url: Annotated[str | None, Form()] = None,
        text: Annotated[str | None, Form()] = None,
        chunk_size: Annotated[int, Form()] = 1000,
        chunk_overlap: Annotated[int, Form()] = 150,
    ) -> dict[str, Any]:
        """How a document would be split, before adding it (nothing is stored)."""
        chunk_size = max(100, min(chunk_size, 8000))
        chunk_overlap = max(0, min(chunk_overlap, chunk_size // 2))
        try:
            if file is not None:
                name = Path(file.filename or "upload").name
                data = await file.read()
                loaded = await asyncio.to_thread(load_bytes, name, data, file.content_type)
                source = name
            elif url:
                loaded = await asyncio.to_thread(load_url, url)
                source = url
            elif text:
                loaded, source = Loaded("text", "Text", [Section(text)]), "text"
            else:
                raise HTTPException(422, detail={"message": "Send a file, a URL or some text."})
        except LoadError as exc:
            raise HTTPException(422, detail={"message": str(exc)}) from exc
        chunks = split(loaded, source, chunk_size, chunk_overlap)
        return {
            "title": loaded.title,
            "kind": loaded.kind,
            "count": len(chunks),
            "chars": sum(len(c.text) for c in chunks),
            "chunks": [
                {"position": c.position, "text": c.text, "page": c.page, "heading": c.heading}
                for c in chunks[:50]
            ],
        }
