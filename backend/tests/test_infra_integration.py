import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from aidev.api.main import create_app
from aidev.config import get_settings
from aidev.infra import postgres

pytestmark = pytest.mark.integration


def test_ready_when_services_are_up() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/ready")

    assert resp.status_code == 200
    assert resp.json() == {"postgres": "up", "redis": "up"}


async def test_pgvector_extension_is_installed() -> None:
    engine = postgres.create_engine(get_settings().database_url)
    try:
        async with engine.connect() as conn:
            version = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
    finally:
        await engine.dispose()

    assert version is not None, "pgvector missing - see infra/postgres/init"
