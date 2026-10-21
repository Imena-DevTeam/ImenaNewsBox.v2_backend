import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Role
from app.wishes.models import Wish
from app.wishes.tests.helpers import WISHES, LoginAs, MakeUser, birthday_in, year_of_birthday_in


async def _wish_count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Wish)) or 0


@pytest.mark.parametrize("days", [0, 1, 30])
async def test_you_wish_a_member_whose_birthday_is_coming(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, days: int
) -> None:
    sender = await make_user()
    recipient = await make_user(birthday=birthday_in(days))

    response = await client.post(
        WISHES,
        json={"recipient_id": str(recipient.id), "message": "  Happy birthday!  "},
        headers=await login_as(sender),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["message"] == "Happy birthday!"
    assert body["year"] == year_of_birthday_in(days)
    assert (body["sender"]["id"], body["recipient"]["id"]) == (str(sender.id), str(recipient.id))
    # Members never see each other's year of birth, wishes included.
    assert body["recipient"]["birthday"] == {
        "month": recipient.birthday.month,  # type: ignore[union-attr]
        "day": recipient.birthday.day,  # type: ignore[union-attr]
    }


@pytest.mark.parametrize("days", [31, 200, -1])
async def test_birthdays_more_than_30_days_away_are_refused(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs, days: int
) -> None:
    recipient = await make_user(birthday=birthday_in(days))

    response = await client.post(
        WISHES,
        json={"recipient_id": str(recipient.id), "message": "Early!"},
        headers=await login_as(await make_user()),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "birthday_not_soon"
    assert await _wish_count(db_session) == 0


async def test_members_without_a_birthday_cannot_be_wished(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    recipient = await make_user(birthday=None)

    response = await client.post(
        WISHES,
        json={"recipient_id": str(recipient.id), "message": "Whenever it is!"},
        headers=await login_as(await make_user()),
    )

    assert response.json()["error"]["code"] == "birthday_not_soon"


async def test_one_wish_per_birthday(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    recipient = await make_user(birthday=birthday_in(3))
    headers = await login_as(await make_user())
    body = {"recipient_id": str(recipient.id), "message": "Happy birthday!"}
    assert (await client.post(WISHES, json=body, headers=headers)).status_code == 201

    again = await client.post(WISHES, json=body, headers=headers)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "wish_already_sent"
    assert await _wish_count(db_session) == 1


async def test_others_can_wish_the_same_member(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    recipient = await make_user(birthday=birthday_in(3))
    body = {"recipient_id": str(recipient.id), "message": "Happy birthday!"}

    for _ in range(2):
        response = await client.post(WISHES, json=body, headers=await login_as(await make_user()))
        assert response.status_code == 201


async def test_you_cannot_wish_yourself(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(birthday=birthday_in(0))

    response = await client.post(
        WISHES,
        json={"recipient_id": str(user.id), "message": "Happy birthday to me"},
        headers=await login_as(user),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "cannot_wish_yourself"


async def test_deactivated_or_unknown_members_are_not_found(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    gone = await make_user(birthday=birthday_in(1), deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(role=Role.ADMIN))

    for recipient_id in (gone.id, uuid.uuid7()):
        response = await client.post(
            WISHES, json={"recipient_id": str(recipient_id), "message": "Hi"}, headers=headers
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "user_not_found"


@pytest.mark.parametrize(
    "extra",
    [{"message": "   "}, {"message": "x" * 2001}, {"year": 2020}, {}],
    ids=["blank", "too-long", "year-sent", "no-message"],
)
async def test_invalid_wishes_are_rejected(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, extra: dict[str, Any]
) -> None:
    recipient = await make_user(birthday=birthday_in(1))
    body: dict[str, Any] = {"recipient_id": str(recipient.id)}
    if "year" in extra:
        body["message"] = "Hi"
    body.update(extra)

    response = await client.post(WISHES, json=body, headers=await login_as(await make_user()))

    assert response.status_code == 422


async def test_wishing_needs_a_login(client: AsyncClient, make_user: MakeUser) -> None:
    recipient = await make_user(birthday=birthday_in(1))

    response = await client.post(WISHES, json={"recipient_id": str(recipient.id), "message": "Hi"})

    assert response.status_code == 401
