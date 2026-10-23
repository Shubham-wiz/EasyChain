"""Adding documents to a Knowledge Base: read, split, embed, store."""

from __future__ import annotations

from typing import Any

from ..runtime.gateway import gateway_init_embeddings
from .loaders import Loaded, LoadError, split
from .store import KnowledgeStore

BATCH = 64


def embedding_kwargs(model: str) -> dict[str, Any]:
    """Settings Easy Chain always passes to an embedding model (and writes into exported code).

    OpenAI's client would otherwise count tokens with tiktoken, which downloads its tables on
    first use; chunks are already well under the model's limit.
    """
    if model.startswith(("openai:", "azure_openai:")):
        return {"check_embedding_ctx_length": False}
    return {}


def build_embeddings(model: str) -> Any:
    return gateway_init_embeddings(model, **embedding_kwargs(model))


def ingest(store: KnowledgeStore, kb_id: str, doc_id: str, loaded: Loaded, source: str) -> int:
    """Split, embed and store one document; returns the number of chunks."""
    base = store.get_base(kb_id)
    if base is None:
        raise LoadError("That Knowledge Base no longer exists.")
    store.set_document(doc_id, status="processing", title=loaded.title)
    chunks = split(loaded, source, base["chunk_size"], base["chunk_overlap"])
    if not chunks:
        raise LoadError("There was no text to add.")
    embeddings = build_embeddings(base["embedding_model"])
    vectors: list[list[float]] = []
    for start in range(0, len(chunks), BATCH):
        vectors += embeddings.embed_documents([c.text for c in chunks[start : start + BATCH]])
    store.ensure_dims(kb_id, len(vectors[0]))
    store.write_chunks(kb_id, doc_id, chunks, vectors)
    store.set_document(
        doc_id,
        status="ready",
        chunks=len(chunks),
        chars=sum(len(c.text) for c in chunks),
        error=None,
    )
    return len(chunks)


def read_document(
    store: KnowledgeStore, doc_id: str, job: dict[str, Any], data: bytes | None
) -> str:
    """A worker's job: read one queued document and add it (see ``Database.enqueue_ingest``).

    Problems end up on the document, in words people can act on, so the job itself always
    finishes. Returns the document's status: ``ready``, ``error``, or ``gone`` when it was
    removed before or while it was being read (any chunks written meanwhile are removed too).
    """
    from ..runtime.errors import explain
    from .loaders import Section, load_bytes, load_url

    doc = store.get_document(doc_id)
    if doc is None:
        return "gone"
    kind = job.get("kind")
    try:
        if kind == "file":
            if data is None:
                raise LoadError("The file was lost before it could be read. Add it again.")
            loaded = load_bytes(job.get("name") or doc["source"], data, job.get("content_type"))
        elif kind == "url":
            loaded = load_url(job["url"])
        else:
            loaded = Loaded("text", job.get("title") or doc["title"], [Section(job["text"])])
        ingest(store, job["kb_id"], doc_id, loaded, doc["source"])
        status = "ready"
    except LoadError as exc:
        store.set_document(doc_id, status="error", error=str(exc))
        status = "error"
    except Exception as exc:
        info = explain(exc)
        message = info["message"] + (f" {info['hint']}" if info.get("hint") else "")
        store.set_document(doc_id, status="error", error=message)
        status = "error"
    if store.get_document(doc_id) is None:
        store.delete_document(doc_id)  # removed while being read: drop what was written
        return "gone"
    return status
