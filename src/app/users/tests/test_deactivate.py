import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserSession
from app.auth.service import create_session
from app.core.fakes import FakeEmailSender
from app.users.models import Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user(role=Role.ADMIN, password="a long admin password")


@pytest.fixture
async def admin_headers(admin: User, login_as: LoginAs) -> dict[str, str]:
    return await login_as(admin)


async def _sessions(db: AsyncSession, user: User) -> list[UserSession]:
    rows = await db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


# --- Deactivating ---


async def test_deactivating_signs_the_user_out_everywhere_at_once(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    member_headers = await login_as(member)
    await create_session(db_session, member, device_label="laptop")
    assert (await client.get(f"{USERS}/me", headers=member_headers)).status_code == 200

    response = await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["deactivated_at"] is not None
    sessions = await _sessions(db_session, member)
    assert len(sessions) == 2
    assert all(session.revoked_at is not None for session in sessions)
    # Their next request fails, not 15 minutes later.
    after = await client.get(f"{USERS}/me", headers=member_headers)
    assert after.json()["error"]["code"] == "session_ended"


async def test_a_deactivated_user_cannot_log_in_or_be_seen(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)

    await client.post("/api/v1/auth/login", json={"family_name": member.family_name})
    directory = await client.get(USERS, headers=admin_headers)
    profile = await client.get(f"{USERS}/{member.id}", headers=admin_headers)

    assert fake_email.sent_to(member.email) == []
    assert str(member.id) not in directory.text
    assert profile.status_code == 404


async def test_their_family_name_stays_reserved(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str], sub_family: SubFamily
) -> None:
    member = await make_user()
    await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)

    response = await client.post(
        USERS,
        json={
            "family_name": member.family_name,
            "display_name": "Newcomer",
            "email": "newcomer@example.com",
            "sub_family_id": sub_family.id,
        },
        headers=admin_headers,
    )

    assert response.json()["error"]["code"] == "family_name_taken"


async def test_deactivating_twice_changes_nothing(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    gone_at = datetime.now(UTC) - timedelta(days=3)
    member = await make_user(deactivated_at=gone_at)

    response = await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)

    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["deactivated_at"]) == gone_at


async def test_admins_cannot_deactivate_themselves(
    client: AsyncClient, admin: User, admin_headers: dict[str, str]
) -> None:
    response = await client.post(f"{USERS}/{admin.id}/deactivate", headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cannot_deactivate_self"
    assert (await client.get(f"{USERS}/me", headers=admin_headers)).status_code == 200


async def test_admins_can_deactivate_other_admins(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    other_admin = await make_user(role=Role.ADMIN, password="another long password")

    response = await client.post(f"{USERS}/{other_admin.id}/deactivate", headers=admin_headers)

    assert response.status_code == 200


# --- Reactivating ---


async def test_reactivating_lets_them_log_in_again(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await create_session(db_session, member, device_label="old phone")
    await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)

    response = await client.post(f"{USERS}/{member.id}/reactivate", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["deactivated_at"] is None
    login = await client.post("/api/v1/auth/login", json={"family_name": member.family_name})
    assert login.status_code == 200
    assert len(fake_email.sent_to(member.email)) == 1
    # Each device logs in from scratch: old sessions stay revoked.
    [old] = await _sessions(db_session, member)
    assert old.revoked_at is not None


async def test_reactivating_an_active_user_changes_nothing(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user()

    response = await client.post(f"{USERS}/{member.id}/reactivate", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["deactivated_at"] is None


# --- Access ---


@pytest.mark.parametrize("action", ["deactivate", "reactivate"])
@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_change_active_status(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, action: str, role: Role
) -> None:
    other = await make_user()
    headers = await login_as(await make_user(role=role))

    response = await client.post(f"{USERS}/{other.id}/{action}", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


@pytest.mark.parametrize("action", ["deactivate", "reactivate"])
async def test_an_unknown_user_is_not_found(
    client: AsyncClient, admin_headers: dict[str, str], action: str
) -> None:
    response = await client.post(f"{USERS}/{uuid.uuid7()}/{action}", headers=admin_headers)

    assert response.status_code == 404
