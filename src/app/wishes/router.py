"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Fixed paths are declared before `/{wish_id}`, which would otherwise try to read them as IDs.
"""

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query

from app.auth.dependencies import CurrentUser, require_owner_or_admin
from app.core.database.session import DbSession
from app.core.pagination import Page
from app.users.schemas import UserPublicRead
from app.wishes import service
from app.wishes.schemas import (
    UpcomingBirthday,
    WishCreate,
    WishListParams,
    WishRead,
    WishUpdate,
)

router = APIRouter(prefix="/wishes", tags=["wishes"])


@router.post("", status_code=HTTPStatus.CREATED)
async def send_wish(body: WishCreate, user: CurrentUser, db: DbSession) -> WishRead:
    """Wish a member a happy birthday: when their birthday is today or in the next 30 days,
    once per birthday, and never yourself."""
    wish = await service.send_wish(db, user, body, on=service.today())
    return WishRead.model_validate(wish)


@router.get("/upcoming-birthdays")
async def list_upcoming_birthdays(user: CurrentUser, db: DbSession) -> list[UpcomingBirthday]:
    """Active members whose birthday is today or in the next 30 days, soonest first, and
    whether you've wished them yet. A month of birthdays is short, so it's not paginated."""
    upcoming = await service.upcoming_birthdays(db, user, on=service.today())
    return [
        UpcomingBirthday(
            member=UserPublicRead.model_validate(item.member),
            falls_on=item.falls_on,
            days_until=item.days_until,
            wished=item.wished,
        )
        for item in upcoming
    ]


@router.get("/received")
async def list_received(
    user: CurrentUser, db: DbSession, params: Annotated[WishListParams, Query()]
) -> Page[WishRead]:
    """Wishes sent to you, newest first; `year` keeps one birthday's. Admins can pass
    `user_id` to see anyone's."""
    owner_id = params.user_id or user.id
    require_owner_or_admin(user, owner_id)
    wishes, total = await service.list_wishes(
        db, recipient_id=owner_id, year=params.year, offset=params.offset, limit=params.limit
    )
    items = [WishRead.model_validate(wish) for wish in wishes]
    return Page.build(items, params=params, total=total)


@router.get("/sent")
async def list_sent(
    user: CurrentUser, db: DbSession, params: Annotated[WishListParams, Query()]
) -> Page[WishRead]:
    """Wishes you sent, newest first; `year` keeps one birthday's. Admins can pass `user_id`
    to see anyone's."""
    owner_id = params.user_id or user.id
    require_owner_or_admin(user, owner_id)
    wishes, total = await service.list_wishes(
        db, sender_id=owner_id, year=params.year, offset=params.offset, limit=params.limit
    )
    items = [WishRead.model_validate(wish) for wish in wishes]
    return Page.build(items, params=params, total=total)


@router.patch("/{wish_id}")
async def update_wish(
    wish_id: uuid.UUID, body: WishUpdate, user: CurrentUser, db: DbSession
) -> WishRead:
    """Change the message of a wish you sent. Only its sender can, not admins."""
    return WishRead.model_validate(await service.update_wish(db, user, wish_id, body))


@router.delete("/{wish_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_wish(wish_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Delete a wish you sent (admins: any wish)."""
    await service.delete_wish(db, user, wish_id)
