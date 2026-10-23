"""seed sub-families and promotions

Revision ID: 7ed5c8f35e90
Revises: d5fd447361c2
Create Date: 2026-10-23 20:06:48.444496

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7ed5c8f35e90"
down_revision: str | Sequence[str] | None = "d5fd447361c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The lists as the family gave them (2026-10-23), spelled exactly as they should appear.
SUB_FAMILIES = ("HOPE", "LIGHT", "WIHOGORA")
# Oldest first: IBIMANUKA have graduated, NKONGI are in year 3, SHEJA_NGABO in year 2 and
# IBIHAME in year 1. Each year's new promotion is added by a later migration, at the end.
PROMOTIONS = ("IBIMANUKA", "NKONGI", "SHEJA_NGABO", "IBIHAME")


def upgrade() -> None:
    """Add the sub-families and promotions. Rows that already exist are left alone."""
    connection = op.get_bind()
    for name in SUB_FAMILIES:
        connection.execute(
            sa.text("INSERT INTO sub_families (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"),
            {"name": name},
        )
    for position, name in enumerate(PROMOTIONS, start=1):
        connection.execute(
            sa.text(
                "INSERT INTO promotions (name, position) VALUES (:name, :position) "
                "ON CONFLICT DO NOTHING"
            ),
            {"name": name, "position": position},
        )


def downgrade() -> None:
    """Remove those rows again, except any that a user still belongs to."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM promotions WHERE name = ANY(:names) "
            "AND NOT EXISTS (SELECT 1 FROM users WHERE users.promotion_id = promotions.id)"
        ),
        {"names": list(PROMOTIONS)},
    )
    connection.execute(
        sa.text(
            "DELETE FROM sub_families WHERE name = ANY(:names) "
            "AND NOT EXISTS (SELECT 1 FROM users WHERE users.sub_family_id = sub_families.id)"
        ),
        {"names": list(SUB_FAMILIES)},
    )
