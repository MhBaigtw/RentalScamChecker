"""M3: reference_rents by geography level; geocode_cache; rate_limits.

reference_rents was created in 0002 with a single `cma` column but never loaded. It is dropped and
re-created with geography levels (CMA or municipality/CSD), the CMHC row each value came from,
the rent basis (vacant units / all units), CMHC reliability codes, the survey month and the source
URL (ED-021). The upgrade refuses to run if the old table has any rows.

Revision ID: 0005_reference_rents_geocoding
Revises: 0004_job_queue_indexes
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_reference_rents_geocoding"
down_revision: str | None = "0004_job_queue_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM reference_rents")).scalar_one()
    if rows:
        raise RuntimeError(f"reference_rents has {rows} rows; this migration expects it empty")
    op.drop_table("reference_rents")
    op.create_table(
        "reference_rents",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("geography_type", sa.Text(), nullable=False),
        sa.Column("geography_code", sa.Text(), nullable=False),
        sa.Column("geography_name", sa.Text(), nullable=False),
        sa.Column("cma_name", sa.Text(), nullable=False),
        sa.Column("source_geography", sa.Text(), nullable=False),
        sa.Column("rent_basis", sa.Text(), nullable=False),
        sa.Column("bedrooms", sa.SmallInteger(), nullable=False),
        sa.Column("avg_rent_cents", sa.Integer(), nullable=True),
        sa.Column("reliability", sa.Text(), nullable=True),
        sa.Column("survey_year", sa.SmallInteger(), nullable=False),
        sa.Column("survey_month", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "geography_type IN ('cma', 'csd')", name=op.f("ck_reference_rents_geography_type")
        ),
        sa.CheckConstraint(
            "rent_basis IN ('vacant_units', 'all_units')",
            name=op.f("ck_reference_rents_rent_basis"),
        ),
        sa.CheckConstraint(
            "bedrooms BETWEEN 0 AND 3", name=op.f("ck_reference_rents_bedrooms_range")
        ),
        sa.CheckConstraint(
            "survey_month BETWEEN 1 AND 12", name=op.f("ck_reference_rents_survey_month_range")
        ),
        sa.CheckConstraint(
            "reliability IN ('a', 'b', 'c', 'd')", name=op.f("ck_reference_rents_reliability")
        ),
        sa.CheckConstraint(
            "(avg_rent_cents IS NULL) = (reliability IS NULL)",
            name=op.f("ck_reference_rents_suppressed_consistently"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_rents")),
        sa.UniqueConstraint(
            "geography_type",
            "geography_code",
            "rent_basis",
            "bedrooms",
            "survey_year",
            "survey_month",
            name="uq_reference_rents_geography_basis_bedrooms_survey",
        ),
    )
    op.create_table(
        "geocode_cache",
        sa.Column("query_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("query_key", "provider", name=op.f("pk_geocode_cache")),
    )
    op.create_table(
        "rate_limits",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("last_request_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_rate_limits")),
    )


def downgrade() -> None:
    op.drop_table("rate_limits")
    op.drop_table("geocode_cache")
    op.drop_table("reference_rents")
    op.create_table(
        "reference_rents",
        sa.Column("id", sa.Integer(), sa.Identity(always=False), nullable=False),
        sa.Column("cma", sa.Text(), nullable=False),
        sa.Column("bedrooms", sa.SmallInteger(), nullable=False),
        sa.Column("avg_rent_cents", sa.Integer(), nullable=False),
        sa.Column("survey_year", sa.SmallInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "bedrooms BETWEEN 0 AND 3", name=op.f("ck_reference_rents_bedrooms_range")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_rents")),
        sa.UniqueConstraint(
            "cma",
            "bedrooms",
            "survey_year",
            name="uq_reference_rents_cma_bedrooms_survey_year",
        ),
    )
