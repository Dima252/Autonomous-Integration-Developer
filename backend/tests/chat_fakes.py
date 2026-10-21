"""In-memory stand-ins for the chat store, the event bus and docs search."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from aidev.chat.store import EDITABLE, RunStatus, StoredMessage
from aidev.events.bus import RunEvent, StoredEvent
from aidev.rag.search import SearchHit, SearchTrace


class MemoryChatStore:
    def __init__(self) -> None:
        self.conversations: set[UUID] = set()
        self.messages: dict[UUID, list[StoredMessage]] = {}
        self.runs: dict[UUID, dict[str, Any]] = {}
        self._next_id = 0

    async def create_conversation(self) -> UUID:
        cid = uuid4()
        self.conversations.add(cid)
        self.messages[cid] = []
        return cid

    async def conversation_exists(self, conversation_id: UUID) -> bool:
        return conversation_id in self.conversations

    async def add_message(
        self,
        conversation_id: UUID,
        role: Literal["user", "assistant"],
        content: str,
        intent: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> int:
        self._next_id += 1
        self.messages[conversation_id].append(
            StoredMessage(self._next_id, role, content, intent, meta or {}, datetime.now(UTC))
        )
        return self._next_id

    async def recent_messages(self, conversation_id: UUID, limit: int) -> list[StoredMessage]:
        return self.messages[conversation_id][-limit:]

    async def has_editable_run(self, conversation_id: UUID) -> bool:
        return any(
            r["conversation_id"] == conversation_id and r["status"] in EDITABLE
            for r in self.runs.values()
        )

    async def create_run(self, conversation_id: UUID, request: str, systems: Sequence[str]) -> UUID:
        run_id = uuid4()
        self.runs[run_id] = {
            "conversation_id": conversation_id,
            "request": request,
            "systems": list(systems),
            "status": RunStatus.QUEUED,
        }
        return run_id

    async def set_run_status(self, run_id: UUID, status: RunStatus) -> None:
        self.runs[run_id]["status"] = status

    async def run_exists(self, run_id: UUID) -> bool:
        return run_id in self.runs


class MemoryBus:
    def __init__(self) -> None:
        self.events: dict[UUID, list[StoredEvent]] = {}

    async def publish(self, run_id: UUID, event: RunEvent) -> StoredEvent:
        events = self.events.setdefault(run_id, [])
        seq = len(events) + 1
        stored = StoredEvent(
            f"{seq}-0",
            str(run_id),
            seq,
            datetime.now(UTC).isoformat(),
            event.type,
            event.label,
            event.node,
            event.data,
        )
        events.append(stored)
        return stored

    async def read(self, run_id: UUID, after: str | None, block_ms: int) -> list[StoredEvent]:
        await asyncio.sleep(0)
        events = self.events.get(run_id, [])
        if after is None:
            return events
        seq = int(after.split("-")[0])
        return [e for e in events if e.seq > seq]


def hit(n: int, provider: str = "stripe") -> SearchHit:
    return SearchHit(
        chunk_id=n,
        doc_key=f"{provider}:guide:{n}",
        provider=provider,
        doc_type="guide",
        title=f"Guide {n}",
        url=f"https://docs.example/{n}",
        breadcrumb=f"Stripe › Guide {n}",
        content=f"Fact number {n}.",
        score=1.0 / n,
    )


class FakeSearch:
    def __init__(self, hits: Sequence[SearchHit] = ()) -> None:
        self.hits = list(hits)
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        k: int,
        providers: Sequence[str] | None,
        trace: SearchTrace | None,
    ) -> list[SearchHit]:
        self.calls.append({"query": query, "k": k, "providers": providers})
        return self.hits[:k]
