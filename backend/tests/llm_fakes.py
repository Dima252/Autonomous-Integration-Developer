"""Offline stand-ins for the LLM layer."""

from collections.abc import AsyncGenerator, Callable
from datetime import datetime

from pydantic import BaseModel

from aidev.llm.meter import Service, Spend, UsageRecord
from aidev.llm.types import Completion, LLMError, LLMRequest, StreamChunk, TextDelta, Usage


class MemoryLedger:
    def __init__(self) -> None:
        self.records: list[tuple[datetime, UsageRecord]] = []
        self.now: datetime | None = None

    async def add(self, record: UsageRecord) -> None:
        assert self.now is not None, "set ledger.now to the test clock"
        self.records.append((self.now, record))

    async def spend_since(self, service: Service, since: datetime) -> Spend:
        rows = [r for t, r in self.records if r.service == service and t >= since]
        u = [r.usage for r in rows]
        return Spend(
            cost_usd=sum(r.cost_usd for r in rows),
            tokens=sum(
                x.input_tokens + x.output_tokens + x.cache_read_tokens + x.cache_write_tokens
                for x in u
            ),
        )


DEFAULT_USAGE = Usage(input_tokens=100, output_tokens=20)


class FakeLLM:
    """Answers from a script. `parse` builds the schema from `parsed(request)`."""

    provider = "fake"

    def __init__(
        self,
        text: str = "hello",
        usage: Usage = DEFAULT_USAGE,
        parsed: Callable[[LLMRequest], dict[str, object]] | None = None,
        fail: bool = False,
    ) -> None:
        self.text = text
        self.usage = usage
        self.parsed = parsed
        self.fail = fail
        self.requests: list[LLMRequest] = []

    def _completion(self, request: LLMRequest) -> Completion:
        return Completion(self.text, request.model, self.usage, "end_turn", "req_fake")

    async def complete(self, request: LLMRequest) -> Completion:
        self.requests.append(request)
        if self.fail:
            raise LLMError("fake outage")
        return self._completion(request)

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk]:
        self.requests.append(request)
        if self.fail:
            raise LLMError("fake outage")
        for word in self.text.split(" "):
            yield TextDelta(word + " ")
        yield self._completion(request)

    async def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, Completion]:
        self.requests.append(request)
        if self.fail or self.parsed is None:
            raise LLMError("fake outage")
        return schema.model_validate(self.parsed(request)), self._completion(request)
