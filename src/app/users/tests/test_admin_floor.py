"""The two-admin floor: an active admin can't be demoted or deactivated while there are only
`MIN_ADMINS` active admins, so a locked-out admin always has another one to ask."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Role, User
from app.users.service import MIN_ADMINS

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
PASSWORD = "an admin password"


async def _admin(make_user: MakeUser, **fields: object) -> User:
    return await make_user(role=Role.ADMIN, password=PASSWORD, **fields)


async def _fresh(db: AsyncSession, user: User) -> User:
    await db.refresh(user)
    return user


def test_the_floor_is_two() -> None:
    assert MIN_ADMINS == 2


async def test_one_of_two_admins_cannot_be_demoted(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    other = await _admin(make_user)

    response = await client.patch(
        f"{USERS}/{other.id}", json={"role": "member"}, headers=await login_as(actor)
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_few_admins"
    assert (await _fresh(db_session, other)).role is Role.ADMIN


async def test_one_of_two_admins_cannot_be_deactivated(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    other = await _admin(make_user)

    response = await client.post(f"{USERS}/{other.id}/deactivate", headers=await login_as(actor))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_few_admins"
    assert (await _fresh(db_session, other)).deactivated_at is None


async def test_with_a_third_admin_one_can_be_demoted(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    other = await _admin(make_user)
    await _admin(make_user)

    response = await client.patch(
        f"{USERS}/{other.id}",
        json={"role": "social_media_manager"},
        headers=await login_as(actor),
    )

    assert response.status_code == 200
    assert response.json()["role"] == "social_media_manager"


async def test_with_a_third_admin_one_can_be_deactivated(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    other = await _admin(make_user)
    await _admin(make_user)

    response = await client.post(f"{USERS}/{other.id}/deactivate", headers=await login_as(actor))

    assert response.status_code == 200
    assert response.json()["deactivated_at"] is not None


async def test_a_deactivated_admin_does_not_count(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    other = await _admin(make_user)
    await _admin(make_user, deactivated_at=datetime.now(UTC))

    response = await client.post(f"{USERS}/{other.id}/deactivate", headers=await login_as(actor))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "too_few_admins"


async def test_a_deactivated_admin_can_still_be_demoted(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    await _admin(make_user)
    gone = await _admin(make_user, deactivated_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{gone.id}", json={"role": "member"}, headers=await login_as(actor)
    )

    assert response.status_code == 200
    assert response.json()["role"] == "member"


async def test_deactivating_an_already_deactivated_admin_changes_nothing(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    await _admin(make_user)
    gone = await _admin(make_user, deactivated_at=datetime.now(UTC))

    response = await client.post(f"{USERS}/{gone.id}/deactivate", headers=await login_as(actor))

    assert response.status_code == 200


async def test_members_are_not_affected_by_the_floor(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    actor = await _admin(make_user)
    writer = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    headers = await login_as(actor)
    demoted = await client.patch(f"{USERS}/{writer.id}", json={"role": "member"}, headers=headers)
    deactivated = await client.post(f"{USERS}/{writer.id}/deactivate", headers=headers)

    assert demoted.status_code == 200
    assert deactivated.status_code == 200
