import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Promotion, Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
PASSWORD = "a long admin password"


@pytest.fixture
async def admin(make_user: MakeUser) -> User:
    return await make_user(role=Role.ADMIN, password=PASSWORD)


@pytest.fixture
async def admin_headers(admin: User, login_as: LoginAs) -> dict[str, str]:
    return await login_as(admin)


async def _fresh(db: AsyncSession, user_id: uuid.UUID) -> User:
    """The row as stored now (a failed save leaves the old object expired)."""
    user = await db.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    assert user is not None
    return user


async def test_an_admin_changes_a_members_fields(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(birthday=date(1990, 1, 1))
    other_family = SubFamily(name="Another sub-family")
    promotion = Promotion(name="Test promotion", position=1000)
    db_session.add_all([other_family, promotion])
    await db_session.flush()

    response = await client.patch(
        f"{USERS}/{member.id}",
        json={
            "family_name": "Renamed",
            "display_name": "New Name",
            "sub_family_id": other_family.id,
            "promotion_id": promotion.id,
            "birthday": "1991-02-03",
        },
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["family_name"], body["display_name"], body["birthday"]) == (
        "Renamed",
        "New Name",
        "1991-02-03",
    )
    assert body["sub_family"] == {"id": other_family.id, "name": "Another sub-family"}
    assert body["promotion"] == {"id": promotion.id, "name": "Test promotion"}


async def test_fields_left_out_stay_the_same(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user(birthday=date(1990, 1, 1))

    response = await client.patch(
        f"{USERS}/{member.id}", json={"display_name": "Only This"}, headers=admin_headers
    )

    body = response.json()
    assert body["display_name"] == "Only This"
    assert (body["family_name"], body["email"], body["birthday"]) == (
        member.family_name,
        member.email,
        "1990-01-01",
    )


async def test_promotion_and_birthday_can_be_cleared(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    promotion = Promotion(name="Test promotion", position=1000)
    db_session.add(promotion)
    await db_session.flush()
    member = await make_user(birthday=date(1990, 1, 1), promotion_id=promotion.id)

    response = await client.patch(
        f"{USERS}/{member.id}", json={"promotion_id": None, "birthday": None}, headers=admin_headers
    )

    assert (response.json()["promotion"], response.json()["birthday"]) == (None, None)


async def test_a_new_email_must_be_verified_again(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{member.id}", json={"email": "new.address@example.com"}, headers=admin_headers
    )

    assert response.json()["email"] == "new.address@example.com"
    assert response.json()["email_verified_at"] is None


async def test_different_capitals_are_the_same_email(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user(email="aline@example.com", email_verified_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{member.id}", json={"email": "Aline@example.com"}, headers=admin_headers
    )

    assert response.json()["email"] == "Aline@example.com"
    assert response.json()["email_verified_at"] is not None


async def test_a_promoted_admin_still_needs_a_password(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user()

    response = await client.patch(
        f"{USERS}/{member.id}", json={"role": "admin"}, headers=admin_headers
    )

    assert response.json()["role"] == "admin"
    assert response.json()["has_password"] is False


async def test_a_demoted_admin_loses_their_password(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    other_admin = await make_user(role=Role.ADMIN, password=PASSWORD)

    response = await client.patch(
        f"{USERS}/{other_admin.id}", json={"role": "social_media_manager"}, headers=admin_headers
    )

    assert response.json()["role"] == "social_media_manager"
    assert response.json()["has_password"] is False
    assert (await _fresh(db_session, other_admin.id)).password_hash is None


async def test_admins_cannot_change_their_own_role(
    client: AsyncClient, db_session: AsyncSession, admin: User, admin_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{USERS}/{admin.id}", json={"role": "member"}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "cannot_change_own_role"
    assert (await _fresh(db_session, admin.id)).role is Role.ADMIN


async def test_admins_can_change_their_other_fields(
    client: AsyncClient, admin: User, admin_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{USERS}/{admin.id}",
        json={"display_name": "Head Admin", "role": "admin"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "Head Admin"


@pytest.mark.parametrize(
    ("field", "code"),
    [("family_name", "family_name_taken"), ("email", "email_taken")],
)
async def test_taken_family_names_and_emails_are_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
    field: str,
    code: str,
) -> None:
    await make_user(family_name="Taken", email="taken@example.com")
    member_id = (await make_user()).id
    value = {"family_name": "TAKEN", "email": "Taken@example.com"}[field]

    response = await client.patch(
        f"{USERS}/{member_id}",
        json={field: value, "display_name": "Not Saved Either"},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    # The whole change was undone, and the old object is expired, so read the row again.
    stored = await _fresh(db_session, member_id)
    assert stored.display_name != "Not Saved Either"


async def test_an_unknown_sub_family_is_refused(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    member = await make_user()

    response = await client.patch(
        f"{USERS}/{member.id}", json={"sub_family_id": 999_999}, headers=admin_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_sub_family"


async def test_deactivated_users_can_be_edited(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    gone = await make_user(deactivated_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{gone.id}", json={"display_name": "Still Editable"}, headers=admin_headers
    )

    assert response.status_code == 200


async def test_an_unknown_user_is_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{USERS}/{uuid.uuid7()}", json={"display_name": "Nobody"}, headers=admin_headers
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"family_name": None},
        {"email": None},
        {"role": None},
        {"sub_family_id": None},
        {"email": "not-an-email"},
        {"password_hash": "x"},
    ],
    ids=["null-family-name", "null-email", "null-role", "null-sub-family", "bad-email", "extra"],
)
async def test_invalid_changes_are_rejected(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str], body: dict[str, Any]
) -> None:
    member = await make_user()

    response = await client.patch(f"{USERS}/{member.id}", json=body, headers=admin_headers)

    assert response.status_code == 422


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_change_users(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    user = await make_user(role=role)

    # Not even their own record: members edit their profile through /users/me.
    response = await client.patch(
        f"{USERS}/{user.id}", json={"display_name": "Self Edit"}, headers=await login_as(user)
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"
