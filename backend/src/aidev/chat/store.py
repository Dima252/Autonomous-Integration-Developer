"""Conversations, messages and runs in Postgres."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from sqlalchemy import exists, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.chat.tables import conversations, messages, runs


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


# A run the user can still edit with a follow-up.
EDITABLE = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.COMPLETED)


@dataclass(frozen=True)
class StoredMessage:
    id: int
    role: Literal["user", "assistant"]
    content: str
    intent: str | None
    meta: dict[str, Any]
    created_at: datetime


class ChatStore(Protocol):
    async def create_conversation(self) -> UUID: ...

    async def conversation_exists(self, conversation_id: UUID) -> bool: ...

    async def add_message(
        self,
        conversation_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        intent: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int: ...

    async def recent_messages(
        self, conversation_id: UUID, limit: int
    ) -> Sequence[StoredMessage]: ...

    async def has_editable_run(self, conversation_id: UUID) -> bool: ...

    async def create_run(
        self, conversation_id: UUID, request: str, systems: Sequence[str]
    ) -> UUID: ...

    async def set_run_status(self, run_id: UUID, status: RunStatus) -> None: ...

    async def run_exists(self, run_id: UUID) -> bool: ...


class PostgresChatStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def create_conversation(self) -> UUID:
        conversation_id = uuid4()
        async with self.engine.begin() as conn:
            await conn.execute(insert(conversations).values(id=conversation_id))
        return conversation_id

    async def conversation_exists(self, conversation_id: UUID) -> bool:
        async with self.engine.connect() as conn:
            return bool(
                await conn.scalar(select(exists().where(conversations.c.id == conversation_id)))
            )

    async def add_message(
        self,
        conversation_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        intent: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        stmt = (
            insert(messages)
            .values(
                conversation_id=conversation_id,
                role=role,
                content=content,
                intent=intent,
                meta=meta or {},
            )
            .returning(messages.c.id)
        )
        async with self.engine.begin() as conn:
            return (await conn.execute(stmt)).scalar_one()

    async def recent_messages(self, conversation_id: UUID, limit: int) -> list[StoredMessage]:
        """The last `limit` messages, oldest first."""
        m = messages.c
        stmt = (
            select(m.id, m.role, m.content, m.intent, m.meta, m.created_at)
            .where(m.conversation_id == conversation_id)
            .order_by(m.id.desc())
            .limit(limit)
        )
        async with self.engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [StoredMessage(*r) for r in reversed(rows)]

    async def has_editable_run(self, conversation_id: UUID) -> bool:
        stmt = select(
            exists().where(runs.c.conversation_id == conversation_id, runs.c.status.in_(EDITABLE))
        )
        async with self.engine.connect() as conn:
            return bool(await conn.scalar(stmt))

    async def create_run(self, conversation_id: UUID, request: str, systems: Sequence[str]) -> UUID:
        run_id = uuid4()
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(runs).values(
                    id=run_id,
                    conversation_id=conversation_id,
                    request=request,
                    systems=list(systems),
                    status=RunStatus.QUEUED,
                )
            )
        return run_id

    async def set_run_status(self, run_id: UUID, status: RunStatus) -> None:
        stmt = update(runs).where(runs.c.id == run_id).values(status=status, updated_at=func.now())
        async with self.engine.begin() as conn:
            await conn.execute(stmt)

    async def run_exists(self, run_id: UUID) -> bool:
        async with self.engine.connect() as conn:
            return bool(await conn.scalar(select(exists().where(runs.c.id == run_id))))
