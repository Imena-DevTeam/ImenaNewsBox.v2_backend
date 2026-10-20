"""Business logic and data access."""

from enum import StrEnum
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.users.models import Role, SubFamily, User

MIN_ADMIN_PASSWORD_LENGTH: Final = 12


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
