"""M5 photo signals: pgvector, check_photos.embedding + HNSW index, web image search tables.

- `CREATE EXTENSION vector` (pgvector, available in the pgvector/pgvector:pg16 image since M0).
- check_photos.embedding vector(512): CLIP ViT-B/32 image embeddings (ED-025), with an HNSW index
  for cosine distance. Hash search uses no index: a full bit_count scan measured faster than a
  BK-tree up to 100k photos (eval/reports/hash_search_bench.md).
- web_image_searches / web_image_search_cache: the photo_reuse_web daily cap and cache (the paid
  provider is OFF by default).

Revision ID: 0009_photo_signals
Revises: 0008_llm_calls
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_photo_signals"
down_revision: str | None = "0008_llm_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("check_photos", sa.Column("embedding", Vector(512), nullable=True))
    op.create_index(
        "ix_check_photos_embedding_hnsw",
        "check_photos",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "web_image_searches",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "called_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("image_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("outcome", sa.Text(), server_default="started", nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('started', 'ok', 'error')", name=op.f("ck_web_image_searches_outcome")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_image_searches")),
    )
    op.create_index("ix_web_image_searches_called_at", "web_image_searches", ["called_at"])
    op.create_table(
        "web_image_search_cache",
        sa.Column("image_sha256", sa.LargeBinary(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("matches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("image_sha256", "provider", name=op.f("pk_web_image_search_cache")),
    )


def downgrade() -> None:
    op.drop_table("web_image_search_cache")
    op.drop_index("ix_web_image_searches_called_at", table_name="web_image_searches")
    op.drop_table("web_image_searches")
    op.drop_index("ix_check_photos_embedding_hnsw", table_name="check_photos")
    op.drop_column("check_photos", "embedding")
    # The vector extension is left installed: other databases objects may come to use it.
