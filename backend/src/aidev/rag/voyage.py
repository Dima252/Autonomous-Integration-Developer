"""Voyage AI adapters for the `Embedder` and `Reranker` protocols."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx2

from aidev.config import Settings
from aidev.llm.meter import Service, SpendMeter, UsageRecord
from aidev.llm.types import BudgetExceededError, Usage
from aidev.rag.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingServiceError,
    RateLimiter,
    RerankResult,
    estimate_tokens,
)

log = logging.getLogger(__name__)

BASE_URL = "https://api.voyageai.com/v1"
# Below every Voyage model's per-request token limit (the smallest is 120K).
MAX_REQUEST_TOKENS = 100_000
MAX_BATCH_TEXTS = 128
MAX_RETRIES = 6


class VoyageError(EmbeddingServiceError):
    pass


class VoyageClient:
    def __init__(
        self,
        api_key: str,
        http: httpx2.AsyncClient,
        limiter: RateLimiter,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        meter: SpendMeter | None = None,
    ) -> None:
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http
        self.limiter = limiter
        self._sleep = sleep
        self.meter = meter

    async def post(self, endpoint: str, payload: dict[str, Any], tokens: int) -> dict[str, Any]:
        """POST with client-side pacing, retrying rate limits and server errors."""
        if self.meter is not None:
            try:
                await self.meter.check(Service.VOYAGE)
            except BudgetExceededError as exc:
                # A VoyageError, so search degrades to BM25 instead of failing.
                raise VoyageError(str(exc)) from exc
        body = await self._post(endpoint, payload, tokens)
        if self.meter is not None:
            used = body.get("usage", {}).get("total_tokens", tokens)
            # Recorded at $0: Voyage usage is capped in tokens, the unit of its free
            # allowance (see Settings.voyage_monthly_token_cap).
            await self.meter.record(
                UsageRecord(
                    Service.VOYAGE, payload["model"], endpoint, Usage(input_tokens=used), 0.0
                )
            )
        return body

    async def _post(self, endpoint: str, payload: dict[str, Any], tokens: int) -> dict[str, Any]:
        for attempt in range(MAX_RETRIES + 1):
            await self.limiter.acquire(tokens)
            try:
                resp = await self._http.post(
                    f"{BASE_URL}/{endpoint}", json=payload, headers=self._headers, timeout=120.0
                )
            except httpx2.TransportError as exc:  # connection failures and timeouts
                if attempt == MAX_RETRIES:
                    raise VoyageError(f"{endpoint}: {exc!r} after {attempt} retries") from exc
                await self._backoff(endpoint, type(exc).__name__, attempt, None)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == MAX_RETRIES:
                    raise VoyageError(f"{endpoint}: {resp.status_code} after {attempt} retries")
                await self._backoff(endpoint, str(resp.status_code), attempt, _retry_after(resp))
                continue
            if resp.is_error:
                raise VoyageError(f"{endpoint}: {resp.status_code} {_detail(resp)}")
            return resp.json()
        raise AssertionError("unreachable")

    async def _backoff(
        self, endpoint: str, reason: str, attempt: int, retry_after: float | None
    ) -> None:
        delay = retry_after or min(2.0 ** (attempt + 1), 60.0)
        log.warning("voyage %s: %s, retrying in %.0fs", endpoint, reason, delay)
        await self._sleep(delay)


class VoyageEmbedder:
    def __init__(self, client: VoyageClient, model: str) -> None:
        self.client = client
        self.model = model

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in self._batches(texts):
            vectors.extend(await self._embed(batch, "document"))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        (vector,) = await self._embed([text], "query")
        return vector

    async def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": input_type,
            "output_dimension": EMBEDDING_DIMENSIONS,
        }
        body = await self.client.post("embeddings", payload, sum(estimate_tokens(t) for t in texts))
        data = sorted(body["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    def _batches(self, texts: Sequence[str]) -> list[list[str]]:
        # Requests must fit both the per-request cap and the per-minute token budget.
        budget = min(MAX_REQUEST_TOKENS, self.client.limiter.tpm)
        batches: list[list[str]] = []
        batch: list[str] = []
        used = 0
        for text in texts:
            tokens = estimate_tokens(text)
            if batch and (len(batch) == MAX_BATCH_TEXTS or used + tokens > budget):
                batches.append(batch)
                batch, used = [], 0
            batch.append(text)
            used += tokens
        if batch:
            batches.append(batch)
        return batches


class VoyageReranker:
    def __init__(self, client: VoyageClient, model: str) -> None:
        self.client = client
        self.model = model

    async def rerank(self, query: str, documents: Sequence[str], top_k: int) -> list[RerankResult]:
        if not documents:
            return []
        payload = {
            "query": query,
            "documents": list(documents),
            "model": self.model,
            "top_k": top_k,
        }
        # Voyage bills query tokens once per document.
        q = estimate_tokens(query)
        tokens = sum(q + estimate_tokens(d) for d in documents)
        body = await self.client.post("rerank", payload, tokens)
        return [RerankResult(r["index"], r["relevance_score"]) for r in body["data"]]


def create_voyage(
    settings: Settings, http: httpx2.AsyncClient, meter: SpendMeter | None = None
) -> tuple[VoyageEmbedder, VoyageReranker]:
    """Embedder and reranker sharing one rate limiter: the free-tier limits apply to
    the account, not to each endpoint."""
    if settings.voyage_api_key is None:
        raise VoyageError("VOYAGE_API_KEY is not set (see .env.example)")
    limiter = RateLimiter(settings.voyage_rpm, settings.voyage_tpm)
    client = VoyageClient(settings.voyage_api_key.get_secret_value(), http, limiter, meter=meter)
    return (
        VoyageEmbedder(client, settings.embedding_model),
        VoyageReranker(client, settings.rerank_model),
    )


def _retry_after(resp: httpx2.Response) -> float | None:
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None


def _detail(resp: httpx2.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))[:300]
    except ValueError:
        return resp.text[:300]
