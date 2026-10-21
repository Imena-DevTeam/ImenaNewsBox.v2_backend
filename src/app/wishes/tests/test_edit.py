import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Role, User
from app.wishes.models import Wish
from app.wishes.tests.helpers import WISHES, LoginAs, MakeUser, birthday_in


@pytest.fixture
async def sender(make_user: MakeUser) -> User:
    return await make_user()


@pytest.fixture
async def wish(
    client: AsyncClient, sender: User, make_user: MakeUser, login_as: LoginAs
) -> uuid.UUID:
    recipient = await make_user(birthday=birthday_in(2))
    response = await client.post(
        WISHES,
        json={"recipient_id": str(recipient.id), "message": "Happy birthday!"},
        headers=await login_as(sender),
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


# --- Editing ---


async def test_the_sender_edits_the_message(
    client: AsyncClient, sender: User, wish: uuid.UUID, login_as: LoginAs
) -> None:
    response = await client.patch(
        f"{WISHES}/{wish}",
        json={"message": " Happy birthday, cousin! "},
        headers=await login_as(sender),
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Happy birthday, cousin!"


@pytest.mark.parametrize("role", [Role.MEMBER, Role.ADMIN])
async def test_nobody_else_edits_it_not_even_admins(
    client: AsyncClient,
    db_session: AsyncSession,
    wish: uuid.UUID,
    make_user: MakeUser,
    login_as: LoginAs,
    role: Role,
) -> None:
    response = await client.patch(
        f"{WISHES}/{wish}",
        json={"message": "Hijacked"},
        headers=await login_as(await make_user(role=role)),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_owner"
    stored = await db_session.get(Wish, wish, populate_existing=True)
    assert stored is not None
    assert stored.message == "Happy birthday!"


@pytest.mark.parametrize("body", [{"message": ""}, {"year": 2030}, {"recipient_id": None}])
async def test_only_the_message_can_change(
    client: AsyncClient, sender: User, wish: uuid.UUID, login_as: LoginAs, body: dict[str, object]
) -> None:
    response = await client.patch(f"{WISHES}/{wish}", json=body, headers=await login_as(sender))

    assert response.status_code == 422


async def test_editing_an_unknown_wish(
    client: AsyncClient, sender: User, login_as: LoginAs
) -> None:
    response = await client.patch(
        f"{WISHES}/{uuid.uuid7()}", json={"message": "Hi"}, headers=await login_as(sender)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "wish_not_found"


# --- Deleting ---


async def test_the_sender_deletes_it(
    client: AsyncClient,
    db_session: AsyncSession,
    sender: User,
    wish: uuid.UUID,
    login_as: LoginAs,
) -> None:
    response = await client.delete(f"{WISHES}/{wish}", headers=await login_as(sender))

    assert response.status_code == 204
    assert await db_session.get(Wish, wish, populate_existing=True) is None


async def test_admins_delete_any_wish(
    client: AsyncClient,
    db_session: AsyncSession,
    wish: uuid.UUID,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.delete(f"{WISHES}/{wish}", headers=headers)

    assert response.status_code == 204
    assert await db_session.get(Wish, wish, populate_existing=True) is None


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_other_members_cannot_delete_it(
    client: AsyncClient,
    db_session: AsyncSession,
    wish: uuid.UUID,
    make_user: MakeUser,
    login_as: LoginAs,
    role: Role,
) -> None:
    response = await client.delete(
        f"{WISHES}/{wish}", headers=await login_as(await make_user(role=role))
    )

    assert response.status_code == 403
    assert await db_session.get(Wish, wish, populate_existing=True) is not None


async def test_deleting_an_unknown_wish(
    client: AsyncClient, sender: User, login_as: LoginAs
) -> None:
    response = await client.delete(f"{WISHES}/{uuid.uuid7()}", headers=await login_as(sender))

    assert response.status_code == 404


async def test_editing_and_deleting_need_a_login(client: AsyncClient, wish: uuid.UUID) -> None:
    assert (await client.patch(f"{WISHES}/{wish}", json={"message": "Hi"})).status_code == 401
    assert (await client.delete(f"{WISHES}/{wish}")).status_code == 401
