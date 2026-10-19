"""create users, sub_families and promotions

Revision ID: 68bbcd61f857
Revises:
Create Date: 2026-10-19 14:45:19.476351

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "68bbcd61f857"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the users table and its two fixed lookup tables (empty for now)."""
    op.create_table(
        "promotions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promotions")),
        sa.UniqueConstraint("name", name=op.f("uq_promotions_name")),
    )
    op.create_table(
        "sub_families",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sub_families")),
        sa.UniqueConstraint("name", name=op.f("uq_sub_families_name")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("family_name", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("birthday", sa.Date(), nullable=True),
        sa.Column(
            "role",
            sa.Enum(
                "member",
                "social_media_manager",
                "admin",
                name="role",
                native_enum=False,
                create_constraint=False,
                length=32,
            ),
            server_default="member",
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("sub_family_id", sa.Integer(), nullable=False),
        sa.Column("promotion_id", sa.Integer(), nullable=True),
        sa.Column("photo_public_id", sa.String(length=255), nullable=True),
        sa.Column("photo_url", sa.String(length=1024), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('member', 'social_media_manager', 'admin')", name=op.f("ck_users_role")
        ),
        sa.ForeignKeyConstraint(
            ["promotion_id"], ["promotions.id"], name=op.f("fk_users_promotion_id_promotions")
        ),
        sa.ForeignKeyConstraint(
            ["sub_family_id"], ["sub_families.id"], name=op.f("fk_users_sub_family_id_sub_families")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_promotion_id"), "users", ["promotion_id"], unique=False)
    op.create_index(op.f("ix_users_sub_family_id"), "users", ["sub_family_id"], unique=False)
    op.create_index(
        "uq_users_family_name_lower",
        "users",
        [sa.literal_column("lower(family_name)")],
        unique=True,
    )
    op.create_index(
        "uq_users_email_lower",
        "users",
        [sa.literal_column("lower(email)")],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_users_email_lower", table_name="users")
    op.drop_index("uq_users_family_name_lower", table_name="users")
    op.drop_index(op.f("ix_users_sub_family_id"), table_name="users")
    op.drop_index(op.f("ix_users_promotion_id"), table_name="users")
    op.drop_table("users")
    op.drop_table("sub_families")
    op.drop_table("promotions")
