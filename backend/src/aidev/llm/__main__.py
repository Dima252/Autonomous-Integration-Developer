"""Spend CLI.

uv run python -m aidev.llm usage     # month-to-date spend against the caps
"""

import argparse
import asyncio

from aidev.config import Settings, get_settings
from aidev.infra import postgres
from aidev.llm.factory import create_meter
from aidev.llm.meter import Service


async def _usage(settings: Settings) -> None:
    engine = postgres.create_engine(settings.database_url)
    try:
        meter = create_meter(settings, engine)
        claude = await meter.month_to_date(Service.ANTHROPIC)
        voyage = await meter.month_to_date(Service.VOYAGE)
    finally:
        await engine.dispose()
    print(
        f"anthropic  ${claude.cost_usd:.4f} of ${meter.anthropic_usd_cap:.2f} this month "
        f"({claude.tokens:,} tokens)"
    )
    print(f"voyage     {voyage.tokens:,} of {meter.voyage_token_cap:,} tokens this month")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m aidev.llm")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("usage", help="month-to-date spend against the monthly caps")
    parser.parse_args()
    asyncio.run(_usage(get_settings()))


if __name__ == "__main__":
    main()
