"""Retrieval evaluation: run the golden set through each search configuration and report
Hit@k, Recall@k and MRR at the document level (what an agent actually sees)."""

import json
import statistics
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.rag.embeddings import Embedder
from aidev.rag.search import DocSearch, SearchMode, SearchTrace
from aidev.rag.tables import doc_chunks


@dataclass(frozen=True)
class EvalCase:
    query: str
    relevant: tuple[str, ...]
    providers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    name: str
    mode: SearchMode
    rerank: bool


CONFIGS = {
    c.name: c
    for c in (
        Config("bm25", SearchMode.BM25, rerank=False),
        Config("vector", SearchMode.VECTOR, rerank=False),
        Config("hybrid", SearchMode.HYBRID, rerank=False),
        Config("hybrid+rerank", SearchMode.HYBRID, rerank=True),
    )
}


@dataclass
class CaseResult:
    case: EvalCase
    retrieved: list[str]  # unique doc keys, in rank order

    def first_hit(self) -> int | None:
        return next(
            (rank for rank, key in enumerate(self.retrieved, 1) if key in self.case.relevant),
            None,
        )


@dataclass
class ConfigReport:
    config: Config
    k: int
    results: list[CaseResult] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)

    @property
    def hit_rate(self) -> float:
        return _mean(r.first_hit() is not None for r in self.results)

    @property
    def recall(self) -> float:
        return _mean(
            len(set(r.retrieved) & set(r.case.relevant)) / len(r.case.relevant)
            for r in self.results
        )

    @property
    def mrr(self) -> float:
        return _mean(1 / rank if (rank := r.first_hit()) else 0.0 for r in self.results)

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0.0

    def misses(self) -> list[CaseResult]:
        return [r for r in self.results if r.first_hit() is None]


def load_cases(path: Path) -> list[EvalCase]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [
        EvalCase(c["query"], tuple(c["relevant"]), tuple(c.get("providers", ())))
        for c in data["case"]
    ]


async def check_labels(engine: AsyncEngine, cases: Sequence[EvalCase]) -> list[str]:
    """Labels that point at documents the index doesn't contain (typos, renamed docs).
    An eval with broken labels silently under-reports, so it refuses to run."""
    keys = {k for c in cases for k in c.relevant}
    async with engine.connect() as conn:
        found = set(
            (
                await conn.execute(
                    select(doc_chunks.c.doc_key).where(doc_chunks.c.doc_key.in_(keys)).distinct()
                )
            ).scalars()
        )
    return sorted(keys - found)


async def evaluate(
    search: DocSearch, cases: Sequence[EvalCase], configs: Sequence[Config], k: int = 8
) -> list[ConfigReport]:
    reports = []
    for config in configs:
        report = ConfigReport(config, k)
        for case in cases:
            trace = SearchTrace()
            hits = await search.search(
                case.query,
                k=k,
                providers=list(case.providers) or None,
                mode=config.mode,
                rerank=config.rerank,
                trace=trace,
            )
            report.results.append(CaseResult(case, list(dict.fromkeys(h.doc_key for h in hits))))
            # Search time excluding the query-embedding call, which depends on the
            # network (and the free tier's pacing), not on the retrieval design.
            report.latencies_ms.append(sum(trace.timings_ms.values()))
        reports.append(report)
    return reports


def format_report(reports: Sequence[ConfigReport], cases: int) -> str:
    k = reports[0].k if reports else 8
    lines = [
        f"Retrieval eval: {cases} cases, top {k} chunks, document-level metrics",
        "",
        f"| Config | Hit@{k} | Recall@{k} | MRR | p50 search ms |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.config.name} | {r.hit_rate:.2f} | {r.recall:.2f} | {r.mrr:.2f} | {r.p50_ms:.0f} |"
        for r in reports
    ]
    for r in reports:
        misses = r.misses()
        if misses:
            lines += ["", f"Misses for {r.config.name} ({len(misses)}):"]
            lines += [f"- {m.case.query!r} -> got {m.retrieved[:3]}" for m in misses]
    return "\n".join(lines)


class CachedQueryEmbedder:
    """Caches query vectors on disk so repeated eval runs don't spend API requests
    (3 per minute on Voyage's free tier) re-embedding the same 50 queries."""

    def __init__(self, inner: Embedder, path: Path) -> None:
        self.inner = inner
        self.model = inner.model
        self.path = path
        self._cache: dict[str, list[float]] = (
            json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        )

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await self.inner.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        key = f"{self.model}\n{text}"
        if key not in self._cache:
            self._cache[key] = await self.inner.embed_query(text)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._cache), encoding="utf-8")
        return self._cache[key]


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0
