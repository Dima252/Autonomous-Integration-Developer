from datetime import UTC, datetime

import pytest

from aidev.llm.meter import MeteredLLM, Service, SpendMeter, UsageRecord
from aidev.llm.models import HAIKU, OPUS
from aidev.llm.pricing import UnpricedModelError, cost_usd
from aidev.llm.types import BudgetExceededError, ChatMessage, LLMRequest, Role, TextDelta, Usage
from tests.llm_fakes import FakeLLM, MemoryLedger

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _request(model: str = HAIKU) -> LLMRequest:
    return LLMRequest(Role.CHAT, model, [ChatMessage("user", "hi")], system="be brief")


def _meter(ledger: MemoryLedger, usd_cap: float = 1.0, token_cap: int = 1000) -> SpendMeter:
    ledger.now = NOW
    return SpendMeter(ledger, usd_cap, token_cap, clock=lambda: NOW)


def test_cost_uses_cache_multipliers() -> None:
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    # 4 input + 20 output + 0.20 cache read + 4 * 1.25 cache write
    assert cost_usd(OPUS, usage) == pytest.approx(29.20)


def test_haiku_switches_rate_card_over_100k_prompt_tokens() -> None:
    short = cost_usd(HAIKU, Usage(input_tokens=100_000, output_tokens=1000))
    long = cost_usd(HAIKU, Usage(input_tokens=100_001, output_tokens=1000))
    assert short == pytest.approx((100_000 * 0.10 + 1000 * 0.50) / 1e6)
    assert long == pytest.approx((100_001 * 0.50 + 1000 * 2.50) / 1e6)


def test_unknown_model_has_no_price() -> None:
    with pytest.raises(UnpricedModelError):
        cost_usd("claude-imaginary-9", Usage())


async def test_metered_llm_records_cost_and_role() -> None:
    ledger = MemoryLedger()
    llm = MeteredLLM(FakeLLM(), _meter(ledger), Service.ANTHROPIC)

    await llm.complete(_request())

    ((_, record),) = ledger.records
    assert record.operation == Role.CHAT
    assert record.request_id == "req_fake"
    assert record.cost_usd == pytest.approx((100 * 0.10 + 20 * 0.50) / 1e6)


async def test_cap_blocks_the_call_before_it_is_made() -> None:
    ledger = MemoryLedger()
    meter = _meter(ledger, usd_cap=0.50)
    await meter.record(UsageRecord(Service.ANTHROPIC, HAIKU, "chat", Usage(), 0.50))
    inner = FakeLLM()
    llm = MeteredLLM(inner, meter, Service.ANTHROPIC)

    with pytest.raises(BudgetExceededError, match="monthly Anthropic cap"):
        await llm.complete(_request())
    assert inner.requests == []


async def test_spend_from_last_month_does_not_count() -> None:
    ledger = MemoryLedger()
    meter = _meter(ledger, usd_cap=0.50)
    ledger.now = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
    await meter.record(UsageRecord(Service.ANTHROPIC, HAIKU, "chat", Usage(), 5.0))
    ledger.now = NOW

    await meter.check(Service.ANTHROPIC)  # does not raise


async def test_voyage_cap_counts_tokens_not_dollars() -> None:
    ledger = MemoryLedger()
    meter = _meter(ledger, token_cap=1000)
    await meter.record(
        UsageRecord(Service.VOYAGE, "voyage-4", "embeddings", Usage(input_tokens=1000), 0.0)
    )

    await meter.check(Service.ANTHROPIC)  # other services are unaffected
    with pytest.raises(BudgetExceededError, match="Voyage"):
        await meter.check(Service.VOYAGE)


async def test_unpriced_model_never_runs() -> None:
    inner = FakeLLM()
    llm = MeteredLLM(inner, _meter(MemoryLedger()), Service.ANTHROPIC)

    with pytest.raises(UnpricedModelError):
        await llm.complete(_request(model="claude-imaginary-9"))
    assert inner.requests == []


async def test_finished_stream_records_reported_usage() -> None:
    ledger = MemoryLedger()
    llm = MeteredLLM(FakeLLM(text="one two three"), _meter(ledger), Service.ANTHROPIC)

    chunks = [c async for c in llm.stream(_request())]

    assert [c.text for c in chunks if isinstance(c, TextDelta)] == ["one ", "two ", "three "]
    ((_, record),) = ledger.records
    assert record.usage == Usage(input_tokens=100, output_tokens=20)


async def test_abandoned_stream_records_an_estimate() -> None:
    ledger = MemoryLedger()
    llm = MeteredLLM(FakeLLM(text="one two three"), _meter(ledger), Service.ANTHROPIC)

    stream = llm.stream(_request())
    async for _ in stream:
        break  # the client went away after the first delta
    await stream.aclose()
    await llm.flush()

    ((_, record),) = ledger.records
    assert record.usage.estimated
    assert record.usage.input_tokens > 0
    assert record.cost_usd > 0
