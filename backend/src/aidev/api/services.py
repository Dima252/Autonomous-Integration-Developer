"""Builds the application's services once, at startup."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx2
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.chat.service import ChatService
from aidev.chat.store import ChatStore, PostgresChatStore
from aidev.config import Settings
from aidev.events.bus import EventBus, RedisEventBus
from aidev.llm.factory import create_llm, create_meter
from aidev.llm.models import model_for
from aidev.llm.types import Role
from aidev.rag.embeddings import EmbeddingServiceError
from aidev.rag.search import DocSearch
from aidev.rag.voyage import create_voyage
from aidev.router.nearest import ExampleIndex, load_examples
from aidev.router.router import Router

log = logging.getLogger(__name__)


@dataclass
class Services:
    store: ChatStore
    bus: EventBus
    redis: Redis | None = None  # rate limits
    chat: ChatService | None = None  # None without ANTHROPIC_API_KEY
    _warmup: asyncio.Task[None] | None = None

    async def aclose(self) -> None:
        if self._warmup is not None:
            self._warmup.cancel()
        if self.chat is not None:
            await self.chat.drain()
            await self.chat.router.drain()


ServicesFactory = Callable[[Settings, AsyncEngine, Redis, httpx2.AsyncClient], Awaitable[Services]]


async def build_services(
    settings: Settings, db: AsyncEngine, redis: Redis, http: httpx2.AsyncClient
) -> Services:
    store = PostgresChatStore(db)
    bus = RedisEventBus(redis)
    meter = create_meter(settings, db)
    embedder = reranker = None
    if settings.voyage_api_key is not None:
        embedder, reranker = create_voyage(settings, http, meter)
    search = DocSearch(db, embedder, reranker)

    if settings.anthropic_api_key is None:
        log.warning("ANTHROPIC_API_KEY is not set: /chat is disabled")
        return Services(store, bus, redis)

    llm = create_llm(settings, meter)
    examples = None
    if embedder is not None:
        examples = ExampleIndex(
            embedder, load_examples(settings.corpus_dir / "router" / "examples.toml")
        )
    router = Router(llm, model_for(Role.ROUTER, settings.llm_profile), examples)
    chat = ChatService(
        store, router, llm, bus, search, lambda role: model_for(role, settings.llm_profile)
    )
    services = Services(store, bus, redis, chat)
    if examples is not None:
        # Embed the router's examples now rather than on the first message. In the
        # background, so startup doesn't wait on (or fail with) the embedding service.
        services._warmup = asyncio.ensure_future(_warm(examples))
    return services


async def _warm(examples: ExampleIndex) -> None:
    try:
        await examples.vectors()
    except EmbeddingServiceError:
        log.warning("couldn't embed router examples at startup; will retry on first use")
