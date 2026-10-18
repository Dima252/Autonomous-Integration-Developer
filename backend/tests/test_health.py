from fastapi.testclient import TestClient

from aidev.api.main import create_app
from aidev.config import Settings


def test_health_is_ok_without_dependencies() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_reports_down_services_with_503() -> None:
    # Port 1 is never a Postgres or Redis server, so both probes must fail.
    unreachable = Settings(
        database_url="postgresql+asyncpg://x:x@127.0.0.1:1/x",
        redis_url="redis://127.0.0.1:1/0",
    )
    with TestClient(create_app(unreachable)) as client:
        resp = client.get("/ready")

    assert resp.status_code == 503
    assert resp.json() == {"postgres": "down", "redis": "down"}
