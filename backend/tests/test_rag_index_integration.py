from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.config import get_settings
from aidev.infra import postgres
from aidev.rag.chunking import Chunk
from aidev.rag.documents import DocType, SourceDoc, doc_key
from aidev.rag.index import DocIndex
from aidev.rag.search import DocSearch, SearchMode, SearchTrace
from aidev.rag.tables import doc_chunks
from tests.rag_fakes import FakeEmbedder, ReversingReranker

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = postgres.create_engine(get_settings().database_url)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def provider(engine: AsyncEngine) -> AsyncIterator[str]:
    """Throwaway provider: tests sync only within it, so the real index is untouched."""
    name = f"test-{uuid4().hex[:8]}"
    yield name
    async with engine.begin() as conn:
        await conn.execute(delete(doc_chunks).where(doc_chunks.c.provider == name))


def _doc(provider: str, name: str, *texts: str, doc_type: DocType = "guide") -> SourceDoc:
    return SourceDoc(
        key=doc_key(provider, doc_type, name),
        provider=provider,
        doc_type=doc_type,
        title=name.title(),
        url=f"https://docs.test/{name}",
        chunks=[Chunk(f"Test › {name}", t) for t in texts],
    )


def _corpus(provider: str) -> list[SourceDoc]:
    return [
        _doc(provider, "signatures", "Verify the webhook signature with an HMAC of the raw body."),
        _doc(provider, "pagination", "Use a cursor to page through list results."),
        _doc(
            provider,
            "create_customer",
            "POST /v1/customers creates a customer with an email address.",
            doc_type="operation",
        ),
        _doc(
            provider, "retries", "Retry 429 responses after the Retry-After delay.", "Use backoff."
        ),
    ]


async def _count(engine: AsyncEngine, provider: str, embedded: bool = False) -> int:
    query = select(func.count()).where(doc_chunks.c.provider == provider)
    if embedded:
        query = query.where(doc_chunks.c.embedding.is_not(None))
    async with engine.connect() as conn:
        return await conn.scalar(query) or 0


async def test_sync_is_incremental(engine: AsyncEngine, provider: str) -> None:
    index = DocIndex(engine)
    docs = _corpus(provider)

    first = await index.sync(docs, "fake-embed", scope=[provider])
    again = await index.sync(docs, "fake-embed", scope=[provider])

    assert (first.written, first.unchanged, first.chunks) == (4, 0, 5)
    assert (again.written, again.unchanged) == (0, 4)
    assert await _count(engine, provider) == 5

    changed = [*docs[:2], _doc(provider, "create_customer", "Changed text.", doc_type="operation")]
    third = await index.sync(changed, "fake-embed", scope=[provider])

    assert (third.written, third.unchanged, third.removed) == (1, 2, 1)  # retries removed
    assert await _count(engine, provider) == 3


async def test_sync_never_touches_providers_outside_its_scope(
    engine: AsyncEngine, provider: str
) -> None:
    other = f"{provider}-other"
    index = DocIndex(engine)
    try:
        await index.sync(_corpus(other), "fake-embed", scope=[other])
        await index.sync(_corpus(provider)[:1], "fake-embed", scope=[provider])

        assert await _count(engine, other) == 5
        with pytest.raises(ValueError, match="outside the sync scope"):
            await index.sync(_corpus(other), "fake-embed", scope=[provider])
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(doc_chunks).where(doc_chunks.c.provider == other))


async def test_embed_pending_batches_and_reuses_vectors(engine: AsyncEngine, provider: str) -> None:
    index = DocIndex(engine)
    embedder = FakeEmbedder()
    docs = _corpus(provider)
    await index.sync(docs, embedder.model, scope=[provider])

    done = await index.embed_pending(embedder, batch_size=2, scope=[provider])

    assert done == 5
    assert [len(call) for call in embedder.document_calls] == [2, 2, 1]
    assert await _count(engine, provider, embedded=True) == 5

    # A changed document keeps the vectors of chunks whose text didn't change.
    retries = _doc(
        provider,
        "retries",
        "Retry 429 responses after the Retry-After delay.",
        "Use backoff.",
        "New.",
    )
    stats = await index.sync([*docs[:3], retries], embedder.model, scope=[provider])

    assert (stats.written, stats.reused_embeddings) == (1, 2)
    assert await index.embed_pending(embedder, scope=[provider]) == 1


async def test_search_modes_filters_and_rerank(engine: AsyncEngine, provider: str) -> None:
    index = DocIndex(engine)
    embedder = FakeEmbedder()
    await index.sync(_corpus(provider), embedder.model, scope=[provider])
    await index.embed_pending(embedder, scope=[provider])
    search = DocSearch(engine, embedder, ReversingReranker())
    scoped = [provider]

    bm25 = await search.search("webhook signature", providers=scoped, mode=SearchMode.BM25)
    assert bm25[0].doc_key == doc_key(provider, "guide", "signatures")

    vector = await search.search(
        "verify signature raw body", providers=scoped, mode=SearchMode.VECTOR, rerank=False
    )
    assert vector[0].doc_key == doc_key(provider, "guide", "signatures")

    trace = SearchTrace()
    hybrid = await search.search("customer email", providers=scoped, rerank=False, k=3, trace=trace)
    assert hybrid[0].doc_key == doc_key(provider, "operation", "create_customer")
    assert trace.bm25_ids and trace.vector_ids
    assert {"bm25", "vector"} <= set(trace.timings_ms)

    ops_only = await search.search(
        "signature customer cursor", providers=scoped, doc_types=["operation"], rerank=False
    )
    assert {h.doc_type for h in ops_only} == {"operation"}

    plain = await search.search("cursor page results", providers=scoped, rerank=False)
    reranked = await search.search("cursor page results", providers=scoped, rerank=True)
    assert [h.chunk_id for h in reranked] == [h.chunk_id for h in reversed(plain)]

    assert await search.search("zzzz", providers=scoped, mode=SearchMode.BM25) == []


async def test_bm25_weighs_rare_terms_above_common_ones(engine: AsyncEngine, provider: str) -> None:
    # Many chunks say "webhook event"; one says "deduplicate". With IDF, the query's
    # rare word decides the ranking even though the common words match more often.
    common = [
        _doc(provider, f"common-{i}", "Webhook event webhook event webhook event payload.")
        for i in range(10)
    ]
    rare = _doc(provider, "dedupe", "Deduplicate a webhook by its ID.")
    await DocIndex(engine).sync([*common, rare], "fake-embed", scope=[provider])

    hits = await DocSearch(engine).search(
        "deduplicate webhook events", providers=[provider], mode=SearchMode.BM25
    )

    assert hits[0].doc_key == rare.key
