from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Role, User
from app.wishes.models import Wish
from app.wishes.tests.helpers import WISHES, LoginAs, MakeUser


async def _add_wish(
    db: AsyncSession, sender: User, recipient: User, year: int = 2026, message: str = "Hi"
) -> Wish:
    # Straight into the table, so tests can set any year.
    wish = Wish(sender_id=sender.id, recipient_id=recipient.id, year=year, message=message)
    db.add(wish)
    await db.flush()
    return wish


def _ids(response_json: dict[str, object]) -> list[str]:
    items = response_json["items"]
    assert isinstance(items, list)
    return [item["id"] for item in items]


async def test_you_see_the_wishes_you_received_newest_first(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    me, aunt, uncle = await make_user(), await make_user(), await make_user()
    older = await _add_wish(db_session, aunt, me, 2025)
    newer = await _add_wish(db_session, uncle, me, 2026)
    await _add_wish(db_session, me, aunt)  # sent, not received

    response = await client.get(f"{WISHES}/received", headers=await login_as(me))

    assert response.status_code == 200
    body = response.json()
    assert _ids(body) == [str(newer.id), str(older.id)]
    assert body["total"] == 2
    assert body["items"][0]["sender"]["id"] == str(uncle.id)


async def test_you_see_the_wishes_you_sent(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    me, cousin = await make_user(), await make_user()
    sent = await _add_wish(db_session, me, cousin)
    await _add_wish(db_session, cousin, me)

    response = await client.get(f"{WISHES}/sent", headers=await login_as(me))

    assert _ids(response.json()) == [str(sent.id)]


@pytest.mark.parametrize("path", ["received", "sent"])
async def test_lists_filter_by_year_and_paginate(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    path: str,
) -> None:
    me = await make_user()
    for year in (2025, 2026, 2026, 2026):
        other = await make_user()
        if path == "received":
            await _add_wish(db_session, other, me, year)
        else:
            await _add_wish(db_session, me, other, year)

    response = await client.get(
        f"{WISHES}/{path}", params={"year": 2026, "limit": 2}, headers=await login_as(me)
    )

    body = response.json()
    assert (body["total"], len(body["items"])) == (3, 2)
    assert {item["year"] for item in body["items"]} == {2026}


async def test_wishes_from_deactivated_members_are_kept(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    me = await make_user()
    gone = await make_user(deactivated_at=datetime.now(UTC))
    wish = await _add_wish(db_session, gone, me)

    response = await client.get(f"{WISHES}/received", headers=await login_as(me))

    assert _ids(response.json()) == [str(wish.id)]


@pytest.mark.parametrize("path", ["received", "sent"])
async def test_admins_see_anyones_wishes(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    path: str,
) -> None:
    sender, recipient = await make_user(), await make_user()
    wish = await _add_wish(db_session, sender, recipient)
    user_id = recipient.id if path == "received" else sender.id

    response = await client.get(
        f"{WISHES}/{path}",
        params={"user_id": str(user_id)},
        headers=await login_as(await make_user(role=Role.ADMIN)),
    )

    assert _ids(response.json()) == [str(wish.id)]


@pytest.mark.parametrize("path", ["received", "sent"])
async def test_members_cannot_see_someone_elses_wishes(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, path: str
) -> None:
    other = await make_user()
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    response = await client.get(
        f"{WISHES}/{path}", params={"user_id": str(other.id)}, headers=headers
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_owner"


@pytest.mark.parametrize("path", ["received", "sent"])
async def test_lists_need_a_login(client: AsyncClient, path: str) -> None:
    assert (await client.get(f"{WISHES}/{path}")).status_code == 401
