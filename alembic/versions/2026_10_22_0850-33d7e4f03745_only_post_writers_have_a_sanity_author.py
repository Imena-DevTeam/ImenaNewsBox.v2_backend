"""only post writers have a sanity author

Revision ID: 33d7e4f03745
Revises: 611b766b660f
Create Date: 2026-10-22 08:50:51.122562

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "33d7e4f03745"
down_revision: str | Sequence[str] | None = "611b766b660f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow a Sanity author only for social media managers and admins."""
    # Members who somehow have one lose it first, so the constraint can be added.
    op.execute(
        "UPDATE users SET sanity_author_id = NULL "
        "WHERE role NOT IN ('social_media_manager', 'admin')"
    )
    op.create_check_constraint(
        op.f("ck_users_sanity_author_role"),
        "users",
        "sanity_author_id IS NULL OR role IN ('social_media_manager', 'admin')",
    )


def downgrade() -> None:
    """Allow a Sanity author for any user again."""
    op.drop_constraint(op.f("ck_users_sanity_author_role"), "users", type_="check")
