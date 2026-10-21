"""Chat and run-event endpoints, both streamed as Server-Sent Events."""

import logging
import time
from collections.abc import AsyncIterable
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field

from aidev.api.services import Services
from aidev.chat.service import ChatService
from aidev.config import Settings

log = logging.getLogger(__name__)

# How long one Redis read waits for new run events before looping. The SSE layer sends
# keep-alive comments meanwhile, so idle connections survive proxies.
EVENTS_BLOCK_MS = 15_000

router = APIRouter()


def services(request: Request) -> Services:
    return request.app.state.services


def settings(request: Request) -> Settings:
    return request.app.state.settings


ServicesDep = Annotated[Services, Depends(services)]
SettingsDep = Annotated[Settings, Depends(settings)]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    conversation_id: UUID | None = None


class ConversationOut(BaseModel):
    conversation_id: UUID


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    intent: str | None
    meta: dict[str, Any]


def chat_service(svc: ServicesDep) -> ChatService:
    if svc.chat is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "chat is not configured")
    return svc.chat


async def rate_limit(request: Request, svc: ServicesDep, cfg: SettingsDep) -> None:
    """Fixed one-minute window per client IP, counted in Redis. Fails open: a Redis
    outage shouldn't take chat down with it."""
    if svc.redis is None or request.client is None:
        return
    window = int(time.time() // 60)
    key = f"aidev:rl:chat:{request.client.host}:{window}"
    try:
        count = await svc.redis.incr(key)
        if count == 1:
            await svc.redis.expire(key, 60)
    except Exception:
        log.warning("rate limiter unavailable; allowing request", exc_info=True)
        return
    if count > cfg.chat_rate_limit_per_minute:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many messages; try again in a minute",
            headers={"Retry-After": "60"},
        )


async def conversation(body: ChatRequest, svc: ServicesDep, cfg: SettingsDep) -> UUID:
    """Validates the request before the stream starts, so errors get a real status."""
    if len(body.message) > cfg.max_message_chars:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"message is longer than {cfg.max_message_chars} characters",
        )
    if body.conversation_id is None:
        return await svc.store.create_conversation()
    if not await svc.store.conversation_exists(body.conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return body.conversation_id


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
async def create_conversation(svc: ServicesDep) -> ConversationOut:
    return ConversationOut(conversation_id=await svc.store.create_conversation())


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, svc: ServicesDep) -> list[MessageOut]:
    if not await svc.store.conversation_exists(conversation_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    rows = await svc.store.recent_messages(conversation_id, limit=500)
    return [
        MessageOut(id=m.id, role=m.role, content=m.content, intent=m.intent, meta=m.meta)
        for m in rows
    ]


@router.post("/chat", response_class=EventSourceResponse, dependencies=[Depends(rate_limit)])
async def chat(
    body: ChatRequest,
    conversation_id: Annotated[UUID, Depends(conversation)],
    chat: Annotated[ChatService, Depends(chat_service)],
) -> AsyncIterable[ServerSentEvent]:
    """Routes the message and streams the answer (event types: aidev.chat.service)."""
    async for event in chat.reply(conversation_id, body.message):
        yield ServerSentEvent(event=event.type, data=event.data)


async def existing_run(run_id: UUID, svc: ServicesDep) -> UUID:
    if not await svc.store.run_exists(run_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return run_id


@router.get("/runs/{run_id}/events", response_class=EventSourceResponse)
async def run_events(
    run_id: Annotated[UUID, Depends(existing_run)],
    svc: ServicesDep,
    last_event_id: Annotated[str | None, Header()] = None,
) -> AsyncIterable[ServerSentEvent]:
    """A run's events from the start, or after `Last-Event-ID` on reconnect. Ends after
    the run's terminal event."""
    after = last_event_id
    while True:
        for event in await svc.bus.read(run_id, after, EVENTS_BLOCK_MS):
            after = event.id
            yield ServerSentEvent(id=event.id, event=event.type.value, data=event.envelope())
            if event.terminal:
                return
