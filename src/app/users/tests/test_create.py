from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeEmailSender
from app.users.models import Promotion, Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"


@pytest.fixture
async def admin_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user(role=Role.ADMIN, password="a long admin password"))


def _body(sub_family: SubFamily, **overrides: Any) -> dict[str, Any]:
    return {
        "family_name": "Ishimwe",
        "display_name": "Ishimwe Aline",
        "email": "aline@example.com",
        "sub_family_id": sub_family.id,
    } | overrides


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User)) or 0


async def test_an_admin_registers_a_member(
    client: AsyncClient,
    db_session: AsyncSession,
    admin_headers: dict[str, str],
    sub_family: SubFamily,
) -> None:
    promotion = Promotion(name="Test promotion", position=1000)
    db_session.add(promotion)
    await db_session.flush()

    response = await client.post(
        USERS,
        json=_body(sub_family, promotion_id=promotion.id, birthday="1995-08-21"),
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["family_name"] == "Ishimwe"
    assert body["display_name"] == "Ishimwe Aline"
    assert body["email"] == "aline@example.com"
    assert body["role"] == "member"
    assert body["birthday"] == "1995-08-21"
    assert body["sub_family"] == {"id": sub_family.id, "name": sub_family.name}
    assert body["promotion"] == {"id": promotion.id, "name": "Test promotion"}
    assert (body["email_verified_at"], body["deactivated_at"], body["photo_url"]) == (
        None,
        None,
        None,
    )
    assert body["has_password"] is False
    stored = await db_session.get(User, body["id"])
    assert stored is not None
    assert stored.password_hash is None


async def test_the_new_member_can_log_in_with_a_code(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    admin_headers: dict[str, str],
    sub_family: SubFamily,
) -> None:
    await client.post(USERS, json=_body(sub_family), headers=admin_headers)

    response = await client.post("/api/v1/auth/login", json={"family_name": "ishimwe"})

    assert response.status_code == 200
    assert len(fake_email.sent_to("aline@example.com")) == 1


async def test_names_are_trimmed(
    client: AsyncClient, admin_headers: dict[str, str], sub_family: SubFamily
) -> None:
    response = await client.post(
        USERS,
        json=_body(sub_family, family_name="  Ishimwe ", display_name=" Aline  "),
        headers=admin_headers,
    )

    assert (response.json()["family_name"], response.json()["display_name"]) == (
        "Ishimwe",
        "Aline",
    )


@pytest.mark.parametrize("role", ["social_media_manager", "admin"])
async def test_any_role_can_be_given(
    client: AsyncClient, admin_headers: dict[str, str], sub_family: SubFamily, role: str
) -> None:
    response = await client.post(USERS, json=_body(sub_family, role=role), headers=admin_headers)

    assert response.status_code == 201
    assert response.json()["role"] == role
    assert response.json()["has_password"] is False


@pytest.mark.parametrize(
    ("existing", "body", "code"),
    [
        ({"family_name": "ISHIMWE"}, {}, "family_name_taken"),
        ({"email": "ALINE@example.com"}, {}, "email_taken"),
        (
            {"family_name": "Ishimwe", "deactivated_at": datetime.now(UTC)},
            {},
            "family_name_taken",
        ),
    ],
    ids=["family-name", "email", "deactivated-users-keep-theirs"],
)
async def test_family_names_and_emails_are_unique(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    admin_headers: dict[str, str],
    sub_family: SubFamily,
    existing: dict[str, Any],
    body: dict[str, Any],
    code: str,
) -> None:
    await make_user(**existing)
    before = await _count(db_session)

    response = await client.post(USERS, json=_body(sub_family, **body), headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == code
    assert await _count(db_session) == before


@pytest.mark.parametrize(
    ("field", "code"),
    [("sub_family_id", "unknown_sub_family"), ("promotion_id", "unknown_promotion")],
)
async def test_lookups_must_exist(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sub_family: SubFamily,
    field: str,
    code: str,
) -> None:
    response = await client.post(
        USERS, json=_body(sub_family, **{field: 999_999}), headers=admin_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize(
    "overrides",
    [
        {"email": "not-an-email"},
        {"email": f"{'x' * 310}@example.com"},
        {"display_name": "  "},
        {"family_name": ""},
        {"birthday": (date.today() + timedelta(days=2)).isoformat()},
        {"birthday": "1899-12-31"},
        {"role": "owner"},
        {"sub_family_id": None},
        {"password": "sneaky extra field"},
    ],
    ids=[
        "bad-email",
        "long-email",
        "blank-display-name",
        "empty-family-name",
        "future-birthday",
        "ancient-birthday",
        "unknown-role",
        "no-sub-family",
        "unexpected-field",
    ],
)
async def test_invalid_input_is_rejected(
    client: AsyncClient,
    admin_headers: dict[str, str],
    sub_family: SubFamily,
    overrides: dict[str, Any],
) -> None:
    response = await client.post(USERS, json=_body(sub_family, **overrides), headers=admin_headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_register_members(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    login_as: LoginAs,
    sub_family: SubFamily,
    role: Role,
) -> None:
    headers = await login_as(await make_user(role=role))
    before = await _count(db_session)

    response = await client.post(USERS, json=_body(sub_family), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"
    assert await _count(db_session) == before


async def test_registering_needs_a_login(client: AsyncClient, sub_family: SubFamily) -> None:
    assert (await client.post(USERS, json=_body(sub_family))).status_code == 401
