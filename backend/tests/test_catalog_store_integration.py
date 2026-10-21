import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx2
import pytest

from aidev.catalog.ingest import ingest
from aidev.catalog.sources import SpecSource
from aidev.catalog.store import CatalogStore
from aidev.config import get_settings
from aidev.infra import postgres
from tests.test_catalog_openapi import OPENAPI3

pytestmark = pytest.mark.integration


@pytest.fixture
async def store() -> AsyncIterator[CatalogStore]:
    engine = postgres.create_engine(get_settings().database_url)
    try:
        yield CatalogStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
async def provider(store: CatalogStore) -> AsyncIterator[str]:
    """A throwaway provider name, so tests never touch the real catalog."""
    name = f"test-{uuid4().hex[:8]}"
    yield name
    await store.delete_provider(name)


async def _ingest(store: CatalogStore, provider: str, doc: dict[str, Any], cache: Path) -> str:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, content=json.dumps(doc)))
    source = SpecSource(provider, "main", f"https://specs.test/{cache.name}.json")
    async with httpx2.AsyncClient(transport=transport) as client:
        (result,) = await ingest([source], store, cache, client)
    return result.status


async def test_ingest_then_exact_lookups(
    store: CatalogStore, provider: str, tmp_path: Path
) -> None:
    assert await _ingest(store, provider, OPENAPI3, tmp_path) == "ingested"

    op = await store.get_operation(provider, "getOrder")
    assert op is not None
    assert (op.method, op.path) == ("GET", "/orders/{order_id}")
    assert [p.name for p in op.parameters] == ["order_id", "expand"]

    event = await store.get_webhook_event(provider, "order/paid")
    assert event is not None
    assert event.payload_schema == {"$ref": "#/components/schemas/Order"}

    schema = await store.get_schema(provider, "Order")
    assert schema is not None
    assert schema.description == "A customer order"

    assert await store.get_operation(provider, "deleteOrder") is None
    assert await store.get_webhook_event(provider, "order/refunded") is None
    assert await store.get_operation("some-other-provider", "getOrder") is None


async def test_match_operation_grounds_concrete_requests(
    store: CatalogStore, provider: str, tmp_path: Path
) -> None:
    await _ingest(store, provider, OPENAPI3, tmp_path)

    async def match(method: str, path: str) -> str | None:
        op = await store.match_operation(provider, method, path)
        return op.op_id if op else None

    assert await match("get", "/orders/ord_42") == "getOrder"
    assert await match("POST", "/orders/ord_42") == "updateOrder"
    assert await match("POST", "/orders") == "POST /orders"
    # Invented endpoint and wrong method: nothing to ground against.
    assert await match("GET", "/orders/ord_42/refunds") is None
    assert await match("DELETE", "/orders/ord_42") is None


async def test_reingest_skips_unchanged_and_replaces_changed(
    store: CatalogStore, provider: str, tmp_path: Path
) -> None:
    assert await _ingest(store, provider, OPENAPI3, tmp_path / "v1") == "ingested"
    assert await _ingest(store, provider, OPENAPI3, tmp_path / "v1-again") == "unchanged"

    v2 = copy.deepcopy(OPENAPI3)
    del v2["paths"]["/orders/{order_id}"]["post"]
    assert await _ingest(store, provider, v2, tmp_path / "v2") == "ingested"

    assert await store.get_operation(provider, "updateOrder") is None
    assert await store.get_operation(provider, "getOrder") is not None
    (summary,) = [s for s in await store.summary() if s.provider == provider]
    assert (summary.operations, summary.webhook_events, summary.schemas) == (2, 1, 3)
