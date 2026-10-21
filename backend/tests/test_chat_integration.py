import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.api.main import create_app
from aidev.api.services import Services
from aidev.chat.store import PostgresChatStore, RunStatus
from aidev.config import Settings, get_settings
from aidev.events.bus import EventType, RedisEventBus, RunEvent
from aidev.infra import postgres
from aidev.infra import redis as redis_infra
from tests.chat_fakes import MemoryBus, MemoryChatStore

pytestmark = pytest.mark.integration


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = postgres.create_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    client = redis_infra.create_client(get_settings().redis_url)
    yield client
    await client.aclose()


async def test_store_round_trips_conversations_messages_and_runs(engine: AsyncEngine) -> None:
    store = PostgresChatStore(engine)
    cid = await store.create_conversation()

    await store.add_message(cid, "user", "hi", intent="chitchat", meta={"route": {"tier": 0}})
    await store.add_message(cid, "assistant", "hello")
    await store.add_message(cid, "user", "build it")

    assert await store.conversation_exists(cid)
    assert not await store.conversation_exists(uuid4())
    last_two = await store.recent_messages(cid, limit=2)
    assert [m.content for m in last_two] == ["hello", "build it"]  # oldest first
    first = (await store.recent_messages(cid, limit=10))[0]
    assert (first.intent, first.meta) == ("chitchat", {"route": {"tier": 0}})

    assert not await store.has_editable_run(cid)
    run_id = await store.create_run(cid, "build it", ["stripe"])
    assert await store.run_exists(run_id)
    assert await store.has_editable_run(cid)
    await store.set_run_status(run_id, RunStatus.FAILED)
    assert not await store.has_editable_run(cid)


async def test_bus_replays_resumes_and_expires_finished_runs(redis: Redis) -> None:
    bus = RedisEventBus(redis, prefix=f"test:{uuid4()}")
    run_id = uuid4()

    a = await bus.publish(run_id, RunEvent(EventType.RUN_STARTED, "started", data={"x": 1}))
    await bus.publish(run_id, RunEvent(EventType.STEP_STARTED, "analyst", node="analyst"))
    await bus.publish(run_id, RunEvent(EventType.RUN_COMPLETED, "done"))

    everything = await bus.read(run_id, None, block_ms=100)
    resumed = await bus.read(run_id, a.id, block_ms=100)

    assert [e.seq for e in everything] == [1, 2, 3]
    assert everything[0].data == {"x": 1} and everything[1].node == "analyst"
    assert [e.type for e in resumed] == [EventType.STEP_STARTED, EventType.RUN_COMPLETED]
    assert everything[-1].terminal
    assert await redis.ttl(f"{bus.prefix}:{run_id}:events") > 0


async def test_blocking_read_wakes_up_for_a_new_event(redis: Redis) -> None:
    bus = RedisEventBus(redis, prefix=f"test:{uuid4()}")
    reader = redis_infra.create_client(get_settings().redis_url)  # XREAD BLOCK holds it
    run_id = uuid4()
    try:
        waiting = asyncio.create_task(RedisEventBus(reader, bus.prefix).read(run_id, "$", 5000))
        await asyncio.sleep(0.2)
        await bus.publish(run_id, RunEvent(EventType.RUN_STARTED, "started"))
        events = await asyncio.wait_for(waiting, timeout=5)
    finally:
        await reader.aclose()

    assert [e.type for e in events] == [EventType.RUN_STARTED]


async def _rate_limited_services(
    settings: Settings, db: AsyncEngine, redis: Redis, http: httpx2.AsyncClient
) -> Services:
    for key in [k async for k in redis.scan_iter("aidev:rl:chat:testclient:*")]:
        await redis.delete(key)
    return Services(MemoryChatStore(), MemoryBus(), redis, chat=None)


def test_chat_is_rate_limited_per_client() -> None:
    settings = Settings(chat_rate_limit_per_minute=2)
    with TestClient(create_app(settings, _rate_limited_services)) as client:
        # No chat service, so allowed requests get a 503 after passing the limiter.
        codes = [client.post("/chat", json={"message": "hi"}).status_code for _ in range(3)]

    assert codes == [503, 503, 429]
