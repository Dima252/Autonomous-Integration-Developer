"""Docs index CLI.

uv run python -m aidev.rag index            # build the corpus and sync doc_chunks
uv run python -m aidev.rag embed            # embed chunks that have no vector yet
uv run python -m aidev.rag status
uv run python -m aidev.rag search "verify stripe webhook" --provider stripe
uv run python -m aidev.rag eval             # every config the index supports
"""

import argparse
import asyncio
import logging
import sys
import time

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.catalog.store import CatalogStore
from aidev.config import Settings, get_settings
from aidev.infra import postgres
from aidev.llm.meter import PostgresLedger, SpendMeter
from aidev.rag.corpus import build_corpus
from aidev.rag.documents import DOC_TYPES
from aidev.rag.eval import (
    CONFIGS,
    CachedQueryEmbedder,
    check_labels,
    evaluate,
    format_report,
    load_cases,
)
from aidev.rag.index import DocIndex
from aidev.rag.search import DocSearch, SearchMode
from aidev.rag.voyage import create_voyage


async def _index(settings: Settings, refresh: bool) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as http:
            docs = await build_corpus(
                CatalogStore(engine), settings.data_dir, settings.corpus_dir, http, refresh
            )
        stats = await DocIndex(engine).sync(docs, settings.embedding_model)
    finally:
        await engine.dispose()
    print(
        f"{stats.documents} documents, {stats.chunks} chunks: {stats.written} written, "
        f"{stats.unchanged} unchanged, {stats.removed} removed, "
        f"{stats.reused_embeddings} embeddings reused"
    )


async def _embed(settings: Settings, batch_size: int) -> None:
    engine = postgres.create_engine(settings.database_url)
    start = time.monotonic()

    def progress(done: int, total: int) -> None:
        rate = done / max(time.monotonic() - start, 1e-9)
        eta = (total - done) / rate if rate else 0
        print(f"  {done}/{total} chunks embedded, ~{eta / 60:.0f} min left", flush=True)

    try:
        async with httpx2.AsyncClient() as http:
            embedder, _ = create_voyage(settings, http, _meter(engine, settings))
            done = await DocIndex(engine).embed_pending(embedder, batch_size, progress)
    finally:
        await engine.dispose()
    print(f"embedded {done} chunks with {settings.embedding_model}")


async def _status(settings: Settings) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        rows = await DocIndex(engine).status()
    finally:
        await engine.dispose()
    if not rows:
        print("Index is empty. Run: python -m aidev.rag index")
    for r in rows:
        print(
            f"{r.provider:8} {r.doc_type:14} {r.documents:5} docs {r.chunks:5} chunks "
            f"{r.embedded:5} embedded"
        )


async def _search(settings: Settings, args: argparse.Namespace) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as http:
            embedder = reranker = None
            if args.mode != SearchMode.BM25 or args.rerank:
                embedder, reranker = create_voyage(settings, http, _meter(engine, settings))
            search = DocSearch(engine, embedder, reranker)
            hits = await search.search(
                args.query,
                k=args.k,
                providers=args.provider or None,
                doc_types=args.type or None,
                mode=SearchMode(args.mode),
                rerank=args.rerank,
            )
    finally:
        await engine.dispose()
    for i, h in enumerate(hits, 1):
        print(f"{i}. [{h.score:.3f}] {h.doc_key}\n   {h.breadcrumb}\n   {h.url or ''}".rstrip())


async def _eval(settings: Settings, args: argparse.Namespace) -> None:
    cases = load_cases(settings.corpus_dir / "eval" / "retrieval.toml")
    engine = postgres.create_engine(settings.database_url)
    try:
        broken = await check_labels(engine, cases)
        if broken:
            sys.exit(f"labels not in the index (run `index` first?): {broken}")
        statuses = await DocIndex(engine).status()
        embedded = sum(s.embedded for s in statuses)
        names = args.config or (list(CONFIGS) if embedded and settings.voyage_api_key else ["bm25"])
        if names == ["bm25"] and not args.config:
            print("No embeddings yet: evaluating bm25 only (run `embed` for the rest).\n")
        async with httpx2.AsyncClient() as http:
            embedder = reranker = None
            if any(CONFIGS[n].mode != SearchMode.BM25 for n in names):
                voyage_embedder, reranker = create_voyage(settings, http, _meter(engine, settings))
                cache = settings.data_dir / "cache" / "eval-query-embeddings.json"
                embedder = CachedQueryEmbedder(voyage_embedder, cache)
            search = DocSearch(engine, embedder, reranker)
            reports = await evaluate(search, cases, [CONFIGS[n] for n in names], k=args.k)
    finally:
        await engine.dispose()
    print(format_report(reports))


def _meter(engine: AsyncEngine, settings: Settings) -> SpendMeter:
    return SpendMeter.from_settings(PostgresLedger(engine), settings)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aidev.rag")
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="build the corpus and sync it into the index")
    index.add_argument("--refresh", action="store_true", help="re-download guide pages")

    embed = commands.add_parser("embed", help="embed chunks that have no vector yet")
    embed.add_argument("--batch-size", type=int, default=64)

    commands.add_parser("status", help="show what the index contains")

    search = commands.add_parser("search", help="query the index")
    search.add_argument("query")
    search.add_argument("--provider", action="append", help="repeatable")
    search.add_argument("--type", action="append", choices=DOC_TYPES, help="repeatable")
    search.add_argument("--mode", choices=[m.value for m in SearchMode], default="vector")
    search.add_argument("--no-rerank", dest="rerank", action="store_false")
    search.add_argument("-k", type=int, default=8)

    ev = commands.add_parser("eval", help="run the retrieval golden set")
    ev.add_argument("--config", action="append", choices=list(CONFIGS), help="repeatable")
    ev.add_argument("-k", type=int, default=8)

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    settings = get_settings()

    match args.command:
        case "index":
            asyncio.run(_index(settings, args.refresh))
        case "embed":
            asyncio.run(_embed(settings, args.batch_size))
        case "status":
            asyncio.run(_status(settings))
        case "search":
            asyncio.run(_search(settings, args))
        case "eval":
            asyncio.run(_eval(settings, args))


if __name__ == "__main__":
    main()
