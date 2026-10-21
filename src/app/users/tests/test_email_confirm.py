from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import EmailCode
from app.core.email import CodePurpose
from app.core.fakes import FakeEmailSender
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
NEW_EMAIL = "new.address@example.com"


@pytest.fixture
async def admin_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user(role=Role.ADMIN, password="a long admin password"))


async def _change_email(
    client: AsyncClient, admin_headers: dict[str, str], user: User, email: str = NEW_EMAIL
) -> None:
    response = await client.patch(
        f"{USERS}/{user.id}", json={"email": email}, headers=admin_headers
    )
    assert response.status_code == 200, response.text


async def _codes(db: AsyncSession, user: User) -> int:
    count = await db.scalar(
        select(func.count()).where(
            EmailCode.user_id == user.id, EmailCode.purpose == CodePurpose.EMAIL_CHANGE
        )
    )
    return count or 0


async def _age_codes(db: AsyncSession, user: User) -> None:
    await db.execute(
        update(EmailCode)
        .where(EmailCode.user_id == user.id)
        .values(created_at=EmailCode.created_at - timedelta(minutes=2))
    )


# --- The code after an admin changes the email ---


async def test_a_new_email_gets_a_confirmation_code(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))
    old_email = member.email

    await _change_email(client, admin_headers, member)

    [message] = fake_email.sent_to(NEW_EMAIL)
    assert "Confirm your new" in message.subject
    assert fake_email.sent_to(old_email) == []


async def test_the_member_confirms_it(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))
    await _change_email(client, admin_headers, member)

    response = await client.post(
        f"{USERS}/me/email/verify",
        json={"code": fake_email.last_code(NEW_EMAIL)},
        headers=await login_as(member),
    )

    assert response.status_code == 200
    assert response.json()["email"] == NEW_EMAIL
    assert response.json()["email_verified_at"] is not None


async def test_a_wrong_code_does_not_confirm(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await _change_email(client, admin_headers, member)
    code = fake_email.last_code(NEW_EMAIL)

    response = await client.post(
        f"{USERS}/me/email/verify",
        json={"code": f"{(int(code) + 1) % 1_000_000:06d}"},
        headers=await login_as(member),
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_code"
    assert member.email_verified_at is None


async def test_a_login_code_cannot_confirm_an_email(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    member = await make_user()
    await client.post("/api/v1/auth/login", json={"family_name": member.family_name})

    response = await client.post(
        f"{USERS}/me/email/verify",
        json={"code": fake_email.last_code(member.email)},
        headers=await login_as(member),
    )

    assert response.json()["error"]["code"] == "invalid_code"


async def test_different_capitals_send_no_code(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email="aline@example.com")

    await _change_email(client, admin_headers, member, "Aline@example.com")

    assert fake_email.sent == []


async def test_other_changes_send_no_code(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()

    response = await client.patch(
        f"{USERS}/{member.id}", json={"display_name": "Renamed"}, headers=admin_headers
    )

    assert response.status_code == 200
    assert fake_email.sent == []


async def test_a_failed_email_does_not_undo_the_change(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    fake_email.failures_left = 2

    response = await client.patch(
        f"{USERS}/{member.id}", json={"email": NEW_EMAIL}, headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["email"] == NEW_EMAIL
    assert response.json()["email_verified_at"] is None
    assert await _codes(db_session, member) == 0


# --- Asking for the code again ---


async def test_the_member_can_ask_for_a_new_code(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await _change_email(client, admin_headers, member)
    first = fake_email.last_code(NEW_EMAIL)
    await _age_codes(db_session, member)

    response = await client.post(f"{USERS}/me/email/resend", headers=await login_as(member))

    assert response.status_code == 200
    assert response.json() == {
        "message": "A confirmation code is on its way.",
        "expires_in": 600,
        "resend_after": 60,
    }
    assert len(fake_email.sent_to(NEW_EMAIL)) == 2
    second = fake_email.last_code(NEW_EMAIL)
    # Only the newest code works.
    if first != second:
        old = await client.post(
            f"{USERS}/me/email/verify", json={"code": first}, headers=await login_as(member)
        )
        assert old.json()["error"]["code"] == "invalid_code"


async def test_resending_within_a_minute_is_refused(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, admin_headers: dict[str, str]
) -> None:
    member = await make_user()
    await _change_email(client, admin_headers, member)

    response = await client.post(f"{USERS}/me/email/resend", headers=await login_as(member))

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "code_recently_sent"


async def test_an_unconfirmed_imported_member_can_confirm_too(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser, login_as: LoginAs
) -> None:
    member = await make_user()
    headers = await login_as(member)

    await client.post(f"{USERS}/me/email/resend", headers=headers)
    response = await client.post(
        f"{USERS}/me/email/verify",
        json={"code": fake_email.last_code(member.email)},
        headers=headers,
    )

    assert response.json()["email_verified_at"] is not None


async def test_a_confirmed_email_needs_no_code(
    client: AsyncClient, fake_email: FakeEmailSender, make_user: MakeUser, login_as: LoginAs
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    response = await client.post(f"{USERS}/me/email/resend", headers=await login_as(member))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_already_verified"
    assert fake_email.sent == []


async def test_confirming_needs_a_login(client: AsyncClient) -> None:
    assert (await client.post(f"{USERS}/me/email/resend")).status_code == 401
    assert (
        await client.post(f"{USERS}/me/email/verify", json={"code": "123456"})
    ).status_code == 401


@pytest.mark.parametrize("code", ["12345", "abcdef", ""])
async def test_malformed_codes_are_rejected(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, code: str
) -> None:
    response = await client.post(
        f"{USERS}/me/email/verify", json={"code": code}, headers=await login_as(await make_user())
    )

    assert response.status_code == 422
