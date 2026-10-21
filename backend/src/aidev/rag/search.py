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

# Okapi BM25 in plain SQL. Postgres' built-in ts_rank has no IDF, so words like
# "webhook" that appear in thousands of chunks swamp the rare words that matter; on
# the golden set true BM25 lifted Hit@8 from 0.70 to 0.76 and MRR from 0.54 to 0.65.
# - The query's lexemes are OR-ed: AND semantics (plainto/websearch_to_tsquery) match
#   nothing for most natural-language questions. They come out of the 'english' config
#   already stemmed, so they're matched verbatim with 'simple'.
# - Document frequencies are counted per query lexeme through the GIN index.
# - Term frequency counts breadcrumb occurrences (weight A) three times, a BM25F-style
#   field boost: a page or section *about* a term beats one that mentions it.
# - Document length is the distinct-lexeme count, which Postgres keeps in the tsvector;
#   exact token counts cost 4x the latency for a one-case difference on the golden set.
BM25_K1 = 1.2
BM25_B = 0.75
BREADCRUMB_BOOST = 2

_BM25_SQL = f"""
WITH q AS (
    SELECT DISTINCT lexeme
    FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS lexeme
),
tsq AS (
    SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query FROM q
),
stats AS (
    SELECT count(*)::float AS n, avg(length(search_vector))::float AS avg_len FROM doc_chunks
),
idf AS (
    SELECT q.lexeme, ln(1 + (stats.n - df.n + 0.5) / (df.n + 0.5)) AS idf
    FROM q, stats, LATERAL (
        SELECT count(*)::float AS n FROM doc_chunks
        WHERE search_vector @@ to_tsquery('simple', quote_literal(q.lexeme))
    ) df
),
terms AS (
    SELECT c.id, length(c.search_vector)::float AS len, idf.idf,
           cardinality(u.positions)
             + {BREADCRUMB_BOOST} * (SELECT count(*) FROM unnest(u.weights) w WHERE w = 'A')
             AS tf
    FROM doc_chunks c
    CROSS JOIN tsq
    CROSS JOIN LATERAL unnest(c.search_vector) u
    JOIN idf ON idf.lexeme = u.lexeme
    WHERE c.search_vector @@ tsq.query{{filters}}
)
SELECT t.id
FROM terms t, stats
GROUP BY t.id, t.len, stats.avg_len
ORDER BY sum(
    t.idf * t.tf * ({BM25_K1} + 1)
    / (t.tf + {BM25_K1} * (1 - {BM25_B} + {BM25_B} * t.len / stats.avg_len))
) DESC, t.id
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
