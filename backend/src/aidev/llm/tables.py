from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    func,
)

from aidev.infra.db import metadata

# One row per paid API call (LLM and embedding/rerank). The spend caps sum this month's
# rows, so every process (API, worker, CLIs) shares one budget.
usage_ledger = Table(
    "usage_ledger",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("service", Text, nullable=False),  # anthropic | voyage
    Column("model", Text, nullable=False),
    Column("operation", Text, nullable=False),  # role, or the endpoint for Voyage
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("cache_read_tokens", Integer, nullable=False),
    Column("cache_write_tokens", Integer, nullable=False),
    Column("cost_usd", Numeric(12, 6), nullable=False),
    Column("estimated", Boolean, nullable=False),
    Column("request_id", Text),
    Index(None, "service", "created_at"),
)
