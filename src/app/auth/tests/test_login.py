from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.fakes import FakeEmailSender
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LOGIN = "/api/v1/auth/login"
SENT = {
    "message": "If the details are right, a code is on its way.",
    "expires_in": 600,
    "resend_after": 60,
}
PASSWORD = "correct horse battery staple"


async def test_a_member_is_emailed_a_code(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()

    response = await client.post(LOGIN, json={"family_name": user.family_name})

    assert response.status_code == 200
    assert response.json() == SENT
    assert len(fake_email.sent_to(user.email)) == 1
    assert fake_email.last_code(user.email).isdigit()


async def test_the_family_name_ignores_case_and_spaces(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user(family_name="Mugisha")

    response = await client.post(LOGIN, json={"family_name": "  mUGISHA "})

    assert response.status_code == 200
    assert len(fake_email.sent_to(user.email)) == 1


@pytest.mark.parametrize("who", ["unknown", "deactivated", "admin-without-password"])
async def test_no_code_but_the_same_answer(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser, who: str
) -> None:
    if who == "unknown":
        family_name = "Nobody"
    elif who == "deactivated":
        family_name = (await make_user(deactivated_at=datetime.now(UTC))).family_name
    else:
        family_name = (await make_user(role=Role.ADMIN, password=PASSWORD)).family_name

    response = await client.post(LOGIN, json={"family_name": family_name})

    assert response.status_code == 200
    assert response.json() == SENT
    assert fake_email.sent == []


async def test_an_admin_with_the_right_password_is_emailed_a_code(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN, password=PASSWORD)

    response = await client.post(
        LOGIN, json={"family_name": admin.family_name, "password": PASSWORD}
    )

    assert response.status_code == 200
    assert response.json() == SENT
    assert len(fake_email.sent_to(admin.email)) == 1


@pytest.mark.parametrize(
    ("fields", "password"),
    [
        ({"role": Role.ADMIN, "password": PASSWORD}, "wrong password"),
        ({}, PASSWORD),
        ({"role": Role.SOCIAL_MEDIA_MANAGER}, PASSWORD),
        (None, PASSWORD),
        ({"role": Role.ADMIN, "password": PASSWORD, "deactivated": True}, PASSWORD),
    ],
    ids=["admin-wrong-password", "member", "social-media-manager", "unknown", "deactivated"],
)
async def test_a_wrong_password_is_refused_the_same_way(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    fields: dict[str, Any] | None,
    password: str,
) -> None:
    if fields is None:
        family_name = "Nobody"
    else:
        if fields.pop("deactivated", False):
            fields["deactivated_at"] = datetime.now(UTC)
        family_name = (await make_user(**fields)).family_name

    response = await client.post(LOGIN, json={"family_name": family_name, "password": password})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert fake_email.sent == []


async def test_a_second_request_within_a_minute_is_refused(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    await client.post(LOGIN, json={"family_name": user.family_name})

    response = await client.post(LOGIN, json={"family_name": user.family_name})

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "code_recently_sent"
    assert len(fake_email.sent_to(user.email)) == 1


async def test_an_email_failure_is_reported(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    fake_email.failures_left = 2

    response = await client.post(LOGIN, json={"family_name": user.family_name})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_not_sent"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"family_name": ""},
        {"family_name": "   "},
        {"family_name": "x" * 65},
        {"family_name": "Mugisha", "password": ""},
        {"family_name": "Mugisha", "password": "p" * 257},
    ],
    ids=["missing", "empty", "blank", "too-long", "empty-password", "huge-password"],
)
async def test_invalid_input_is_rejected_without_echoing_it(
    client: AsyncClient, body: dict[str, str]
) -> None:
    response = await client.post(LOGIN, json=body)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "p" * 257 not in response.text
