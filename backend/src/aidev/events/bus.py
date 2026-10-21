"""Run events: one typed envelope per step, appended to a Redis Stream per run
(docs/architecture.md §5).

The stream entry id doubles as the SSE event id, so a client that reconnects with
`Last-Event-ID` resumes exactly after the last event it saw. `seq` is a per-run counter
for humans and the UI: it orders events without parsing Redis ids.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from redis.asyncio import Redis


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    STEP_STARTED = "step.started"
    STEP_COMPLETED = "step.completed"
    CLARIFICATION_REQUESTED = "clarification.requested"
    ASSUMPTION_RECORDED = "assumption.recorded"
    RETRIEVAL_HITS = "retrieval.hits"
    DECISION_RECORDED = "decision.recorded"
    THOUGHT_UPDATE = "thought.update"
    CODE_DELTA = "code.delta"
    CHECK_RESULT = "check.result"
    ITERATION_STARTED = "iteration.started"
    LESSON_LEARNED = "lesson.learned"


TERMINAL = frozenset({EventType.RUN_COMPLETED, EventType.RUN_FAILED})


@dataclass(frozen=True)
class RunEvent:
    type: EventType
    label: str  # one line for the timeline, from a template
    node: str | None = None  # the graph node that emitted it
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent:
    id: str  # stream entry id: the SSE event id
    run_id: str
    seq: int
    ts: str
    type: EventType
    label: str
    node: str | None
    data: dict[str, Any]

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL

    def envelope(self) -> dict[str, Any]:
        """The JSON shape clients receive."""
        body = asdict(self)
        del body["id"]
        return body


class EventBus(Protocol):
    async def publish(self, run_id: UUID, event: RunEvent) -> StoredEvent: ...

    async def read(
        self, run_id: UUID, after: str | None, block_ms: int
    ) -> Sequence[StoredEvent]: ...


# Bounded so a runaway run can't fill Redis; far above any real run's event count.
MAX_EVENTS = 10_000
# Finished runs are replayed from Postgres later (Phase 5); Redis keeps them a while.
FINISHED_TTL_S = 7 * 24 * 3600


class RedisEventBus:
    def __init__(self, redis: Redis, prefix: str = "aidev:run") -> None:
        self.redis = redis
        self.prefix = prefix

    def _key(self, run_id: UUID, part: str) -> str:
        return f"{self.prefix}:{run_id}:{part}"

    async def publish(self, run_id: UUID, event: RunEvent) -> StoredEvent:
        # INCR then XADD is not atomic, so `seq` follows stream order only with one
        # writer per run. That holds by design: a run belongs to one worker task.
        seq = await self.redis.incr(self._key(run_id, "seq"))
        ts = datetime.now(UTC).isoformat()
        payload = json.dumps(
            {
                "seq": seq,
                "ts": ts,
                "type": event.type.value,
                "label": event.label,
                "node": event.node,
                "data": event.data,
            }
        )
        stream = self._key(run_id, "events")
        entry_id = await self.redis.xadd(
            stream, {"e": payload}, maxlen=MAX_EVENTS, approximate=True
        )
        if event.type in TERMINAL:
            await self.redis.expire(stream, FINISHED_TTL_S)
            await self.redis.expire(self._key(run_id, "seq"), FINISHED_TTL_S)
        return StoredEvent(
            _text(entry_id), str(run_id), seq, ts, event.type, event.label, event.node, event.data
        )

    async def read(self, run_id: UUID, after: str | None, block_ms: int) -> list[StoredEvent]:
        """Events after the given entry id (from the start when None), waiting up to
        `block_ms` for the first one."""
        stream = self._key(run_id, "events")
        reply: Any = await self.redis.xread({stream: after or "0-0"}, block=block_ms, count=500)
        events = []
        for _, entries in reply or []:
            for entry_id, fields in entries:
                body = json.loads(_text(fields[b"e"] if b"e" in fields else fields["e"]))
                events.append(
                    StoredEvent(
                        id=_text(entry_id),
                        run_id=str(run_id),
                        seq=body["seq"],
                        ts=body["ts"],
                        type=EventType(body["type"]),
                        label=body["label"],
                        node=body["node"],
                        data=body["data"],
                    )
                )
        return events


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value
