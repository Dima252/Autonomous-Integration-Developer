from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Table,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from aidev.infra.db import metadata

conversations = Table(
    "conversations",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

messages = Table(
    "messages",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column(
        "conversation_id",
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("role", Text, nullable=False),  # user | assistant
    Column("content", Text, nullable=False),
    Column("intent", Text),  # the route taken, on user messages
    # Route decision, citations, cost and latency: what the UI shows next to a message.
    Column("meta", JSONB, nullable=False, server_default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "conversation_id", "id"),
)

# An integration run. Phase 4 adds the agent graph that executes it; its LangGraph
# checkpoints are keyed by this id.
runs = Table(
    "runs",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column(
        "conversation_id",
        Uuid,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("request", Text, nullable=False),
    Column("systems", JSONB, nullable=False, server_default="[]"),
    Column("status", Text, nullable=False),  # queued | running | completed | failed
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Index(None, "conversation_id"),
)
