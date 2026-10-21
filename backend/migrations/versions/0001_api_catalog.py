"""api catalog

Revision ID: 0001
Revises:
Create Date: 2026-10-21 13:03:14.948044
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_specs",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("api_version", sa.Text(), nullable=False),
        sa.Column("spec_format", sa.Text(), nullable=False),
        sa.Column("base_urls", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_specs")),
        sa.UniqueConstraint("provider", "name", name=op.f("uq_api_specs_provider_name")),
    )
    op.create_table(
        "api_operations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("spec_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("op_id", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("path_regex", sa.Text(), nullable=False),
        sa.Column("path_param_count", sa.Integer(), nullable=False),
        sa.Column("deprecated", sa.Boolean(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["spec_id"],
            ["api_specs.id"],
            name=op.f("fk_api_operations_spec_id_api_specs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_operations")),
        sa.UniqueConstraint("spec_id", "op_id", name=op.f("uq_api_operations_spec_id_op_id")),
    )
    op.create_index(
        op.f("ix_api_operations_provider_method"),
        "api_operations",
        ["provider", "method"],
        unique=False,
    )
    op.create_index(
        op.f("ix_api_operations_provider_op_id"),
        "api_operations",
        ["provider", "op_id"],
        unique=False,
    )
    op.create_table(
        "api_schemas",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("spec_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["spec_id"],
            ["api_specs.id"],
            name=op.f("fk_api_schemas_spec_id_api_specs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_schemas")),
        sa.UniqueConstraint("spec_id", "name", name=op.f("uq_api_schemas_spec_id_name")),
    )
    op.create_index(
        op.f("ix_api_schemas_provider_name"), "api_schemas", ["provider", "name"], unique=False
    )
    op.create_table(
        "api_webhook_events",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("spec_id", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["spec_id"],
            ["api_specs.id"],
            name=op.f("fk_api_webhook_events_spec_id_api_specs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_webhook_events")),
        sa.UniqueConstraint("spec_id", "name", name=op.f("uq_api_webhook_events_spec_id_name")),
    )
    op.create_index(
        op.f("ix_api_webhook_events_provider_name"),
        "api_webhook_events",
        ["provider", "name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_webhook_events_provider_name"), table_name="api_webhook_events")
    op.drop_table("api_webhook_events")
    op.drop_index(op.f("ix_api_schemas_provider_name"), table_name="api_schemas")
    op.drop_table("api_schemas")
    op.drop_index(op.f("ix_api_operations_provider_op_id"), table_name="api_operations")
    op.drop_index(op.f("ix_api_operations_provider_method"), table_name="api_operations")
    op.drop_table("api_operations")
    op.drop_table("api_specs")
