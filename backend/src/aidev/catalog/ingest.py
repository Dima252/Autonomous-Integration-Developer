import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx2

from aidev.catalog.models import ParsedSpec
from aidev.catalog.openapi import parse_spec
from aidev.catalog.profiles import SpecProfile
from aidev.catalog.sources import SpecSource
from aidev.catalog.store import CatalogStore

log = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT_S = 120.0
# Part of the content hash, so changing how specs are parsed re-ingests unchanged specs.
# Bump it whenever the parser or a profile changes what gets stored.
CATALOG_FORMAT_VERSION = 2


@dataclass(frozen=True)
class IngestResult:
    source: SpecSource
    status: Literal["ingested", "unchanged"]
    operations: int = 0
    webhook_events: int = 0
    schemas: int = 0


async def ingest(
    sources: list[SpecSource],
    store: CatalogStore,
    cache_dir: Path,
    client: httpx2.AsyncClient,
) -> list[IngestResult]:
    """Download (or reuse the cached copy of) each spec and load it into the catalog.
    A spec whose content hash is already in the catalog is skipped."""
    results = []
    for source in sources:
        raw = await fetch_spec(source, cache_dir, client)
        content_hash = hashlib.sha256(b"v%d:" % CATALOG_FORMAT_VERSION + raw).hexdigest()
        if await store.has_spec(source.provider, source.name, content_hash):
            results.append(IngestResult(source, "unchanged"))
            continue
        # Parsing a 13 MB spec takes a moment; keep the event loop free meanwhile.
        spec = await asyncio.to_thread(_parse, raw, source.profile)
        await store.replace_spec(source.provider, source.name, source.url, content_hash, spec)
        results.append(
            IngestResult(
                source,
                "ingested",
                operations=len(spec.operations),
                webhook_events=len(spec.webhook_events),
                schemas=len(spec.schemas),
            )
        )
        log.info("ingested %s/%s", source.provider, source.name)
    return results


def _parse(raw: bytes, profile: SpecProfile) -> ParsedSpec:
    return parse_spec(json.loads(raw), profile)


async def fetch_spec(source: SpecSource, cache_dir: Path, client: httpx2.AsyncClient) -> bytes:
    """Source URLs are pinned to commits, so a cached download keyed by URL never goes
    stale. Changing the pin changes the key and triggers a fresh download."""
    url_key = hashlib.sha256(source.url.encode()).hexdigest()[:12]
    path = cache_dir / source.provider / f"{source.name}-{url_key}.json"
    if path.exists():
        return path.read_bytes()
    log.info("downloading %s", source.url)
    resp = await client.get(source.url, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return resp.content
