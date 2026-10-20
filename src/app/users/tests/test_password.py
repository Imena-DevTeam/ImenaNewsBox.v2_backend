import uuid
from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeEmailSender
from app.core.security import verify_password
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
NEW_PASSWORD = "a brand new admin passphrase"


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user(role=Role.ADMIN, password="the first admin password")


@pytest.fixture
async def admin_headers(admin: User, login_as: LoginAs) -> dict[str, str]:
    return await login_as(admin)


async def _stored_hash(db: AsyncSession, user: User) -> str | None:
    return await db.scalar(
        select(User.password_hash)
        .where(User.id == user.id)
        .execution_options(populate_existing=True)
    )


async def test_a_promoted_admin_gets_a_password_and_can_log_in(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    promoted = await make_user(role=Role.ADMIN)

    response = await client.put(
        f"{USERS}/{promoted.id}/password", json={"password": NEW_PASSWORD}, headers=admin_headers
    )

    assert response.status_code == 204
    assert await verify_password(NEW_PASSWORD, await _stored_hash(db_session, promoted))
    account = await client.get(f"{USERS}/{promoted.id}/account", headers=admin_headers)
    assert account.json()["has_password"] is True
    login = await client.post(
        "/api/v1/auth/login",
        json={"family_name": promoted.family_name, "password": NEW_PASSWORD},
    )
    assert login.status_code == 200
    assert len(fake_email.sent_to(promoted.email)) == 1


async def test_an_admin_can_change_their_own_password(
    client: AsyncClient, admin: User, admin_headers: dict[str, str]
) -> None:
    await client.put(
        f"{USERS}/{admin.id}/password", json={"password": NEW_PASSWORD}, headers=admin_headers
    )

    old = await client.post(
        "/api/v1/auth/login",
        json={"family_name": admin.family_name, "password": "the first admin password"},
    )
    assert old.json()["error"]["code"] == "invalid_credentials"


async def test_members_have_no_password(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    response = await client.put(
        f"{USERS}/{member.id}/password", json={"password": NEW_PASSWORD}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_an_admin"
    assert await _stored_hash(db_session, member) is None


@pytest.mark.parametrize("password", ["eleven char", "", "p" * 257])
async def test_weak_or_huge_passwords_are_rejected_without_echoing_them(
    client: AsyncClient, admin: User, admin_headers: dict[str, str], password: str
) -> None:
    response = await client.put(
        f"{USERS}/{admin.id}/password", json={"password": password}, headers=admin_headers
    )

    assert response.status_code == 422
    if password:
        assert password not in response.text


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_set_passwords(
    client: AsyncClient, admin: User, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    headers = await login_as(await make_user(role=role))

    response = await client.put(
        f"{USERS}/{admin.id}/password", json={"password": NEW_PASSWORD}, headers=headers
    )

    assert response.status_code == 403


async def test_an_unknown_user_is_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.put(
        f"{USERS}/{uuid.uuid7()}/password", json={"password": NEW_PASSWORD}, headers=admin_headers
    )

    assert response.status_code == 404
