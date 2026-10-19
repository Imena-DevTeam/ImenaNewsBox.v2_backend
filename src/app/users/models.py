"""Users (with their profile fields), roles, and the fixed sub-family and promotion lists."""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base, TimestampMixin

FAMILY_NAME_MAX_LENGTH: Final = 64
DISPLAY_NAME_MAX_LENGTH: Final = 100
EMAIL_MAX_LENGTH: Final = 320


class Role(StrEnum):
    """Roles are cumulative: each one can do everything the roles before it can.

    Check them with `role.at_least(...)` (or the `require_role` dependency), never with `<`
    or `>`: a `StrEnum` compares alphabetically, not by rank.
    """

    MEMBER = "member"
    SOCIAL_MEDIA_MANAGER = "social_media_manager"
    ADMIN = "admin"

    @property
    def rank(self) -> int:
        return _ROLE_RANKS[self]

    def at_least(self, minimum: Role) -> bool:
        return self.rank >= minimum.rank


_ROLE_RANKS: Final = {role: rank for rank, role in enumerate(Role)}


class SubFamily(Base):
    """A fixed list, filled in by a migration once the values are agreed."""

    __tablename__ = "sub_families"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class Promotion(Base):
    """A fixed list, filled in by a migration once the values are agreed."""

    __tablename__ = "promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    # UUIDv7: time-ordered, so new rows land at the end of the primary key index.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    family_name: Mapped[str] = mapped_column(String(FAMILY_NAME_MAX_LENGTH))
    display_name: Mapped[str] = mapped_column(String(DISPLAY_NAME_MAX_LENGTH))
    # Unique ignoring case (see the index below), so a code always reaches one account.
    email: Mapped[str] = mapped_column(String(EMAIL_MAX_LENGTH))
    email_verified_at: Mapped[datetime | None]
    birthday: Mapped[date | None]

    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="role",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=lambda roles: [role.value for role in roles],
        ),
        default=Role.MEMBER,
        server_default=Role.MEMBER.value,
    )
    # Admins only. An admin without one (e.g. imported from v1) can't log in until it's set.
    password_hash: Mapped[str | None] = mapped_column(String(255))

    sub_family_id: Mapped[int] = mapped_column(ForeignKey("sub_families.id"), index=True)
    promotion_id: Mapped[int | None] = mapped_column(ForeignKey("promotions.id"), index=True)

    photo_public_id: Mapped[str | None] = mapped_column(String(255))
    photo_url: Mapped[str | None] = mapped_column(String(1024))

    deactivated_at: Mapped[datetime | None]

    sub_family: Mapped[SubFamily] = relationship(lazy="raise")
    promotion: Mapped[Promotion | None] = relationship(lazy="raise")

    @property
    def is_active(self) -> bool:
        return self.deactivated_at is None

    def __repr__(self) -> str:
        # Only the id: names and emails stay out of logs and tracebacks.
        return f"<User {self.id}>"


# Case-insensitive uniqueness, also covering deactivated users (their name stays reserved).
# Login looks users up by `lower(family_name)`, which uses this index.
Index("uq_users_family_name_lower", func.lower(User.family_name), unique=True)
# One account per address, ignoring case. Deactivated users keep theirs reserved too.
Index("uq_users_email_lower", func.lower(User.email), unique=True)
