"""Catalog tables. Columns that lookups filter on are real columns; the full model is
kept as JSONB in `data` and round-trips through the Pydantic models in `models.py`."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from aidev.infra.db import metadata

# One row per ingested spec document. A provider can have several (HubSpot publishes
# one spec per product area). Re-ingesting an unchanged document is a no-op because the
# content hash matches; a changed one replaces its rows.
api_specs = Table(
    "api_specs",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("provider", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("source_url", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("api_version", Text, nullable=False),
    Column("spec_format", Text, nullable=False),
    Column("base_urls", ARRAY(Text), nullable=False),
    Column("ingested_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("provider", "name"),
)


def _spec_id() -> Column[int]:
    return Column(
        "spec_id", BigInteger, ForeignKey("api_specs.id", ondelete="CASCADE"), nullable=False
    )


api_operations = Table(
    "api_operations",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _spec_id(),
    Column("provider", Text, nullable=False),
    Column("op_id", Text, nullable=False),
    Column("method", Text, nullable=False),
    Column("path", Text, nullable=False),
    # Matches concrete request paths to this operation (used by the grounding check).
    Column("path_regex", Text, nullable=False),
    Column("path_param_count", Integer, nullable=False),
    Column("deprecated", Boolean, nullable=False),
    Column("data", JSONB, nullable=False),
    UniqueConstraint("spec_id", "op_id"),
    Index(None, "provider", "op_id"),
    Index(None, "provider", "method"),
)

api_webhook_events = Table(
    "api_webhook_events",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _spec_id(),
    Column("provider", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("data", JSONB, nullable=False),
    UniqueConstraint("spec_id", "name"),
    Index(None, "provider", "name"),
)

api_schemas = Table(
    "api_schemas",
    metadata,
    Column("id", BigInteger, primary_key=True),
    _spec_id(),
    Column("provider", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("data", JSONB, nullable=False),
    UniqueConstraint("spec_id", "name"),
    Index(None, "provider", "name"),
)
