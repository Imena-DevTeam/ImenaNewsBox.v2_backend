"""Business logic and data access."""

import uuid
from enum import StrEnum
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import hash_password
from app.users.exceptions import UserNotFound
from app.users.models import Promotion, Role, SubFamily, User

MIN_ADMIN_PASSWORD_LENGTH: Final = 12


# --- Reading ---


def _with_lookups(statement: Select[User]) -> Select[User]:
    # Read schemas include the sub-family and promotion. populate_existing also fills them
    # in for users this session already holds without them.
    return statement.options(
        selectinload(User.sub_family), selectinload(User.promotion)
    ).execution_options(populate_existing=True)


async def list_users(
    db: AsyncSession, *, offset: int, limit: int, include_deactivated: bool = False
) -> tuple[list[User], int]:
    """Users in alphabetical order of display name; deactivated ones only if asked."""
    conditions = [] if include_deactivated else [User.deactivated_at.is_(None)]
    total = await db.scalar(select(func.count()).select_from(User).where(*conditions))
    users = await db.scalars(
        _with_lookups(
            select(User)
            .where(*conditions)
            .order_by(func.lower(User.display_name), User.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return list(users), total or 0


async def get_user(
    db: AsyncSession, user_id: uuid.UUID, *, include_deactivated: bool = False
) -> User:
    """One user with their sub-family and promotion. Raises `UserNotFound`."""
    conditions = [] if include_deactivated else [User.deactivated_at.is_(None)]
    user = await db.scalar(_with_lookups(select(User).where(User.id == user_id, *conditions)))
    if user is None:
        raise UserNotFound()
    return user


async def list_sub_families(db: AsyncSession) -> list[SubFamily]:
    return list(await db.scalars(select(SubFamily).order_by(SubFamily.name)))


async def list_promotions(db: AsyncSession) -> list[Promotion]:
    return list(await db.scalars(select(Promotion).order_by(Promotion.name)))


# --- First admin ---


class FirstAdminError(Exception):
    """The first admin can't be seeded as asked. The message says why (never the email)."""


class SeedResult(StrEnum):
    CREATED = "created"
    ALREADY_EXISTS = "already_exists"


async def seed_first_admin(
    db: AsyncSession, *, family_name: str, email: str, password: str, sub_family: str
) -> SeedResult:
    """Create the first admin, the only user not created by another admin. Commits.

    Safe to run again: if that admin already exists, nothing changes (the password isn't
    reset). It refuses, instead of guessing, when the family name or email belongs to
    someone else, or when the sub-family doesn't exist (it never creates one).
    """
    family_name, email = family_name.strip(), email.strip()
    if not family_name or not sub_family:
        raise FirstAdminError("Set FIRST_ADMIN_FAMILY_NAME and FIRST_ADMIN_SUB_FAMILY.")
    if "@" not in email:
        raise FirstAdminError("FIRST_ADMIN_EMAIL must be an email address.")
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        raise FirstAdminError(
            f"FIRST_ADMIN_PASSWORD must be at least {MIN_ADMIN_PASSWORD_LENGTH} characters."
        )

    existing = await db.scalar(
        select(User).where(func.lower(User.family_name) == func.lower(family_name))
    )
    if existing is not None:
        same_admin = (
            existing.role.at_least(Role.ADMIN)
            and existing.is_active
            and existing.email.lower() == email.lower()
        )
        if same_admin:
            return SeedResult.ALREADY_EXISTS
        raise FirstAdminError(
            f"A user with the family name {family_name!r} already exists and isn't this "
            "active admin with this email. Change them in the app instead."
        )
    if await db.scalar(select(User.id).where(func.lower(User.email) == func.lower(email))):
        raise FirstAdminError("FIRST_ADMIN_EMAIL already belongs to another user.")
    sub_family_row = await db.scalar(select(SubFamily).where(SubFamily.name == sub_family))
    if sub_family_row is None:
        raise FirstAdminError(
            f"There's no sub-family named {sub_family!r}. Sub-families come from a migration; "
            "use one of their exact names."
        )

    db.add(
        User(
            family_name=family_name,
            display_name=family_name,
            email=email,
            sub_family_id=sub_family_row.id,
            role=Role.ADMIN,
            password_hash=await hash_password(password),
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        # Someone took the family name or email between the checks and the insert.
        await db.rollback()
        raise FirstAdminError("The family name or email was just taken. Run it again.") from exc
    return SeedResult.CREATED
