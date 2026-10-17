"""M7 scoring: checks.independent_risk_level and checks.risk_details.

- independent_risk_level: the level from a check's own evidence, without results that depend on
  other checks' risk. Linked checks read this one, so risk can't cascade through the graph
  (ED-027). Same allowed values as risk_level.
- risk_details (jsonb): contributing and missing signal names, and whether the graph cap applied.

Revision ID: 0011_scoring
Revises: 0010_check_graph
Create Date: 2026-10-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_scoring"
down_revision: str | None = "0010_check_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("checks", sa.Column("independent_risk_level", sa.Text(), nullable=True))
    op.add_column(
        "checks",
        sa.Column("risk_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_check_constraint(
        "independent_risk_level",
        "checks",
        "independent_risk_level IN ('low', 'some', 'high', 'incomplete')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_checks_independent_risk_level"), "checks", type_="check")
    op.drop_column("checks", "risk_details")
    op.drop_column("checks", "independent_risk_level")
