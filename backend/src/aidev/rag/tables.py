from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Column,
    Computed,
    DateTime,
    Index,
    Integer,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR

from aidev.infra.db import metadata
from aidev.rag.embeddings import EMBEDDING_DIMENSIONS

# One row per chunk. Both search surfaces live here: `search_vector` for full-text
# search (breadcrumb weighted above body) and `embedding` for vector search. Rows are
# written before they're embedded, so full-text search works immediately and embedding
# can catch up in resumable batches.
doc_chunks = Table(
    "doc_chunks",
    metadata,
    Column("id", BigInteger, primary_key=True),
    Column("doc_key", Text, nullable=False),
    Column("chunk_index", Integer, nullable=False),
    Column("provider", Text, nullable=False),
    Column("doc_type", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("url", Text),
    Column("breadcrumb", Text, nullable=False),
    Column("content", Text, nullable=False),
    # Hash of exactly the text that gets embedded; identical text reuses its vector.
    Column("content_hash", Text, nullable=False),
    Column("embedding", Vector(EMBEDDING_DIMENSIONS)),
    Column("embedding_model", Text),
    Column(
        "search_vector",
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', breadcrumb), 'A') || "
            "setweight(to_tsvector('english', content), 'B')",
            persisted=True,
        ),
        nullable=False,
    ),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("doc_key", "chunk_index"),
    Index(None, "provider", "doc_type"),
    Index("ix_doc_chunks_search_vector", "search_vector", postgresql_using="gin"),
    Index(
        "ix_doc_chunks_embedding",
        "embedding",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    ),
)
