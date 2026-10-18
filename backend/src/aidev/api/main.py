import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request, Response, status

from aidev import __version__
from aidev.config import Settings, get_settings
from aidev.infra import postgres, redis

PROBE_TIMEOUT_S = 2.0

ProbeStatus = Literal["up", "down"]


async def _probe(check: Awaitable[None]) -> ProbeStatus:
    try:
        await asyncio.wait_for(check, timeout=PROBE_TIMEOUT_S)
    except Exception:
        return "down"
    return "up"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.db = postgres.create_engine(settings.database_url)
        app.state.redis = redis.create_client(settings.redis_url)
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.db.dispose()

    app = FastAPI(title="Autonomous Integration Developer", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness: the process is up. Never touches dependencies."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request, response: Response) -> dict[str, ProbeStatus]:
        """Readiness: every backing service answers within the probe timeout."""
        pg, rd = await asyncio.gather(
            _probe(postgres.ping(request.app.state.db)),
            _probe(redis.ping(request.app.state.redis)),
        )
        checks: dict[str, ProbeStatus] = {"postgres": pg, "redis": rd}
        if "down" in checks.values():
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return checks

    return app


app = create_app()
