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
