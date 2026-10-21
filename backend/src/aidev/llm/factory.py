from sqlalchemy.ext.asyncio import AsyncEngine

from aidev.config import Settings
from aidev.llm.claude import create_anthropic
from aidev.llm.meter import MeteredLLM, PostgresLedger, Service, SpendMeter


def create_meter(settings: Settings, engine: AsyncEngine) -> SpendMeter:
    return SpendMeter.from_settings(PostgresLedger(engine), settings)


def create_llm(settings: Settings, meter: SpendMeter) -> MeteredLLM:
    """The LLM client the application uses: Claude behind the spend cap."""
    return MeteredLLM(create_anthropic(settings), meter, Service.ANTHROPIC)
