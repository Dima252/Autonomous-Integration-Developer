"""Two-tier router (docs/architecture.md §2, ADR 012).

Tier 0 answers obvious small talk without waiting for a model: whole-message rules,
then nearest-neighbour matching against labeled examples. Tier 1, a small model with
structured output, handles everything else and also names the systems involved.

The nearest-neighbour lookup and the classifier start together, so messages that aren't
small talk wait no longer than the classifier alone. When tier 0 answers first, the
classifier still finishes in the background as a shadow check: its spend is recorded
exactly, and a disagreement with tier 0 is logged, which measures tier 0's precision on
real traffic.
"""

import asyncio
import logging
import time

from aidev.llm.client import LLMClient
from aidev.llm.types import BudgetExceededError, LLMError
from aidev.rag.embeddings import EmbeddingServiceError
from aidev.router.classifier import classify
from aidev.router.intents import Intent, RouteContext, RouteDecision
from aidev.router.nearest import (
    DEFAULT_THRESHOLDS,
    ExampleIndex,
    Match,
    Thresholds,
    is_chitchat,
)
from aidev.router.rules import match_smalltalk

log = logging.getLogger(__name__)


class Router:
    def __init__(
        self,
        llm: LLMClient,
        model: str,
        examples: ExampleIndex | None = None,
        thresholds: Thresholds = DEFAULT_THRESHOLDS,
    ) -> None:
        self.llm = llm
        self.model = model
        self.examples = examples
        self.thresholds = thresholds
        self._shadows: set[asyncio.Task[RouteDecision]] = set()

    async def route(self, text: str, context: RouteContext | None = None) -> RouteDecision:
        context = context or RouteContext()
        start = time.perf_counter()
        decision = await self._route(text, context)
        latency = (time.perf_counter() - start) * 1000
        return RouteDecision(**{**decision.__dict__, "latency_ms": latency})

    async def drain(self) -> None:
        """Waits for shadow classifications still running (tests, shutdown)."""
        if self._shadows:
            await asyncio.gather(*self._shadows, return_exceptions=True)

    async def _route(self, text: str, context: RouteContext) -> RouteDecision:
        rule = match_smalltalk(text, context)
        if rule is not None:
            return RouteDecision(Intent.CHITCHAT, 1.0, tier=0, reason=f"rule: {rule}", rule=rule)

        if self.examples is None or context.awaiting_answer:
            return await self._classify(text, context)

        classifier = asyncio.ensure_future(self._classify(text, context))
        match = await self._nearest(self.examples, text)
        if match is None or not is_chitchat(match, self.thresholds):
            return await classifier
        self._shadow(classifier, text)
        return RouteDecision(
            Intent.CHITCHAT,
            match.similarity,
            tier=0,
            reason=f"nearest: {match.example!r} ({match.similarity:.2f})",
        )

    async def _nearest(self, examples: ExampleIndex, text: str) -> Match | None:
        try:
            return await examples.match(await examples.embed(text))
        except EmbeddingServiceError:
            log.warning("embedding service unavailable; skipping tier 0 matching")
            return None

    async def _classify(self, text: str, context: RouteContext) -> RouteDecision:
        try:
            return await classify(self.llm, self.model, text, context)
        except LLMError as exc:
            log.warning("router classifier failed: %s", exc)
            # No safe guess exists without the classifier; the chat layer explains.
            return RouteDecision(
                Intent.CLARIFY,
                0.0,
                tier=1,
                reason=f"classifier failed: {exc}",
                degraded=True,
                budget_exceeded=isinstance(exc, BudgetExceededError),
            )

    def _shadow(self, task: asyncio.Task[RouteDecision], text: str) -> None:
        self._shadows.add(task)

        def done(t: asyncio.Task[RouteDecision]) -> None:
            self._shadows.discard(t)
            if t.cancelled() or t.exception() is not None:
                return
            shadow = t.result()
            if not shadow.degraded and shadow.intent is not Intent.CHITCHAT:
                log.warning(
                    "tier 0 disagreement: answered chitchat, classifier says %s (%.2f): %r",
                    shadow.intent,
                    shadow.confidence,
                    text[:200],
                )

        task.add_done_callback(done)
