"""The LLM interface every caller depends on (ADR 004). Adapters implement it for a
provider; `MeteredLLM` wraps any adapter with spend caps and the usage ledger."""

from collections.abc import AsyncGenerator
from typing import Protocol

from pydantic import BaseModel

from aidev.llm.types import Completion, LLMRequest, StreamChunk


class LLMClient(Protocol):
    provider: str

    async def complete(self, request: LLMRequest) -> Completion: ...

    def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk]:
        """Yields `TextDelta`s as text arrives, then exactly one `Completion`."""
        ...

    async def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, Completion]:
        """Structured output, validated against `schema`."""
        ...
