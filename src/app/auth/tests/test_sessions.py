import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserSession
from app.auth.service import create_session
from app.core.security import decode_access_token
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
SESSIONS = "/api/v1/auth/sessions"


def _session_id(headers: dict[str, str]) -> uuid.UUID:
    return decode_access_token(headers["Authorization"].removeprefix("Bearer ")).session_id


async def _device(
    db: AsyncSession, user: User, label: str, *, used: timedelta = timedelta(0)
) -> UserSession:
    session = (await create_session(db, user, device_label=label)).session
    session.last_used_at = datetime.now(UTC) - used
    await db.flush()
    return session


# --- Listing ---


async def test_you_see_your_active_devices(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()
    headers = await login_as(user)
    laptop = await _device(db_session, user, "Firefox on Windows", used=timedelta(days=2))
    revoked = await _device(db_session, user, "old phone")
    revoked.revoked_at = datetime.now(UTC)
    expired = await _device(db_session, user, "lost tablet")
    expired.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await _device(db_session, await make_user(), "someone else's phone")
    await db_session.flush()

    response = await client.get(SESSIONS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    [this_device, other] = body["items"]
    assert this_device["id"] == str(_session_id(headers))
    assert this_device["current"] is True
    assert other == {
        "id": str(laptop.id),
        "device_label": "Firefox on Windows",
        "created_at": laptop.created_at.isoformat().replace("+00:00", "Z"),
        "last_used_at": laptop.last_used_at.isoformat().replace("+00:00", "Z"),
        "expires_at": laptop.expires_at.isoformat().replace("+00:00", "Z"),
        "current": False,
    }


async def test_devices_are_listed_most_recently_used_first(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()
    headers = await login_as(user)
    await _device(db_session, user, "a week ago", used=timedelta(days=7))
    await _device(db_session, user, "yesterday", used=timedelta(days=1))

    response = await client.get(SESSIONS, headers=headers)

    labels = [item["device_label"] for item in response.json()["items"]]
    assert labels == ["pytest", "yesterday", "a week ago"]


async def test_the_list_is_paginated(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()
    headers = await login_as(user)
    await _device(db_session, user, "laptop", used=timedelta(days=1))

    response = await client.get(SESSIONS, params={"page": 2, "limit": 1}, headers=headers)

    body = response.json()
    assert (body["total"], body["page"], body["limit"]) == (2, 2, 1)
    assert [item["device_label"] for item in body["items"]] == ["laptop"]


async def test_no_secrets_are_listed(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    response = await client.get(SESSIONS, headers=await login_as(await make_user()))

    assert "token_hash" not in response.text
    assert "user_id" not in response.text


async def test_your_own_user_id_is_allowed(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.get(
        SESSIONS, params={"user_id": str(user.id)}, headers=await login_as(user)
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1


async def test_members_cannot_see_other_peoples_devices(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    other = await make_user()
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    response = await client.get(SESSIONS, params={"user_id": str(other.id)}, headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_owner"


async def test_admins_can_see_anyones_devices(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    member = await make_user()
    phone = await _device(db_session, member, "phone")
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.get(SESSIONS, params={"user_id": str(member.id)}, headers=headers)

    assert response.status_code == 200
    [item] = response.json()["items"]
    assert item["id"] == str(phone.id)
    assert item["current"] is False


async def test_listing_needs_a_login(client: AsyncClient) -> None:
    response = await client.get(SESSIONS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


async def test_a_malformed_user_id_is_rejected(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    response = await client.get(
        SESSIONS, params={"user_id": "not-a-uuid"}, headers=await login_as(await make_user())
    )

    assert response.status_code == 422
