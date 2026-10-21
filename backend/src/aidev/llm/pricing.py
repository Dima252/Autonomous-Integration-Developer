"""Prices in USD per million tokens, Anthropic first-party rates (checked 2026-10-21).

Every model the system can call must be listed: the spend meter refuses to record an
unpriced model rather than let it run uncapped.
"""

from dataclasses import dataclass

from aidev.llm.models import HAIKU, OPUS, SONNET
from aidev.llm.types import Usage

# Cache writes are billed as a multiple of the input rate.
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    # Some models switch to a second rate card when the prompt is longer than this.
    long_prompt_over: int | None = None
    long_input: float = 0.0
    long_output: float = 0.0
    long_cache_read: float = 0.0


PRICES: dict[str, Price] = {
    HAIKU: Price(
        input=0.10,
        output=0.50,
        cache_read=0.01,
        long_prompt_over=100_000,
        long_input=0.50,
        long_output=2.50,
        long_cache_read=0.05,
    ),
    SONNET: Price(input=2.00, output=10.00, cache_read=0.20),
    OPUS: Price(input=4.00, output=20.00, cache_read=0.20),
}


class UnpricedModelError(KeyError):
    pass


def cost_usd(model: str, usage: Usage) -> float:
    try:
        price = PRICES[model]
    except KeyError:
        raise UnpricedModelError(
            f"no price for {model!r}; add it to aidev/llm/pricing.py"
        ) from None
    rate_in, rate_out, rate_read = price.input, price.output, price.cache_read
    if price.long_prompt_over is not None and usage.prompt_tokens > price.long_prompt_over:
        rate_in, rate_out, rate_read = price.long_input, price.long_output, price.long_cache_read
    return (
        usage.input_tokens * rate_in
        + usage.cache_write_tokens * rate_in * CACHE_WRITE_5M
        + usage.cache_write_1h_tokens * rate_in * CACHE_WRITE_1H
        + usage.cache_read_tokens * rate_read
        + usage.output_tokens * rate_out
    ) / 1_000_000
