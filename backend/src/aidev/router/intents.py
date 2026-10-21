from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from aidev.llm.types import ChatMessage


class Intent(StrEnum):
    """Routes a message can take (docs/architecture.md §2)."""

    CHITCHAT = "chitchat"  # direct reply from the small model
    DOCS_QUESTION = "docs_question"  # one RAG call, no agents
    INTEGRATION_TASK = "integration_task"  # start an agent run
    FOLLOWUP_EDIT = "followup_edit"  # change the current run's spec and resume it
    CLARIFY = "clarify"  # ask the user: cheaper than a wrong heavy run


# Intents that start or resume the agent graph: the expensive mistakes are here.
HEAVY = frozenset({Intent.INTEGRATION_TASK, Intent.FOLLOWUP_EDIT})

Provider = Literal["stripe", "slack", "github", "hubspot", "other"]


@dataclass(frozen=True)
class RouteContext:
    history: Sequence[ChatMessage] = ()  # recent turns, oldest first
    active_run: bool = False  # the conversation has an integration run to edit

    @property
    def awaiting_answer(self) -> bool:
        """The assistant's last turn asked the user something, so a short reply like
        "yes" or "ok" is an answer, not small talk."""
        return bool(
            self.history
            and self.history[-1].role == "assistant"
            and self.history[-1].content.rstrip().endswith("?")
        )


@dataclass(frozen=True)
class RouteDecision:
    intent: Intent
    confidence: float
    tier: int  # 0: rules or nearest neighbour, no LLM; 1: LLM classifier
    reason: str
    systems: tuple[Provider, ...] = ()
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    degraded: bool = False  # the classifier failed and this is the safe default
    budget_exceeded: bool = False  # it failed because the monthly spend cap is reached
    search_query: str | None = None  # docs questions, rewritten to stand alone
    rule: str | None = None  # which tier 0 rule answered (rules.RULE_CLASSES)
