"""doc chunks

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21 13:33:38.988856
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Also created by infra/postgres/init for local Docker; here for every other database.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "doc_chunks",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("doc_key", sa.Text(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("breadcrumb", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("embedding_model", sa.Text(), nullable=True),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', breadcrumb), 'A') || "
                "setweight(to_tsvector('english', content), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doc_chunks")),
        sa.UniqueConstraint(
            "doc_key", "chunk_index", name=op.f("uq_doc_chunks_doc_key_chunk_index")
        ),
    )
    op.create_index(
        "ix_doc_chunks_embedding",
        "doc_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        op.f("ix_doc_chunks_provider_doc_type"),
        "doc_chunks",
        ["provider", "doc_type"],
        unique=False,
    )
    op.create_index(
        "ix_doc_chunks_search_vector",
        "doc_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_doc_chunks_search_vector", table_name="doc_chunks", postgresql_using="gin")
    op.drop_index(op.f("ix_doc_chunks_provider_doc_type"), table_name="doc_chunks")
    op.drop_index(
        "ix_doc_chunks_embedding",
        table_name="doc_chunks",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_table("doc_chunks")
