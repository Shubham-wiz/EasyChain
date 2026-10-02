"""Embeddings that need no model or key: words hashed into a fixed-size vector.

Good enough to try a Knowledge Base out and to run tests offline (it finds
chunks that share words with the question). Real embedding models also match
meaning, so use one for real work. The class below is copied into exported code
when a Knowledge Base uses it, so it only needs the standard library.
"""

from __future__ import annotations

import hashlib
import math
import re

from langchain_core.embeddings import Embeddings


class KeywordEmbeddings(Embeddings):
    """Embeddings without a model: each word is hashed into one of `dims` slots."""

    STOPWORDS = frozenset(
        [
            "the",
            "and",
            "for",
            "are",
            "but",
            "not",
            "you",
            "all",
            "any",
            "can",
            "had",
            "her",
            "was",
            "one",
            "our",
            "out",
            "has",
            "his",
            "how",
            "its",
            "may",
            "new",
            "now",
            "old",
            "see",
            "two",
            "way",
            "who",
            "did",
            "get",
            "got",
            "let",
            "put",
            "say",
            "she",
            "too",
            "use",
            "this",
            "that",
            "with",
            "from",
            "have",
            "what",
            "when",
            "where",
            "which",
            "will",
            "your",
            "about",
            "would",
            "there",
            "their",
            "them",
            "they",
            "been",
            "into",
            "than",
            "then",
            "these",
            "those",
            "some",
            "such",
            "only",
            "also",
            "just",
            "does",
        ]
    )

    def __init__(self, dims: int = 256):
        self.dims = dims

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dims
        for word in re.findall(r"[a-z0-9]{3,}", text.lower()):
            if word in self.STOPWORDS:
                continue
            digest = hashlib.md5(word[:6].encode()).digest()
            slot = int.from_bytes(digest[:4], "little") % self.dims
            vector[slot] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
