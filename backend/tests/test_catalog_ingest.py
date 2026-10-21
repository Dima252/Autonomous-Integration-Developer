from pathlib import Path

import httpx2
import pytest

from aidev.catalog.ingest import fetch_spec
from aidev.catalog.sources import SOURCES, SpecSource


async def test_fetch_spec_downloads_once_then_uses_the_cache(tmp_path: Path) -> None:
    requests: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        return httpx2.Response(200, content=b'{"openapi": "3.0.0"}')

    source = SpecSource("shop", "main", "https://specs.test/shop.json")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        first = await fetch_spec(source, tmp_path, client)
        second = await fetch_spec(source, tmp_path, client)

    assert first == second == b'{"openapi": "3.0.0"}'
    assert requests == ["https://specs.test/shop.json"]


async def test_failed_downloads_raise_and_are_not_cached(tmp_path: Path) -> None:
    statuses = iter([404, 200])
    transport = httpx2.MockTransport(lambda _: httpx2.Response(next(statuses), content=b"{}"))
    source = SpecSource("shop", "main", "https://specs.test/flaky.json")

    async with httpx2.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx2.HTTPStatusError):
            await fetch_spec(source, tmp_path, client)
        # The retry goes back to the network instead of reading a cached error page.
        assert await fetch_spec(source, tmp_path, client) == b"{}"


def test_sources_are_pinned_and_unique() -> None:
    keys = [(s.provider, s.name) for s in SOURCES]

    assert len(keys) == len(set(keys))
    assert {s.provider for s in SOURCES} == {"stripe", "github", "slack", "hubspot"}
    for s in SOURCES:
        # Pinned to a commit SHA, never a moving branch.
        assert "/main/" not in s.url and "/master/" not in s.url, s.url
