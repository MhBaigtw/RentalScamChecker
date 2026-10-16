"""checks.unit_type: whole_unit (default) | room | basement.

price_vs_market skips rooms (CMHC averages cover whole units only) and adds a caveat for
basement apartments (ED-021). Existing checks become whole_unit, the assumption made before.

Revision ID: 0006_check_unit_type
Revises: 0005_reference_rents_geocoding
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_check_unit_type"
down_revision: str | None = "0005_reference_rents_geocoding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "checks",
        sa.Column("unit_type", sa.Text(), server_default="whole_unit", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_checks_unit_type"), "checks", "unit_type IN ('whole_unit', 'room', 'basement')"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_checks_unit_type"), "checks", type_="check")
    op.drop_column("checks", "unit_type")
