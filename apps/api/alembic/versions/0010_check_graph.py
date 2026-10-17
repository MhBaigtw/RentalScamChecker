"""M6 check graph: photo_links table, contact lookup index.

- photo_links: photo pairs from different checks that match under the M5 photo rule, stored in
  both directions, indexed by (check_id, other_check_id) for the graph queries (ED-026).
- ix_check_contacts_hmac_hash_check_id: "which checks used this contact?" (contact_reuse and the
  graph). The existing unique index starts with check_id, so it can't serve a lookup by hash.

EXPLAIN output for both at 50k checks: docs/database-design.md.

Revision ID: 0010_check_graph
Revises: 0009_photo_signals
Create Date: 2026-10-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_check_graph"
down_revision: str | None = "0009_photo_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_check_contacts_hmac_hash_check_id", "check_contacts", ["hmac_hash", "check_id"]
    )
    op.create_table(
        "photo_links",
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column("other_photo_id", sa.Uuid(), nullable=False),
        sa.Column("check_id", sa.Uuid(), nullable=False),
        sa.Column("other_check_id", sa.Uuid(), nullable=False),
        sa.Column("match_type", sa.Text(), nullable=False),
        sa.Column("phash_distance", sa.SmallInteger(), nullable=True),
        sa.Column("dhash_distance", sa.SmallInteger(), nullable=True),
        sa.Column("cosine", sa.Double(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "match_type IN ('hash', 'embedding')", name=op.f("ck_photo_links_match_type")
        ),
        sa.CheckConstraint(
            "check_id <> other_check_id", name=op.f("ck_photo_links_different_checks")
        ),
        sa.ForeignKeyConstraint(
            ["photo_id"],
            ["check_photos.id"],
            name=op.f("fk_photo_links_photo_id_check_photos"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["other_photo_id"],
            ["check_photos.id"],
            name=op.f("fk_photo_links_other_photo_id_check_photos"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["checks.id"],
            name=op.f("fk_photo_links_check_id_checks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["other_check_id"],
            ["checks.id"],
            name=op.f("fk_photo_links_other_check_id_checks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("photo_id", "other_photo_id", name=op.f("pk_photo_links")),
    )
    op.create_index(
        "ix_photo_links_check_id_other_check_id", "photo_links", ["check_id", "other_check_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_photo_links_check_id_other_check_id", table_name="photo_links")
    op.drop_table("photo_links")
    op.drop_index("ix_check_contacts_hmac_hash_check_id", table_name="check_contacts")
