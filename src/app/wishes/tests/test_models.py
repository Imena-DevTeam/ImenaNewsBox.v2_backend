from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User
from app.wishes.models import Wish

MakeUser = Callable[..., Awaitable[User]]


async def test_one_wish_per_sender_recipient_and_year(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    sender, recipient = await make_user(), await make_user()
    db_session.add(Wish(sender_id=sender.id, recipient_id=recipient.id, year=2026, message="1"))
    await db_session.flush()

    # Next year's birthday is a new wish.
    db_session.add(Wish(sender_id=sender.id, recipient_id=recipient.id, year=2027, message="2"))
    await db_session.flush()

    db_session.add(Wish(sender_id=sender.id, recipient_id=recipient.id, year=2026, message="3"))
    with pytest.raises(IntegrityError, match="uq_wishes_sender_recipient_year"):
        await db_session.flush()


async def test_nobody_wishes_themselves(db_session: AsyncSession, make_user: MakeUser) -> None:
    user = await make_user()
    db_session.add(Wish(sender_id=user.id, recipient_id=user.id, year=2026, message="Me!"))

    with pytest.raises(IntegrityError, match="ck_wishes_not_self"):
        await db_session.flush()


async def test_the_repr_leaves_out_the_message(make_user: MakeUser) -> None:
    sender, recipient = await make_user(), await make_user()
    wish = Wish(sender_id=sender.id, recipient_id=recipient.id, year=2026, message="Secret")

    assert "Secret" not in repr(wish)
