"""Business logic and data access."""

import logging
import uuid
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_owner_or_admin
from app.auth.exceptions import CodeRecentlySent, TooManyCodes
from app.auth.service import (
    consume_code,
    issue_code,
    mark_email_verified,
    revoke_all_sessions,
)
from app.core.email import CodePurpose, EmailNotConfigured, EmailNotSent, EmailSender
from app.core.media import MediaStore
from app.core.security import hash_password
from app.users.exceptions import (
    AuthorAlreadyLinked,
    CannotChangeOwnRole,
    CannotDeactivateSelf,
    EmailAlreadyVerified,
    EmailTaken,
    FamilyNameTaken,
    NotAnAdmin,
    NotAWriter,
    UnknownPromotion,
    UnknownSubFamily,
    UserNotFound,
)
from app.users.models import Promotion, Role, SubFamily, User
from app.users.schemas import MIN_ADMIN_PASSWORD_LENGTH, ProfileUpdate, UserCreate, UserUpdate

logger = logging.getLogger(__name__)


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


async def get_users(db: AsyncSession, user_ids: Collection[uuid.UUID]) -> list[User]:
    """Active users with these IDs, with their sub-family and promotion, in no set order.
    Unknown or deactivated IDs are left out."""
    if not user_ids:
        return []
    users = await db.scalars(
        _with_lookups(select(User).where(User.id.in_(user_ids), User.deactivated_at.is_(None)))
    )
    return list(users)


async def list_sub_families(db: AsyncSession) -> list[SubFamily]:
    return list(await db.scalars(select(SubFamily).order_by(SubFamily.name)))


async def list_promotions(db: AsyncSession) -> list[Promotion]:
    """Oldest promotion first."""
    return list(await db.scalars(select(Promotion).order_by(Promotion.position)))


# --- Creating and changing users ---

# The unique indexes behind each "taken" error (see `users/models.py`).
_UNIQUE_ERRORS: Final = {
    "uq_users_family_name_lower": FamilyNameTaken,
    "uq_users_email_lower": EmailTaken,
    "uq_users_sanity_author_id": AuthorAlreadyLinked,
}


@asynccontextmanager
async def _unique_fields(db: AsyncSession) -> AsyncIterator[None]:
    """Make user changes inside this block. A duplicate family name or email, caught by the
    database's unique indexes, becomes a 409 instead of a crash.

    The block runs in a savepoint, so a duplicate only undoes these changes. Make no other
    changes before it: entering the savepoint flushes them outside its protection.
    """
    try:
        async with db.begin_nested():
            yield
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        error = _UNIQUE_ERRORS.get(getattr(diag, "constraint_name", None) or "")
        if error is None:
            raise
        raise error() from exc


async def _check_lookups(
    db: AsyncSession, *, sub_family_id: int | None, promotion_id: int | None
) -> None:
    if sub_family_id is not None and await db.get(SubFamily, sub_family_id) is None:
        raise UnknownSubFamily()
    if promotion_id is not None and await db.get(Promotion, promotion_id) is None:
        raise UnknownPromotion()


async def create_user(db: AsyncSession, data: UserCreate) -> User:
    """Register a member (admins only). Commits."""
    await _check_lookups(db, sub_family_id=data.sub_family_id, promotion_id=data.promotion_id)
    user = User(**data.model_dump())
    async with _unique_fields(db):
        db.add(user)
    await db.commit()
    return await get_user(db, user.id)


async def update_user(
    db: AsyncSession,
    actor: User,
    user_id: uuid.UUID,
    changes: UserUpdate,
    sender: EmailSender,
) -> User:
    """Change a user's fields (admins only, deactivated users included). Commits.

    - A new email (not just different capitals) clears `email_verified_at` and sends
      a code to the new address (see `send_email_confirmation`).
    - Demoting an admin removes their password: only admins have one.
    - Demoting someone to a plain member unlinks their Sanity author: only post
      writers have one. Their old posts keep their author.
    - Admins can't change their own role, so the last admin can't lock everyone out.
    """
    user = await get_user(db, user_id, include_deactivated=True)
    fields = changes.model_dump(exclude_unset=True)

    new_role = fields.get("role")
    if new_role is not None and new_role != user.role:
        if user.id == actor.id:
            raise CannotChangeOwnRole()
        if not new_role.at_least(Role.ADMIN):
            fields["password_hash"] = None
        if not new_role.at_least(Role.SOCIAL_MEDIA_MANAGER):
            fields["sanity_author_id"] = None
    new_email = fields.get("email")
    email_changed = new_email is not None and new_email.lower() != user.email.lower()
    if email_changed:
        fields["email_verified_at"] = None
    await _check_lookups(
        db, sub_family_id=fields.get("sub_family_id"), promotion_id=fields.get("promotion_id")
    )

    async with _unique_fields(db):
        for name, value in fields.items():
            setattr(user, name, value)
    await db.commit()
    if email_changed:
        await _try_to_send_confirmation(db, sender, user)
    return await get_user(db, user.id, include_deactivated=True)


