"""Embedding and reranking interfaces (ADR 005). Callers depend on these protocols, so a
local model can replace Voyage with a new adapter and a config change."""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

# Width of the `doc_chunks.embedding` column. Adapters must return vectors of this size.
EMBEDDING_DIMENSIONS = 1024


class EmbeddingServiceError(RuntimeError):
    """The embedding or reranking service failed after retries. Adapters raise this
    (not their HTTP library's errors) so callers can degrade without knowing the
    vendor."""


class Embedder(Protocol):
    model: str

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@dataclass(frozen=True)
class RerankResult:
    index: int  # position in the documents passed in
    score: float


class Reranker(Protocol):
    model: str

    async def rerank(
        self, query: str, documents: Sequence[str], top_k: int
    ) -> list[RerankResult]: ...


def estimate_tokens(text: str) -> int:
    """Deliberately high (3 chars per token) so pacing errs toward staying under limits.
    Exact counts would need the provider's tokenizer, a large download."""
    return len(text) // 3 + 1


class RateLimiter:
    """Paces requests to stay under requests-per-minute and tokens-per-minute limits,
    using a sliding 60-second window. Cheaper than discovering the limit through 429s,
    which still count against it."""

    WINDOW_S = 60.0

    def __init__(
        self,
        rpm: int,
        tpm: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.rpm = rpm
        self.tpm = tpm
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int) -> None:
        # A request bigger than the whole budget can never fit; let it through alone.
        tokens = min(tokens, self.tpm)
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and self._sent[0][0] <= now - self.WINDOW_S:
                    self._sent.popleft()
                used = sum(t for _, t in self._sent)
                if len(self._sent) < self.rpm and used + tokens <= self.tpm:
                    self._sent.append((now, tokens))
                    return
                await self._sleep(max(self._sent[0][0] + self.WINDOW_S - now, 0.01))
