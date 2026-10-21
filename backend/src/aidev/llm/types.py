"""Provider-neutral request and response types for LLM calls (ADR 004)."""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

Effort = Literal["low", "medium", "high", "xhigh", "max"]


class Role(StrEnum):
    """Who is calling. Each role picks its model from the active profile (models.py) and
    labels its spend in the usage ledger."""

    ROUTER = "router"
    CHAT = "chat"
    DOCS_QA = "docs_qa"


@dataclass(frozen=True)
class ChatMessage:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMRequest:
    role: Role
    model: str
    messages: Sequence[ChatMessage]
    system: str | None = None
    max_tokens: int = 1024
    effort: Effort = "low"
    # Off by default: the Phase 2 roles are short, latency-sensitive calls. Adapters
    # whose model can't turn thinking off fall back to the lowest effort instead.
    thinking: bool = False


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0  # uncached input
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0  # 5-minute cache writes
    cache_write_1h_tokens: int = 0
    # True when the call ended before the provider reported usage (a client disconnect
    # mid-stream) and the counts are a deliberately high estimate.
    estimated: bool = False

    @property
    def prompt_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.cache_write_1h_tokens
        )


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Usage
    stop_reason: str | None
    request_id: str | None = None


@dataclass(frozen=True)
class TextDelta:
    text: str


StreamChunk = TextDelta | Completion


class LLMError(RuntimeError):
    """The LLM call failed after the SDK's own retries, or returned nothing usable.
    Adapters raise this instead of their SDK's errors so callers stay vendor-neutral."""


class BudgetExceededError(LLMError):
    """A monthly spend cap is reached. Raised before the call is made."""
