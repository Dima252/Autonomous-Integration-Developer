from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from aidev.config import get_settings
from aidev.infra import postgres
from aidev.llm.meter import PostgresLedger, Service, UsageRecord
from aidev.llm.tables import usage_ledger
from aidev.llm.types import Usage

pytestmark = pytest.mark.integration


async def test_ledger_sums_spend_per_service_since_a_time() -> None:
    engine = postgres.create_engine(get_settings().database_url)
    ledger = PostgresLedger(engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(delete(usage_ledger).where(usage_ledger.c.operation == "test"))
        before = datetime.now(UTC) - timedelta(seconds=5)
        usage = Usage(input_tokens=100, output_tokens=50, cache_read_tokens=10)
        await ledger.add(UsageRecord(Service.ANTHROPIC, "m", "test", usage, 0.25, "req_1"))
        await ledger.add(UsageRecord(Service.ANTHROPIC, "m", "test", usage, 0.50))
        await ledger.add(UsageRecord(Service.VOYAGE, "v", "test", Usage(input_tokens=7), 0.0))

        claude = await ledger.spend_since(Service.ANTHROPIC, before)
        voyage = await ledger.spend_since(Service.VOYAGE, before)
        future = await ledger.spend_since(Service.ANTHROPIC, datetime.now(UTC) + timedelta(hours=1))
    finally:
        async with engine.begin() as conn:
            await conn.execute(delete(usage_ledger).where(usage_ledger.c.operation == "test"))
        await engine.dispose()

    assert claude.cost_usd == pytest.approx(0.75) and claude.tokens == 320
    assert voyage.tokens == 7
    assert future.cost_usd == 0 and future.tokens == 0
