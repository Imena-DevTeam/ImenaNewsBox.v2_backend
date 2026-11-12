"""Invitation links: sending them (`send_invitation`), and the two public endpoints the
frontend's invitation page calls with the token from the link."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import InvitationsNotConfigured
from app.auth.models import Invitation
from app.auth.router import REFRESH_COOKIE
from app.auth.service import send_invitation
from app.core import config
from app.core.email import EmailNotSent
from app.core.fakes import FakeEmailSender
from app.core.security import INVITATION_TTL, verify_password
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
CHECK = "/api/v1/auth/invitations/check"
ACCEPT = "/api/v1/auth/invitations/accept"
NEW_PASSWORD = "eight ch"


async def _invite(db: AsyncSession, fake_email: FakeEmailSender, user: User) -> str:
    """Send `user` an invitation and return the token from the emailed link."""
    await send_invitation(db, fake_email, user)
    _, token = fake_email.last_invitation_link(user.email)
    return token


async def _invitation(db: AsyncSession, user: User) -> Invitation | None:
    return await db.scalar(
        select(Invitation)
        .where(Invitation.user_id == user.id)
        .execution_options(populate_existing=True)
    )


async def _fresh(db: AsyncSession, user: User) -> User:
    await db.refresh(user)
    return user


# --- Sending ---


async def test_the_email_links_to_the_frontend_page_with_the_token_after_the_hash(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser, frontend_url: str
) -> None:
    member = await make_user(display_name="Aline")

    token = await _invite(db_session, fake_email, member)

    page, _ = fake_email.last_invitation_link(member.email)
    assert page == f"{frontend_url}/invitation"
    message = fake_email.sent_to(member.email)[-1]
    assert message.subject == "You're invited to Imena NewsBox"
    assert "Hello Aline" in message.text
    assert member.family_name in message.text
    assert "7 days" in message.text
    # Only an HMAC of the token is stored.
    invitation = await _invitation(db_session, member)
    assert invitation is not None
    assert token not in invitation.token_hash
    assert invitation.expires_at - invitation.created_at == INVITATION_TTL
    assert member.invited_at == invitation.created_at


async def test_an_admin_is_asked_to_set_a_password(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser, frontend_url: str
) -> None:
    admin = await make_user(role=Role.ADMIN)

    await _invite(db_session, fake_email, admin)

    message = fake_email.sent_to(admin.email)[-1]
    assert message.subject == "Set your Imena NewsBox admin password"
    assert "set your password" in message.text


async def test_a_new_invitation_replaces_the_old_link(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    old = await _invite(db_session, fake_email, member)

    new = await _invite(db_session, fake_email, member)

    assert (await client.post(CHECK, json={"token": old})).status_code == 400
    assert (await client.post(CHECK, json={"token": new})).status_code == 200


async def test_a_failed_email_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)
    invited_at = member.invited_at
    fake_email.failures_left = 2

    with pytest.raises(EmailNotSent):
        await send_invitation(db_session, fake_email, member)

    assert member.invited_at == invited_at
    assert (await client.post(CHECK, json={"token": token})).status_code == 200


async def test_without_a_frontend_url_nothing_is_sent(
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = config.get_settings().model_copy(update={"frontend_url": ""})
    monkeypatch.setattr("app.auth.service.get_settings", lambda: settings)
    member = await make_user()

    with pytest.raises(InvitationsNotConfigured) as exc_info:
        await send_invitation(db_session, fake_email, member)

    assert exc_info.value.status_code == 503
    assert fake_email.sent == []
    assert await _invitation(db_session, member) is None


# --- Checking ---


async def test_check_says_who_the_link_is_for_and_changes_nothing(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user(display_name="Aline")
    token = await _invite(db_session, fake_email, member)

    response = await client.post(CHECK, json={"token": token})

    assert response.status_code == 200
    assert response.json() == {
        "family_name": member.family_name,
        "display_name": "Aline",
        "needs_password": False,
    }
    assert (await _fresh(db_session, member)).email_verified_at is None
    assert await _invitation(db_session, member) is not None


async def test_check_tells_an_admin_to_choose_a_password(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN)
    token = await _invite(db_session, fake_email, admin)

    response = await client.post(CHECK, json={"token": token})

    assert response.json()["needs_password"] is True


# --- Accepting ---


async def test_a_member_accepts_and_their_email_is_verified(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)

    response = await client.post(ACCEPT, json={"token": token})

    assert response.status_code == 204
    assert (await _fresh(db_session, member)).email_verified_at is not None
    assert await _invitation(db_session, member) is None
    # Accepting isn't logging in: they log in afterwards like everyone else.
    assert REFRESH_COOKIE not in response.headers.get("set-cookie", "")
    assert response.content == b""


async def test_the_link_works_once(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)
    await client.post(ACCEPT, json={"token": token})

    again = await client.post(ACCEPT, json={"token": token})
    check = await client.post(CHECK, json={"token": token})

    for response in (again, check):
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_invitation"


async def test_an_earlier_verification_time_is_kept(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    verified_at = datetime(2026, 1, 2, tzinfo=UTC)
    member = await make_user(email_verified_at=verified_at)
    token = await _invite(db_session, fake_email, member)

    await client.post(ACCEPT, json={"token": token})

    assert (await _fresh(db_session, member)).email_verified_at == verified_at


async def test_a_member_cannot_set_a_password(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)

    response = await client.post(ACCEPT, json={"token": token, "password": NEW_PASSWORD})

    assert response.status_code == 204
    assert (await _fresh(db_session, member)).password_hash is None


async def test_an_admin_sets_their_password_and_can_then_log_in(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN)
    token = await _invite(db_session, fake_email, admin)

    response = await client.post(ACCEPT, json={"token": token, "password": NEW_PASSWORD})

    assert response.status_code == 204
    admin = await _fresh(db_session, admin)
    assert admin.email_verified_at is not None
    assert await verify_password(NEW_PASSWORD, admin.password_hash)
    login = await client.post(
        "/api/v1/auth/login", json={"family_name": admin.family_name, "password": NEW_PASSWORD}
    )
    assert login.status_code == 200
    assert fake_email.last_code(admin.email)


async def test_an_admins_link_also_replaces_a_forgotten_password(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN, password="the forgotten one")
    token = await _invite(db_session, fake_email, admin)

    await client.post(ACCEPT, json={"token": token, "password": NEW_PASSWORD})

    admin = await _fresh(db_session, admin)
    assert await verify_password(NEW_PASSWORD, admin.password_hash)
    assert not await verify_password("the forgotten one", admin.password_hash)


async def test_an_admin_must_choose_a_password(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN)
    token = await _invite(db_session, fake_email, admin)

    response = await client.post(ACCEPT, json={"token": token})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "password_required"
    assert (await _fresh(db_session, admin)).email_verified_at is None
    # Nothing was used up: they can try again with a password.
    retry = await client.post(ACCEPT, json={"token": token, "password": NEW_PASSWORD})
    assert retry.status_code == 204


@pytest.mark.parametrize("password", ["seven c", "x" * 257])
async def test_a_password_needs_8_to_256_characters(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    password: str,
) -> None:
    admin = await make_user(role=Role.ADMIN)
    token = await _invite(db_session, fake_email, admin)

    response = await client.post(ACCEPT, json={"token": token, "password": password})

    assert response.status_code == 422
    assert password not in response.text
    assert (await _fresh(db_session, admin)).password_hash is None


# --- Links that don't work ---


async def test_an_unknown_token_is_refused(client: AsyncClient) -> None:
    for path in (CHECK, ACCEPT):
        response = await client.post(path, json={"token": "not-a-real-token"})
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_invitation"


async def test_an_expired_link_is_refused(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)
    invitation = await _invitation(db_session, member)
    assert invitation is not None
    invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    for path in (CHECK, ACCEPT):
        assert (await client.post(path, json={"token": token})).status_code == 400
    assert (await _fresh(db_session, member)).email_verified_at is None


async def test_a_deactivated_users_link_is_refused(
    client: AsyncClient, db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    member = await make_user()
    token = await _invite(db_session, fake_email, member)
    member.deactivated_at = datetime.now(UTC)
    await db_session.flush()

    for path in (CHECK, ACCEPT):
        assert (await client.post(path, json={"token": token})).status_code == 400


@pytest.mark.parametrize("body", [{}, {"token": ""}, {"token": "x" * 129}])
async def test_a_missing_or_oversized_token_is_a_validation_error(
    client: AsyncClient, body: dict[str, str]
) -> None:
    for path in (CHECK, ACCEPT):
        assert (await client.post(path, json=body)).status_code == 422
