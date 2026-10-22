"""add users sanity_author_id

Revision ID: 611b766b660f
Revises: d55e31618406
Create Date: 2026-10-22 08:25:11.382208

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "611b766b660f"
down_revision: str | Sequence[str] | None = "d55e31618406"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Link users to the Sanity author their blog posts are credited to."""
    op.add_column("users", sa.Column("sanity_author_id", sa.String(length=128), nullable=True))
    op.create_unique_constraint(op.f("uq_users_sanity_author_id"), "users", ["sanity_author_id"])


def downgrade() -> None:
    """Drop the link to Sanity authors."""
    op.drop_constraint(op.f("uq_users_sanity_author_id"), "users", type_="unique")
    op.drop_column("users", "sanity_author_id")
