import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection

# Importing the table modules registers their tables on the shared metadata.
import aidev.catalog.tables
import aidev.rag.tables  # noqa: F401
from aidev.config import get_settings
from aidev.infra import postgres
from aidev.infra.db import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade head --sql`)."""
    context.configure(
        url=get_settings().database_url,
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = postgres.create_engine(get_settings().database_url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