async def _try_to_send_confirmation(db: AsyncSession, sender: EmailSender, user: User) -> None:
    # The admin's change is already saved and shouldn't fail because of the email;
    # the member can ask for the code again (`send_email_confirmation`).
    try:
        await issue_code(db, sender, user, CodePurpose.EMAIL_CHANGE, to=user.email)
    except (EmailNotSent, EmailNotConfigured, CodeRecentlySent, TooManyCodes) as exc:
        logger.warning("No confirmation code was sent for user %s: %s", user.id, exc.code)


# --- Confirming an email ---


async def send_email_confirmation(db: AsyncSession, sender: EmailSender, user: User) -> None:
    """Email a code that confirms the user's current address. Commits.

    Same limits as login codes: one a minute, ten a day.
    """
    if user.email_verified_at is not None:
        raise EmailAlreadyVerified()
    await issue_code(db, sender, user, CodePurpose.EMAIL_CHANGE, to=user.email)


async def confirm_email(db: AsyncSession, user: User, code: str) -> User:
    """Check a confirmation code and mark the email verified. Commits. Raises
    `InvalidCode`."""
    await consume_code(db, user, CodePurpose.EMAIL_CHANGE, code)
    mark_email_verified(user)
    await db.commit()
    return await get_user(db, user.id)


async def update_profile(db: AsyncSession, user: User, changes: ProfileUpdate) -> User:
    """A member changing their own display name, birthday or bio. Commits."""
    for name, value in changes.model_dump(exclude_unset=True).items():
        setattr(user, name, value)
    await db.commit()
    return await get_user(db, user.id)


# --- Photos ---


async def set_photo(
    db: AsyncSession, actor: User, user_id: uuid.UUID, data: bytes, media: MediaStore
) -> tuple[User, str | None]:
    """Upload a checked image as the user's photo (their own, or anyone's for admins).
    Commits.

    Returns the user and the public ID of the photo it replaced, for the caller to delete in
    the background. The new photo is saved first, so a failed upload never loses the old one.
    """
    require_owner_or_admin(actor, user_id)
    user = await get_user(db, user_id, include_deactivated=actor.role.at_least(Role.ADMIN))
    uploaded = await media.upload_profile_photo(data)
    replaced = user.photo_public_id
    user.photo_public_id, user.photo_url = uploaded.public_id, uploaded.url
    try:
        await db.commit()
    except Exception:
        # Not saved, so the new upload would only be an orphan.
        await media.delete_image(uploaded.public_id)
        raise
    return await get_user(db, user.id, include_deactivated=True), replaced


async def remove_photo(
    db: AsyncSession, actor: User, user_id: uuid.UUID
) -> tuple[User, str | None]:
    """Remove the user's photo (their own, or anyone's for admins). Commits. Returns the
    user and the removed photo's public ID, for the caller to delete in the background."""
    require_owner_or_admin(actor, user_id)
    user = await get_user(db, user_id, include_deactivated=actor.role.at_least(Role.ADMIN))
    removed = user.photo_public_id
    if removed is not None or user.photo_url is not None:
        user.photo_public_id = user.photo_url = None
        await db.commit()
    return await get_user(db, user.id, include_deactivated=True), removed


async def deactivate_user(db: AsyncSession, actor: User, user_id: uuid.UUID) -> User:
    """Soft-delete a user: block logins and sign them out everywhere, at once. Commits.

    Their family name and email stay reserved, and their wishes are kept. Doing it again
    changes nothing. Admins can't deactivate themselves.
    """
    user = await get_user(db, user_id, include_deactivated=True)
    if user.id == actor.id:
        raise CannotDeactivateSelf()
    if user.deactivated_at is None:
        user.deactivated_at = datetime.now(UTC)
        await revoke_all_sessions(db, user.id)
        await db.commit()
    return await get_user(db, user.id, include_deactivated=True)


async def reactivate_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    """Let a deactivated user log in again. Their old sessions stay revoked. Commits."""
    user = await get_user(db, user_id, include_deactivated=True)
    if user.deactivated_at is not None:
        user.deactivated_at = None
        await db.commit()
    return await get_user(db, user.id, include_deactivated=True)


async def set_admin_password(db: AsyncSession, user_id: uuid.UUID, password: str) -> None:
    """Give an admin a new password (admins only, their own included). Commits.

    For admins who have none yet (promoted or imported from v1) or who forgot theirs. Their
    sessions stay as they are: the password is only checked when a new device logs in.
    """
    user = await get_user(db, user_id, include_deactivated=True)
    if not user.role.at_least(Role.ADMIN):
        raise NotAnAdmin()
    user.password_hash = await hash_password(password)
    await db.commit()


async def set_sanity_author(db: AsyncSession, user: User, author_id: str | None) -> User:
    """Credit the user's blog posts to this Sanity author (None unlinks). Commits.

    Doesn't check that the author exists: `posts.service.link_author` does, before calling
    this. Raises `NotAWriter` for plain members and `AuthorAlreadyLinked` if another
    user has it.
    """
    if author_id is not None and not user.role.at_least(Role.SOCIAL_MEDIA_MANAGER):
        raise NotAWriter()
    async with _unique_fields(db):
        user.sanity_author_id = author_id
    await db.commit()
    return await get_user(db, user.id, include_deactivated=True)


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
