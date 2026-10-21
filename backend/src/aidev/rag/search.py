"""Hybrid search over `doc_chunks` (docs/architecture.md §3.2).

Full-text search catches exact identifiers (`chat.postMessage`, `invoice.payment_failed`)
that embeddings blur; vector search catches paraphrases ("tell me when a card is
declined") that share no words with the docs. Their rankings are merged with Reciprocal
Rank Fusion, and a cross-encoder reranker orders the fused candidates.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from sqlalchemy import Select, Text, bindparam, select, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.rag.documents import DocType
from aidev.rag.embeddings import Embedder, Reranker
from aidev.rag.index import embedding_text
from aidev.rag.tables import doc_chunks

CANDIDATES = 50  # per retriever, and the most the reranker sees
RRF_K = 60  # standard constant from the RRF paper; damps the weight of top ranks
HNSW_EF_SEARCH = 100

# The query's lexemes are OR-ed: AND semantics (plainto/websearch_to_tsquery) match
# nothing for most natural-language questions. Lexemes come out of the 'english' config
# already stemmed, so they're matched verbatim with 'simple'. ts_rank normalization 1
# divides by log(document length), the closest built-in to BM25's length penalty.
_BM25_SQL = """
WITH q AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query
    FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS lexeme
)
SELECT c.id
FROM doc_chunks c, q
WHERE c.search_vector @@ q.query{filters}
ORDER BY ts_rank(c.search_vector, q.query, 1) DESC, c.id
LIMIT :limit
"""


class SearchMode(StrEnum):
    BM25 = "bm25"
    VECTOR = "vector"
    HYBRID = "hybrid"


@dataclass(frozen=True)
class SearchHit:
    chunk_id: int
    doc_key: str
    provider: str
    doc_type: str
    title: str
    url: str | None
    breadcrumb: str
    content: str
    score: float


@dataclass
class SearchTrace:
    """What each stage contributed, for the UI and for evaluation."""

    bm25_ids: list[int] = field(default_factory=list)
    vector_ids: list[int] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


class DocSearch:
    def __init__(
        self,
        engine: AsyncEngine,
        embedder: Embedder | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        self.engine = engine
        self.embedder = embedder
        self.reranker = reranker

    async def search(
        self,
        query: str,
        k: int = 8,
        providers: Sequence[str] | None = None,
        doc_types: Sequence[DocType] | None = None,
        mode: SearchMode = SearchMode.HYBRID,
        rerank: bool = True,
        trace: SearchTrace | None = None,
    ) -> list[SearchHit]:
        trace = trace if trace is not None else SearchTrace()
        if mode in (SearchMode.BM25, SearchMode.HYBRID):
            with _timer(trace, "bm25"):
                trace.bm25_ids = await self._bm25(query, providers, doc_types)
        if mode in (SearchMode.VECTOR, SearchMode.HYBRID):
            if self.embedder is None:
                raise ValueError(f"{mode} search needs an embedder")
            with _timer(trace, "vector"):
                trace.vector_ids = await self._vector(query, providers, doc_types)

        fused = rrf([trace.bm25_ids, trace.vector_ids])[:CANDIDATES]
        hits = await self._load(fused)
        if rerank and self.reranker is not None and hits:
            with _timer(trace, "rerank"):
                ranked = await self.reranker.rerank(
                    query, [embedding_text(h.breadcrumb, h.content) for h in hits], top_k=k
                )
            return [_with_score(hits[r.index], r.score) for r in ranked]
        return hits[:k]

    async def _bm25(
        self, query: str, providers: Sequence[str] | None, doc_types: Sequence[DocType] | None
    ) -> list[int]:
        filters = ""
        params: dict[str, object] = {"query": query, "limit": CANDIDATES}
        if providers:
            filters += " AND c.provider = ANY(:providers)"
            params["providers"] = list(providers)
        if doc_types:
            filters += " AND c.doc_type = ANY(:doc_types)"
            params["doc_types"] = list(doc_types)
        stmt = text(_BM25_SQL.format(filters=filters)).bindparams(
            *(
                bindparam(name, type_=ARRAY(Text))
                for name in ("providers", "doc_types")
                if name in params
            )
        )
        async with self.engine.connect() as conn:
            return list((await conn.execute(stmt, params)).scalars())

    async def _vector(
        self, query: str, providers: Sequence[str] | None, doc_types: Sequence[DocType] | None
    ) -> list[int]:
        assert self.embedder is not None
        vector = await self.embedder.embed_query(query)
        distance = doc_chunks.c.embedding.cosine_distance(vector)
        stmt = (
            _filtered(
                select(doc_chunks.c.id).where(doc_chunks.c.embedding_model == self.embedder.model),
                providers,
                doc_types,
            )
            .order_by(distance)
            .limit(CANDIDATES)
        )
        async with self.engine.begin() as conn:
            # Filters are applied after the HNSW scan; iterative scans keep searching
            # until enough rows pass them instead of returning too few.
            await conn.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))
            await conn.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            return list((await conn.execute(stmt)).scalars())

    async def _load(self, ids: list[int]) -> list[SearchHit]:
        if not ids:
            return []
        stmt = select(
            doc_chunks.c.id,
            doc_chunks.c.doc_key,
            doc_chunks.c.provider,
            doc_chunks.c.doc_type,
            doc_chunks.c.title,
            doc_chunks.c.url,
            doc_chunks.c.breadcrumb,
            doc_chunks.c.content,
        ).where(doc_chunks.c.id.in_(ids))
        async with self.engine.connect() as conn:
            rows = {r.id: r for r in await conn.execute(stmt)}
        n = len(ids)
        return [
            SearchHit(*rows[i], score=(n - rank) / n) for rank, i in enumerate(ids) if i in rows
        ]


def rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[int]:
    """Reciprocal Rank Fusion: each list contributes 1 / (k + rank) per item. Rank-based,
    so BM25 scores and cosine similarities never need to be put on one scale."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda item: (-scores[item], item))


def _filtered(
    stmt: Select[int],
    providers: Sequence[str] | None,
    doc_types: Sequence[DocType] | None,
) -> Select[int]:
    if providers:
        stmt = stmt.where(doc_chunks.c.provider.in_(providers))
    if doc_types:
        stmt = stmt.where(doc_chunks.c.doc_type.in_(doc_types))
    return stmt


def _with_score(hit: SearchHit, score: float) -> SearchHit:
    return SearchHit(**{**hit.__dict__, "score": score})


class _timer:
    def __init__(self, trace: SearchTrace, name: str) -> None:
        self.trace = trace
        self.name = name

    def __enter__(self) -> None:
        self.start = time.perf_counter()

    def __exit__(self, *_: object) -> None:
        self.trace.timings_ms[self.name] = (time.perf_counter() - self.start) * 1000
