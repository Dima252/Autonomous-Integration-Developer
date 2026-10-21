"""Offline stand-ins for the Voyage adapters."""

import hashlib
import math
import re
from collections.abc import Sequence

from aidev.rag.embeddings import EMBEDDING_DIMENSIONS, RerankResult


def bag_of_words(text: str) -> list[float]:
    """Hashed bag-of-words vector: texts sharing words have high cosine similarity,
    so vector search behaves meaningfully without a model."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        bucket = int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIMENSIONS
        vector[bucket] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeEmbedder:
    def __init__(self, model: str = "fake-embed") -> None:
        self.model = model
        self.document_calls: list[list[str]] = []
        self.queries: list[str] = []

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.document_calls.append(list(texts))
        return [bag_of_words(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return bag_of_words(text)


class ReversingReranker:
    """Reverses the candidate order, so tests can tell the reranker's output was used."""

    model = "fake-rerank"

    async def rerank(self, query: str, documents: Sequence[str], top_k: int) -> list[RerankResult]:
        n = len(documents)
        return [RerankResult(i, float(i) / n) for i in reversed(range(n))][:top_k]
