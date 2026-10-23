"""order promotions by position

Revision ID: d5fd447361c2
Revises: 33d7e4f03745
Create Date: 2026-10-23 20:02:48.234403

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5fd447361c2"
down_revision: str | Sequence[str] | None = "33d7e4f03745"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Order promotions oldest first. The table is still empty, so the column can be required."""
    op.add_column("promotions", sa.Column("position", sa.Integer(), nullable=False))
    op.create_unique_constraint(op.f("uq_promotions_position"), "promotions", ["position"])


def downgrade() -> None:
    """Drop the promotion order."""
    op.drop_constraint(op.f("uq_promotions_position"), "promotions", type_="unique")
    op.drop_column("promotions", "position")
