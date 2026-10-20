import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Promotion, Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
PUBLIC_FIELDS = {
    "id",
    "family_name",
    "display_name",
    "photo_url",
    "sub_family",
    "promotion",
    "birthday",
}


async def _promotion(db: AsyncSession, name: str) -> Promotion:
    row = Promotion(name=name)
    db.add(row)
    await db.flush()
    return row


# --- The directory ---


async def test_members_see_each_other_without_private_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    sub_family: SubFamily,
) -> None:
    promotion = await _promotion(db_session, "Test promotion 2019")
    other = await make_user(
        display_name="Aline",
        birthday=date(1994, 3, 9),
        promotion_id=promotion.id,
        photo_url="https://media.test/aline.jpg",
        role=Role.ADMIN,
        password="secret password",
    )
    headers = await login_as(await make_user(display_name="Zed"))

    response = await client.get(USERS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    aline = body["items"][0]
    assert set(aline) == PUBLIC_FIELDS
    assert aline == {
        "id": str(other.id),
        "family_name": other.family_name,
        "display_name": "Aline",
        "photo_url": "https://media.test/aline.jpg",
        "sub_family": {"id": sub_family.id, "name": sub_family.name},
        "promotion": {"id": promotion.id, "name": "Test promotion 2019"},
        "birthday": {"month": 3, "day": 9},
    }
    assert "1994" not in response.text
    assert other.email not in response.text


async def test_the_directory_is_alphabetical_and_skips_deactivated_members(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    await make_user(display_name="charles")
    await make_user(display_name="Béatrice")
    await make_user(display_name="Gone", deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(display_name="Aimé"))

    response = await client.get(USERS, headers=headers)

    names = [item["display_name"] for item in response.json()["items"]]
    assert names == ["Aimé", "Béatrice", "charles"]


async def test_the_directory_is_paginated(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    for name in ("B", "C"):
        await make_user(display_name=name)
    headers = await login_as(await make_user(display_name="A"))

    response = await client.get(USERS, params={"page": 2, "limit": 2}, headers=headers)

    body = response.json()
    assert (body["total"], body["page"], body["limit"]) == (3, 2, 2)
    assert [item["display_name"] for item in body["items"]] == ["C"]


async def test_no_birthday_and_no_promotion_are_null(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.get(f"{USERS}/{user.id}", headers=await login_as(user))

    body = response.json()
    assert (body["birthday"], body["promotion"], body["photo_url"]) == (None, None, None)


async def test_the_directory_needs_a_login(client: AsyncClient) -> None:
    assert (await client.get(USERS)).status_code == 401


# --- One member ---


async def test_a_member_can_see_another(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    other = await make_user(birthday=date(2000, 12, 31))
    headers = await login_as(await make_user())

    response = await client.get(f"{USERS}/{other.id}", headers=headers)

    assert response.status_code == 200
    assert set(response.json()) == PUBLIC_FIELDS
    assert response.json()["birthday"] == {"month": 12, "day": 31}


async def test_deactivated_and_unknown_members_are_not_found(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    gone = await make_user(deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(role=Role.ADMIN))

    for user_id in (gone.id, uuid.uuid7()):
        response = await client.get(f"{USERS}/{user_id}", headers=headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "user_not_found"


# --- Lookup lists ---


async def test_sub_families_and_promotions_are_listed_by_name(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    sub_family: SubFamily,
) -> None:
    second = SubFamily(name="AAA first by name")
    db_session.add(second)
    late, early = await _promotion(db_session, "2021"), await _promotion(db_session, "2009")
    headers = await login_as(await make_user())

    families = await client.get(f"{USERS}/sub-families", headers=headers)
    promotions = await client.get(f"{USERS}/promotions", headers=headers)

    assert families.json() == [
        {"id": second.id, "name": "AAA first by name"},
        {"id": sub_family.id, "name": sub_family.name},
    ]
    assert promotions.json() == [
        {"id": early.id, "name": "2009"},
        {"id": late.id, "name": "2021"},
    ]


async def test_lookup_lists_need_a_login(client: AsyncClient) -> None:
    assert (await client.get(f"{USERS}/sub-families")).status_code == 401
    assert (await client.get(f"{USERS}/promotions")).status_code == 401
