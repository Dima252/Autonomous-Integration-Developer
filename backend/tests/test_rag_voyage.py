import json
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from aidev.config import Settings
from aidev.rag.embeddings import EMBEDDING_DIMENSIONS, RateLimiter, estimate_tokens
from aidev.rag.voyage import (
    VoyageClient,
    VoyageEmbedder,
    VoyageError,
    VoyageReranker,
    create_voyage,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_rate_limiter_enforces_requests_per_minute() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=3, tpm=1_000_000, clock=clock, sleep=clock.sleep)

    for _ in range(4):
        await limiter.acquire(10)

    assert clock.now == pytest.approx(60.0)  # the fourth request waited for the window


async def test_rate_limiter_enforces_tokens_per_minute() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=100, tpm=10_000, clock=clock, sleep=clock.sleep)

    await limiter.acquire(6_000)
    await limiter.acquire(6_000)

    assert clock.now == pytest.approx(60.0)


async def test_rate_limiter_lets_an_oversized_request_through_alone() -> None:
    clock = FakeClock()
    limiter = RateLimiter(rpm=100, tpm=10_000, clock=clock, sleep=clock.sleep)

    await limiter.acquire(50_000)  # bigger than the budget: must not wait forever

    assert clock.now == 0.0


def _client(handler: Any, clock: FakeClock, tpm: int = 1_000_000) -> VoyageClient:
    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    limiter = RateLimiter(rpm=1000, tpm=tpm, clock=clock, sleep=clock.sleep)
    return VoyageClient("key", http, limiter, sleep=clock.sleep)


def _embeddings_response(request: httpx2.Request) -> httpx2.Response:
    body = json.loads(request.content)
    # Return out of order: the client must sort by index.
    data = [
        {"index": i, "embedding": [float(i)] * EMBEDDING_DIMENSIONS}
        for i in reversed(range(len(body["input"])))
    ]
    return httpx2.Response(200, json={"data": data})


async def test_client_retries_rate_limits_then_succeeds() -> None:
    clock = FakeClock()
    statuses = iter([429, 503, 200])

    def handler(request: httpx2.Request) -> httpx2.Response:
        status = next(statuses)
        if status != 200:
            return httpx2.Response(status, headers={"retry-after": "7"} if status == 429 else {})
        return _embeddings_response(request)

    embedder = VoyageEmbedder(_client(handler, clock), "voyage-4")
    vectors = await embedder.embed_documents(["a"])

    assert len(vectors) == 1
    assert clock.sleeps[0] == 7.0  # Retry-After honored
    assert clock.sleeps[1] == 4.0  # then exponential backoff


async def test_client_does_not_retry_client_errors() -> None:
    clock = FakeClock()
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(400, json={"detail": "bad model"})

    with pytest.raises(VoyageError, match="bad model"):
        await VoyageEmbedder(_client(handler, clock), "nope").embed_query("q")
    assert calls == 1


async def test_embedder_batches_by_token_budget_and_keeps_order() -> None:
    clock = FakeClock()
    requests: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        return _embeddings_response(request)

    texts = ["x" * 3000] * 5  # ~1,000 estimated tokens each
    embedder = VoyageEmbedder(_client(handler, clock, tpm=2_500), "voyage-4")
    vectors = await embedder.embed_documents(texts)

    assert [len(r["input"]) for r in requests] == [2, 2, 1]
    assert all(r["input_type"] == "document" for r in requests)
    assert all(r["output_dimension"] == EMBEDDING_DIMENSIONS for r in requests)
    assert [v[0] for v in vectors] == [0.0, 1.0, 0.0, 1.0, 0.0]


async def test_query_embedding_uses_query_input_type() -> None:
    clock = FakeClock()
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content)["input_type"])
        return _embeddings_response(request)

    await VoyageEmbedder(_client(handler, clock), "voyage-4").embed_query("hello")

    assert seen == ["query"]


async def test_reranker_parses_results_and_counts_tokens_per_document() -> None:
    clock = FakeClock()

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        assert body["top_k"] == 2
        return httpx2.Response(
            200,
            json={
                "data": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]
            },
        )

    client = _client(handler, clock)
    results = await VoyageReranker(client, "rerank-3").rerank("q", ["a", "b"], top_k=2)

    assert [(r.index, r.score) for r in results] == [(1, 0.9), (0, 0.2)]
    assert await VoyageReranker(client, "rerank-3").rerank("q", [], top_k=2) == []


def test_estimate_tokens_is_conservative() -> None:
    assert estimate_tokens("x" * 300) >= 100


async def test_create_voyage_requires_a_key() -> None:
    async with httpx2.AsyncClient() as http:
        with pytest.raises(VoyageError, match="VOYAGE_API_KEY"):
            create_voyage(Settings(voyage_api_key=None), http)
        embedder, reranker = create_voyage(
            Settings(voyage_api_key=SecretStr("k"), embedding_model="m", rerank_model="r"), http
        )

    assert (embedder.model, reranker.model) == ("m", "r")
    assert embedder.client is reranker.client  # one shared rate limiter
