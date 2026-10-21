"""Catalog CLI.

uv run python -m aidev.catalog ingest            # every provider
uv run python -m aidev.catalog ingest stripe     # just one
uv run python -m aidev.catalog status
"""

import argparse
import asyncio
import logging

import httpx2

from aidev.catalog.ingest import ingest
from aidev.catalog.sources import PROVIDERS, SOURCES
from aidev.catalog.store import CatalogStore
from aidev.config import get_settings
from aidev.infra import postgres


async def _ingest(providers: list[str]) -> None:
    settings = get_settings()
    sources = [s for s in SOURCES if not providers or s.provider in providers]
    engine = postgres.create_engine(settings.database_url)
    try:
        async with httpx2.AsyncClient() as client:
            results = await ingest(
                sources, CatalogStore(engine), settings.data_dir / "specs", client
            )
    finally:
        await engine.dispose()
    for r in results:
        counts = (
            f"{r.operations} operations, {r.webhook_events} events, {r.schemas} schemas"
            if r.status == "ingested"
            else ""
        )
        print(f"{r.source.provider}/{r.source.name}: {r.status} {counts}".rstrip())


async def _status() -> None:
    engine = postgres.create_engine(get_settings().database_url)
    try:
        rows = await CatalogStore(engine).summary()
    finally:
        await engine.dispose()
    if not rows:
        print("Catalog is empty. Run: python -m aidev.catalog ingest")
    for r in rows:
        print(
            f"{r.provider}/{r.name} ({r.api_version}): {r.operations} operations, "
            f"{r.webhook_events} events, {r.schemas} schemas"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aidev.catalog")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest_cmd = commands.add_parser("ingest", help="download specs and load the catalog")
    ingest_cmd.add_argument("providers", nargs="*", help=f"any of: {', '.join(PROVIDERS)}")
    commands.add_parser("status", help="show what the catalog contains")
    args = parser.parse_args()
    unknown = set(getattr(args, "providers", [])) - set(PROVIDERS)
    if unknown:
        parser.error(f"unknown provider(s): {', '.join(sorted(unknown))}")

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx2").setLevel(logging.WARNING)  # one line per request is noise
    if args.command == "ingest":
        asyncio.run(_ingest(args.providers))
    else:
        asyncio.run(_status())


if __name__ == "__main__":
    main()
