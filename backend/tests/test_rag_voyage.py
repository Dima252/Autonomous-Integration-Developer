import json
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from aidev.config import Settings
from aidev.llm.meter import Service, SpendMeter
from aidev.rag.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingServiceError,
    RateLimiter,
    estimate_tokens,
)
from aidev.rag.voyage import (
    VoyageClient,
    VoyageEmbedder,
    VoyageError,
    VoyageReranker,
    create_voyage,
)
from tests.llm_fakes import MemoryLedger


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


async def test_client_retries_connection_errors_and_wraps_the_last_one() -> None:
    clock = FakeClock()
    attempts = 0

    def flaky(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx2.ConnectError("connection refused")
        return _embeddings_response(request)

    assert len(await VoyageEmbedder(_client(flaky, clock), "voyage-4").embed_query("q")) > 0
    assert attempts == 2

    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectTimeout("timed out")

    with pytest.raises(EmbeddingServiceError):  # vendor-neutral error type
        await VoyageEmbedder(_client(down, FakeClock()), "voyage-4").embed_query("q")


def _metered_client(ledger: MemoryLedger, token_cap: int) -> VoyageClient:
    ledger.now = datetime(2026, 10, 21, tzinfo=UTC)
    meter = SpendMeter(ledger, 1.0, token_cap, clock=lambda: datetime(2026, 10, 21, tzinfo=UTC))
    clock = FakeClock()
    client = _client(
        lambda r: httpx2.Response(
            200, json={**_embeddings_response(r).json(), "usage": {"total_tokens": 600}}
        ),
        clock,
    )
    client.meter = meter
    return client


async def test_metered_calls_record_the_reported_tokens() -> None:
    ledger = MemoryLedger()
    embedder = VoyageEmbedder(_metered_client(ledger, token_cap=10_000), "voyage-4")

    await embedder.embed_query("verify a stripe signature")

    ((_, record),) = ledger.records
    assert (record.service, record.operation, record.usage.input_tokens) == (
        Service.VOYAGE,
        "embeddings",
        600,
    )


async def test_voyage_cap_raises_an_embedding_error_so_search_degrades() -> None:
    ledger = MemoryLedger()
    embedder = VoyageEmbedder(_metered_client(ledger, token_cap=1000), "voyage-4")
    await embedder.embed_query("first")
    await embedder.embed_query("second")  # 1200 tokens now, over the cap

    with pytest.raises(EmbeddingServiceError, match="monthly Voyage cap"):
        await embedder.embed_query("third")
