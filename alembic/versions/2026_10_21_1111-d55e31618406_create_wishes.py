"""create wishes

Revision ID: d55e31618406
Revises: e970735cb726
Create Date: 2026-10-21 11:11:03.865338

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d55e31618406"
down_revision: str | Sequence[str] | None = "e970735cb726"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create birthday wishes (one per sender, recipient and year)."""
    op.create_table(
        "wishes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("message", sa.String(length=2000), nullable=False),
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
        sa.CheckConstraint("sender_id <> recipient_id", name=op.f("ck_wishes_not_self")),
        sa.ForeignKeyConstraint(
            ["recipient_id"], ["users.id"], name=op.f("fk_wishes_recipient_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["sender_id"], ["users.id"], name=op.f("fk_wishes_sender_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wishes")),
        sa.UniqueConstraint(
            "sender_id", "recipient_id", "year", name="uq_wishes_sender_recipient_year"
        ),
    )
    op.create_index(op.f("ix_wishes_recipient_id"), "wishes", ["recipient_id"], unique=False)


def downgrade() -> None:
    """Drop birthday wishes."""
    op.drop_index(op.f("ix_wishes_recipient_id"), table_name="wishes")
    op.drop_table("wishes")
