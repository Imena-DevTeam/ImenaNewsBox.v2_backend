import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient

from app.users.models import Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
ACCOUNT_FIELDS = {
    "id",
    "family_name",
    "display_name",
    "photo_url",
    "sub_family",
    "promotion",
    "birthday",
    "email",
    "email_verified_at",
    "role",
    "has_password",
    "sanity_author_id",
    "deactivated_at",
    "created_at",
}


# --- Your own account ---


async def test_you_see_your_whole_account(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, sub_family: SubFamily
) -> None:
    user = await make_user(birthday=date(1990, 5, 17), role=Role.SOCIAL_MEDIA_MANAGER)

    response = await client.get(f"{USERS}/me", headers=await login_as(user))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ACCOUNT_FIELDS
    assert body["id"] == str(user.id)
    assert body["email"] == user.email
    assert body["birthday"] == "1990-05-17"
    assert body["role"] == "social_media_manager"
    assert body["sub_family"] == {"id": sub_family.id, "name": sub_family.name}
    assert (body["email_verified_at"], body["deactivated_at"]) == (None, None)
    assert body["has_password"] is False


async def test_the_password_hash_never_leaves(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    admin = await make_user(role=Role.ADMIN, password="a long admin password")

    response = await client.get(f"{USERS}/me", headers=await login_as(admin))

    assert response.json()["has_password"] is True
    assert admin.password_hash is not None
    assert admin.password_hash not in response.text
    assert "argon2" not in response.text
    assert "password_hash" not in response.text


async def test_your_account_needs_a_login(client: AsyncClient) -> None:
    assert (await client.get(f"{USERS}/me")).status_code == 401


# --- One account ---


async def test_you_can_read_your_account_by_id(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.get(f"{USERS}/{user.id}/account", headers=await login_as(user))

    assert response.status_code == 200
    assert set(response.json()) == ACCOUNT_FIELDS


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_members_cannot_read_other_accounts(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    other = await make_user()
    headers = await login_as(await make_user(role=role))

    response = await client.get(f"{USERS}/{other.id}/account", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_owner"
    assert other.email not in response.text


async def test_admins_can_read_any_account_even_deactivated(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    gone = await make_user(deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.get(f"{USERS}/{gone.id}/account", headers=headers)

    assert response.status_code == 200
    assert response.json()["deactivated_at"] is not None


async def test_an_unknown_account_is_not_found_for_admins(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.get(f"{USERS}/{uuid.uuid7()}/account", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "user_not_found"


# --- All accounts ---


async def test_admins_list_active_accounts_by_default(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    await make_user(display_name="Active")
    await make_user(display_name="Gone", deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(display_name="Admin", role=Role.ADMIN))

    response = await client.get(f"{USERS}/accounts", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert [item["display_name"] for item in body["items"]] == ["Active", "Admin"]
    assert set(body["items"][0]) == ACCOUNT_FIELDS


async def test_admins_can_include_deactivated_accounts(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    await make_user(display_name="Gone", deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(display_name="Admin", role=Role.ADMIN))

    response = await client.get(
        f"{USERS}/accounts", params={"include_deactivated": "true", "limit": 1}, headers=headers
    )

    body = response.json()
    assert (body["total"], body["limit"]) == (2, 1)
    assert [item["display_name"] for item in body["items"]] == ["Admin"]


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_list_accounts(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    response = await client.get(
        f"{USERS}/accounts", headers=await login_as(await make_user(role=role))
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"
