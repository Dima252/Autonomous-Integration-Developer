"""Spend metering and hard monthly caps.

Every paid call is checked against its service's cap before it is sent and written to
the usage ledger after it returns. The cap is checked, not reserved, so calls already in
flight when it is reached can overshoot it by their own cost (cents at most at Phase 2
volumes). That's the price of not serializing every LLM call through a lock.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.config import Settings
from aidev.llm.client import LLMClient
from aidev.llm.pricing import PRICES, UnpricedModelError, cost_usd
from aidev.llm.tables import usage_ledger
from aidev.llm.types import (
    BudgetExceededError,
    Completion,
    LLMRequest,
    StreamChunk,
    TextDelta,
    Usage,
)

log = logging.getLogger(__name__)


class Service(StrEnum):
    ANTHROPIC = "anthropic"
    VOYAGE = "voyage"


@dataclass(frozen=True)
class UsageRecord:
    service: Service
    model: str
    operation: str
    usage: Usage
    cost_usd: float
    request_id: str | None = None


@dataclass(frozen=True)
class Spend:
    cost_usd: float = 0.0
    tokens: int = 0


class UsageLedger(Protocol):
    async def add(self, record: UsageRecord) -> None: ...

    async def spend_since(self, service: Service, since: datetime) -> Spend: ...


class PostgresLedger:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    async def add(self, record: UsageRecord) -> None:
        u = record.usage
        async with self.engine.begin() as conn:
            await conn.execute(
                insert(usage_ledger).values(
                    service=record.service,
                    model=record.model,
                    operation=record.operation,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_write_tokens=u.cache_write_tokens + u.cache_write_1h_tokens,
                    cost_usd=record.cost_usd,
                    estimated=u.estimated,
                    request_id=record.request_id,
                )
            )

    async def spend_since(self, service: Service, since: datetime) -> Spend:
        t = usage_ledger.c
        tokens = t.input_tokens + t.output_tokens + t.cache_read_tokens + t.cache_write_tokens
        stmt = select(
            func.coalesce(func.sum(t.cost_usd), 0), func.coalesce(func.sum(tokens), 0)
        ).where(t.service == service, t.created_at >= since)
        async with self.engine.connect() as conn:
            cost, total = (await conn.execute(stmt)).one()
        return Spend(cost_usd=float(cost), tokens=int(total))


class SpendMeter:
    def __init__(
        self,
        ledger: UsageLedger,
        anthropic_usd_cap: float,
        voyage_token_cap: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.ledger = ledger
        self.anthropic_usd_cap = anthropic_usd_cap
        self.voyage_token_cap = voyage_token_cap
        self._clock = clock

    @classmethod
    def from_settings(cls, ledger: UsageLedger, settings: Settings) -> "SpendMeter":
        return cls(ledger, settings.anthropic_monthly_usd_cap, settings.voyage_monthly_token_cap)

    async def month_to_date(self, service: Service) -> Spend:
        now = self._clock()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return await self.ledger.spend_since(service, start)

    async def check(self, service: Service) -> None:
        """Raises `BudgetExceededError` once this month's spend has reached the cap."""
        spend = await self.month_to_date(service)
        if service is Service.ANTHROPIC and spend.cost_usd >= self.anthropic_usd_cap:
            raise BudgetExceededError(
                f"monthly Anthropic cap reached: ${spend.cost_usd:.2f} of "
                f"${self.anthropic_usd_cap:.2f} (ANTHROPIC_MONTHLY_USD_CAP)"
            )
        if service is Service.VOYAGE and spend.tokens >= self.voyage_token_cap:
            raise BudgetExceededError(
                f"monthly Voyage cap reached: {spend.tokens:,} of {self.voyage_token_cap:,} "
                "tokens (VOYAGE_MONTHLY_TOKEN_CAP)"
            )

    async def record(self, record: UsageRecord) -> None:
        await self.ledger.add(record)


class MeteredLLM:
    """An `LLMClient` that enforces the spend cap and records every call's cost."""

    def __init__(self, inner: LLMClient, meter: SpendMeter, service: Service) -> None:
        self.inner = inner
        self.meter = meter
        self.service = service
        self.provider = inner.provider
        self._pending: set[asyncio.Task[None]] = set()

    async def complete(self, request: LLMRequest) -> Completion:
        await self._check(request)
        completion = await self.inner.complete(request)
        await self._record(request, completion.model, completion.usage, completion.request_id)
        return completion

    async def stream(self, request: LLMRequest) -> AsyncGenerator[StreamChunk]:
        await self._check(request)
        seen: list[str] = []
        done = False
        try:
            async for chunk in self.inner.stream(request):
                if isinstance(chunk, TextDelta):
                    seen.append(chunk.text)
                else:
                    done = True
                    await self._record(request, chunk.model, chunk.usage, chunk.request_id)
                yield chunk
        finally:
            if not done:
                # The consumer went away (client disconnect) or the stream failed before
                # the provider reported usage. Record an over-estimate so abandoned
                # streams still count against the cap. Not awaited: this may run during
                # task cancellation, where awaiting would be cancelled too.
                usage = _estimate_usage(request, "".join(seen))
                task = asyncio.ensure_future(self._record(request, request.model, usage, None))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)

    async def parse[T: BaseModel](
        self, request: LLMRequest, schema: type[T]
    ) -> tuple[T, Completion]:
        await self._check(request)
        parsed, completion = await self.inner.parse(request, schema)
        await self._record(request, completion.model, completion.usage, completion.request_id)
        return parsed, completion

    async def flush(self) -> None:
        """Waits for estimates still being written (tests, shutdown)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def _check(self, request: LLMRequest) -> None:
        if request.model not in PRICES:
            # An unpriced model would run uncapped, so it doesn't run at all.
            raise UnpricedModelError(f"no price for {request.model!r} in aidev/llm/pricing.py")
        await self.meter.check(self.service)

    async def _record(
        self, request: LLMRequest, model: str, usage: Usage, request_id: str | None
    ) -> None:
        try:
            await self.meter.record(
                UsageRecord(
                    self.service, model, request.role, usage, cost_usd(model, usage), request_id
                )
            )
        except Exception:
            # The call already happened; losing its ledger row must not fail the request.
            log.exception("failed to record LLM usage for %s", model)


def _estimate_usage(request: LLMRequest, output: str) -> Usage:
    """3 characters per token, deliberately high, like the Voyage pacing estimate."""
    prompt = (request.system or "") + "".join(m.content for m in request.messages)
    return Usage(
        input_tokens=len(prompt) // 3 + 1, output_tokens=len(output) // 3 + 1, estimated=True
    )
