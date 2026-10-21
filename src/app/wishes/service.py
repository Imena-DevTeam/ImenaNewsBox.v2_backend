"""Business logic and data access."""

import calendar
import uuid
from datetime import UTC, date, datetime
from typing import Final

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.dependencies import require_owner_or_admin
from app.users import service as users_service
from app.users.models import User
from app.wishes.exceptions import (
    BirthdayNotSoon,
    CannotWishYourself,
    WishAlreadySent,
    WishNotFound,
)
from app.wishes.models import Wish
from app.wishes.schemas import WishCreate, WishUpdate

# Wishes are open, and "upcoming birthdays" look ahead, from the birthday itself to this many
# days before it.
WISH_WINDOW_DAYS: Final = 30


# --- Birthday dates ---


def today() -> date:
    """Today's date in UTC, the day every birthday rule uses."""
    return datetime.now(UTC).date()


def birthday_in(birthday: date, year: int) -> date:
    """The date the birthday falls on in `year`. 29 February is kept on 28 February in years
    without one."""
    if (birthday.month, birthday.day) == (2, 29) and not calendar.isleap(year):
        return date(year, 2, 28)
    return birthday.replace(year=year)


def next_birthday(birthday: date, on: date) -> date:
    """The birthday's next date from `on`: `on` itself when it's the day."""
    this_year = birthday_in(birthday, on.year)
    return this_year if this_year >= on else birthday_in(birthday, on.year + 1)


def days_until_birthday(birthday: date, on: date) -> int:
    return (next_birthday(birthday, on) - on).days


def is_wishable(birthday: date, on: date) -> bool:
    """Whether the birthday is today or within the next `WISH_WINDOW_DAYS` days."""
    return days_until_birthday(birthday, on) <= WISH_WINDOW_DAYS


# --- Wishes ---


def _with_people(statement: Select[Wish]) -> Select[Wish]:
    # `WishRead` shows sender and recipient with their sub-family and promotion.
    # populate_existing also fills them in for users this session already holds.
    return statement.options(
        selectinload(Wish.sender).selectinload(User.sub_family),
        selectinload(Wish.sender).selectinload(User.promotion),
        selectinload(Wish.recipient).selectinload(User.sub_family),
        selectinload(Wish.recipient).selectinload(User.promotion),
    ).execution_options(populate_existing=True)


async def get_wish(db: AsyncSession, wish_id: uuid.UUID) -> Wish:
    """One wish with its sender and recipient. Raises `WishNotFound`."""
    wish = await db.scalar(_with_people(select(Wish).where(Wish.id == wish_id)))
    if wish is None:
        raise WishNotFound()
    return wish


async def send_wish(db: AsyncSession, sender: User, data: WishCreate, *, on: date) -> Wish:
    """Wish an active member a happy birthday, for their next birthday. Commits.

    Only when that birthday is today or within 30 days of `on`, and once per birthday.
    """
    if data.recipient_id == sender.id:
        raise CannotWishYourself()
    recipient = await users_service.get_user(db, data.recipient_id)
    if recipient.birthday is None or not is_wishable(recipient.birthday, on):
        raise BirthdayNotSoon()

    wish = Wish(
        sender_id=sender.id,
        recipient_id=recipient.id,
        year=next_birthday(recipient.birthday, on).year,
        message=data.message,
    )
    # The unique constraint, not a check first, so two requests at once can't both succeed.
    try:
        async with db.begin_nested():
            db.add(wish)
    except IntegrityError as exc:
        diag = getattr(exc.orig, "diag", None)
        if getattr(diag, "constraint_name", None) == "uq_wishes_sender_recipient_year":
            raise WishAlreadySent() from exc
        raise
    await db.commit()
    return await get_wish(db, wish.id)


async def update_wish(
    db: AsyncSession, actor: User, wish_id: uuid.UUID, changes: WishUpdate
) -> Wish:
    """Change a wish's message. Only its sender may, not even admins. Commits."""
    wish = await get_wish(db, wish_id)
    require_owner_or_admin(actor, wish.sender_id, allow_admin=False)
    wish.message = changes.message
    await db.commit()
    return await get_wish(db, wish.id)


async def delete_wish(db: AsyncSession, actor: User, wish_id: uuid.UUID) -> None:
    """Delete a wish: its sender, or any admin. Commits."""
    wish = await db.get(Wish, wish_id)
    if wish is None:
        raise WishNotFound()
    require_owner_or_admin(actor, wish.sender_id)
    await db.delete(wish)
    await db.commit()
