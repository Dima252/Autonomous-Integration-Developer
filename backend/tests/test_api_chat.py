from collections.abc import Iterator
from uuid import uuid4

import httpx2
import pytest
from fastapi.testclient import TestClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.api.main import create_app
from aidev.api.services import Services
from aidev.chat.service import ChatService
from aidev.config import Settings
from aidev.events.bus import EventType, RunEvent
from aidev.llm.models import HAIKU
from aidev.router.router import Router
from tests.chat_fakes import FakeSearch, MemoryBus, MemoryChatStore
from tests.llm_fakes import FakeLLM
from tests.sse import parse_sse

SETTINGS = Settings(max_message_chars=50)


def _classifier(intent: str):
    return FakeLLM(
        text="hello there",
        parsed=lambda _: {
            "intent": intent,
            "confidence": 0.9,
            "systems": ["stripe"],
            "search_query": "",
        },
    )


class Harness:
    def __init__(self, chat_enabled: bool = True, intent: str = "chitchat") -> None:
        self.store = MemoryChatStore()
        self.bus = MemoryBus()
        llm = _classifier(intent)
        self.chat = (
            ChatService(
                self.store, Router(llm, HAIKU), llm, self.bus, FakeSearch(), lambda _: HAIKU
            )
            if chat_enabled
            else None
        )

    async def factory(
        self, settings: Settings, db: AsyncEngine, redis: Redis, http: httpx2.AsyncClient
    ) -> Services:
        return Services(self.store, self.bus, None, self.chat)


@pytest.fixture
def harness() -> Harness:
    return Harness()


@pytest.fixture
def client(harness: Harness) -> Iterator[TestClient]:
    with TestClient(create_app(SETTINGS, harness.factory)) as c:
        yield c


def test_chat_streams_events_and_starts_a_conversation(client: TestClient) -> None:
    resp = client.post("/chat", json={"message": "what's your favourite movie?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    assert [e[0] for e in events] == ["conversation", "route", "delta", "delta", "done"]
    cid = events[0][2]["conversation_id"]

    history = client.get(f"/conversations/{cid}/messages").json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "hello there "
    assert history[0]["meta"]["route"]["intent"] == "chitchat"


def test_chat_continues_an_existing_conversation(client: TestClient) -> None:
    cid = client.post("/conversations").json()["conversation_id"]

    client.post("/chat", json={"message": "hi", "conversation_id": cid})
    client.post("/chat", json={"message": "thanks!", "conversation_id": cid})

    assert len(client.get(f"/conversations/{cid}/messages").json()) == 4


def test_unknown_conversation_is_a_404_before_streaming(client: TestClient) -> None:
    resp = client.post("/chat", json={"message": "hi", "conversation_id": str(uuid4())})

    assert resp.status_code == 404


def test_overlong_and_empty_messages_are_rejected(client: TestClient) -> None:
    assert client.post("/chat", json={"message": "x" * 51}).status_code == 422
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_chat_without_an_api_key_is_unavailable() -> None:
    harness = Harness(chat_enabled=False)
    with TestClient(create_app(SETTINGS, harness.factory)) as client:
        assert client.post("/chat", json={"message": "hi"}).status_code == 503


def test_run_events_stream_until_the_run_ends() -> None:
    harness = Harness(intent="integration_task")
    with TestClient(create_app(SETTINGS, harness.factory)) as client:
        events = parse_sse(client.post("/chat", json={"message": "sync stripe to hubspot"}).text)
        run = next(data for name, _, data in events if name == "run")

        resp = client.get(run["events_url"])

    run_events = parse_sse(resp.text)
    assert [e[0] for e in run_events] == ["run.created", "run.failed"]
    assert [e[2]["seq"] for e in run_events] == [1, 2]
    assert run_events[0][1] is not None  # an id, for Last-Event-ID


async def test_run_events_resume_after_last_event_id() -> None:
    harness = Harness()
    cid = await harness.store.create_conversation()
    run_id = await harness.store.create_run(cid, "x", [])
    first = await harness.bus.publish(run_id, RunEvent(EventType.RUN_STARTED, "started"))
    await harness.bus.publish(run_id, RunEvent(EventType.STEP_STARTED, "analyst"))
    await harness.bus.publish(run_id, RunEvent(EventType.RUN_COMPLETED, "done"))

    with TestClient(create_app(SETTINGS, harness.factory)) as client:
        resp = client.get(f"/runs/{run_id}/events", headers={"Last-Event-ID": first.id})

    assert [e[0] for e in parse_sse(resp.text)] == ["step.started", "run.completed"]


def test_unknown_run_is_a_404(client: TestClient) -> None:
    assert client.get(f"/runs/{uuid4()}/events").status_code == 404
