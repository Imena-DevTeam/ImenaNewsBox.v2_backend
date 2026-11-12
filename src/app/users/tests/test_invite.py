"""Invitations sent by admins: the manual resend (`POST /users/{id}/invitation`), and the
automatic ones when someone is promoted to admin or their unaccepted invitation's address
changes."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Invitation
from app.auth.service import send_invitation
from app.core.config import get_settings
from app.core.fakes import FakeEmailSender
from app.core.security import verify_password
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
USERS = "/api/v1/users"
CHECK = "/api/v1/auth/invitations/check"
ACCEPT = "/api/v1/auth/invitations/accept"
INVITED = "You're invited to Imena NewsBox"
SET_PASSWORD = "Set your Imena NewsBox admin password"


@pytest.fixture
async def admin_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user(role=Role.ADMIN, password="the admin password"))


def _invite_path(user: User) -> str:
    return f"{USERS}/{user.id}/invitation"


async def _long_ago(db: AsyncSession, user: User) -> None:
    """Pretend the last invitation went out long enough ago for another one."""
    user.invited_at = datetime.now(UTC) - timedelta(hours=1)
    await db.flush()


async def _has_invitation(db: AsyncSession, user: User) -> bool:
    found = await db.scalar(select(Invitation.user_id).where(Invitation.user_id == user.id))
    return found is not None


# --- Sending one again ---


async def test_an_admin_sends_a_member_a_new_link(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await send_invitation(db_session, fake_email, member)
    _, old = fake_email.last_invitation_link(member.email)
    await _long_ago(db_session, member)

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 200
    invited_at = datetime.fromisoformat(response.json()["invited_at"])
    assert datetime.now(UTC) - invited_at < timedelta(minutes=1)
    assert fake_email.sent_to(member.email)[-1].subject == INVITED
    _, new = fake_email.last_invitation_link(member.email)
    assert (await client.post(CHECK, json={"token": old})).status_code == 400
    assert (await client.post(CHECK, json={"token": new})).status_code == 200


async def test_a_member_imported_without_an_invitation_can_get_one(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(legacy_id="0123456789abcdef01234567")

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 200
    assert fake_email.last_invitation_link(member.email)


async def test_an_admin_with_a_password_can_get_a_link_to_reset_it(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    other_admin = await make_user(
        role=Role.ADMIN, password="the forgotten one", email_verified_at=datetime.now(UTC)
    )

    response = await client.post(_invite_path(other_admin), headers=admin_headers)

    assert response.status_code == 200
    assert fake_email.sent_to(other_admin.email)[-1].subject == SET_PASSWORD
    _, token = fake_email.last_invitation_link(other_admin.email)
    await client.post(ACCEPT, json={"token": token, "password": "a new password"})
    await db_session.refresh(other_admin)
    assert await verify_password("a new password", other_admin.password_hash)


async def test_a_member_who_already_accepted_needs_no_link(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invitation_not_needed"
    assert fake_email.sent == []


async def test_a_deactivated_user_gets_no_link(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(deactivated_at=datetime.now(UTC))

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "user_deactivated"
    assert fake_email.sent == []


async def test_a_second_link_within_a_minute_is_refused(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    first = await client.post(_invite_path(member), headers=admin_headers)

    second = await client.post(_invite_path(member), headers=admin_headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "invitation_recently_sent"
    assert len(fake_email.sent_to(member.email)) == 1


async def test_a_failed_email_is_reported_and_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    fake_email.failures_left = 2

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_not_sent"
    await db_session.refresh(member)
    assert member.invited_at is None
    assert not await _has_invitation(db_session, member)


async def test_without_a_frontend_url_the_admin_is_told(
    client: AsyncClient,
    make_user: MakeUser,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings().model_copy(update={"frontend_url": ""})
    monkeypatch.setattr("app.auth.service.get_settings", lambda: settings)
    member = await make_user()

    response = await client.post(_invite_path(member), headers=admin_headers)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "invitations_not_configured"


async def test_an_unknown_user_is_not_found(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(f"{USERS}/{uuid.uuid7()}/invitation", headers=admin_headers)

    assert response.status_code == 404


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_send_links(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
    role: Role,
) -> None:
    member = await make_user()
    headers = await login_as(await make_user(role=role))

    response = await client.post(_invite_path(member), headers=headers)

    assert response.status_code == 403
    assert fake_email.sent == []


async def test_sending_a_link_needs_a_login(client: AsyncClient, make_user: MakeUser) -> None:
    member = await make_user()

    assert (await client.post(_invite_path(member))).status_code == 401


# --- Promotions ---


async def test_a_promoted_admin_is_emailed_a_link_to_set_their_password(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{member.id}", json={"role": "admin"}, headers=admin_headers
    )

    assert response.json()["has_password"] is False
    assert response.json()["invited_at"] is not None
    assert fake_email.sent_to(member.email)[-1].subject == SET_PASSWORD
    _, token = fake_email.last_invitation_link(member.email)
    accepted = await client.post(ACCEPT, json={"token": token, "password": "eight ch"})
    assert accepted.status_code == 204
    await db_session.refresh(member)
    assert await verify_password("eight ch", member.password_hash)


async def test_other_role_changes_send_nothing(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()

    await client.patch(
        f"{USERS}/{member.id}", json={"role": "social_media_manager"}, headers=admin_headers
    )

    assert fake_email.sent == []


async def test_a_deactivated_user_promoted_to_admin_gets_no_email(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(deactivated_at=datetime.now(UTC))

    response = await client.patch(
        f"{USERS}/{member.id}", json={"role": "admin"}, headers=admin_headers
    )

    assert response.json()["role"] == "admin"
    assert fake_email.sent == []


async def test_a_promotion_is_saved_even_if_the_email_fails(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    fake_email.failures_left = 2

    response = await client.patch(
        f"{USERS}/{member.id}", json={"role": "admin"}, headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    assert response.json()["invited_at"] is None


# --- Email changes ---


async def test_an_unaccepted_invitation_follows_a_corrected_address(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email="aline@exmaple.com")
    await send_invitation(db_session, fake_email, member)
    _, old = fake_email.last_invitation_link("aline@exmaple.com")

    response = await client.patch(
        f"{USERS}/{member.id}", json={"email": "aline@example.com"}, headers=admin_headers
    )

    assert response.status_code == 200
    # Straight away: the cooldown is for manual resends only.
    [message] = fake_email.sent_to("aline@example.com")
    assert message.subject == INVITED
    _, new = fake_email.last_invitation_link("aline@example.com")
    # The old link went to the wrong address, so it mustn't confirm the new one.
    assert (await client.post(ACCEPT, json={"token": old})).status_code == 400
    assert (await client.post(ACCEPT, json={"token": new})).status_code == 204


async def test_without_an_invitation_a_new_address_gets_a_code(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    await client.patch(
        f"{USERS}/{member.id}", json={"email": "new.address@example.com"}, headers=admin_headers
    )

    assert fake_email.last_code("new.address@example.com")


async def test_a_promotion_and_a_new_address_share_one_link(
    client: AsyncClient,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user(email_verified_at=datetime.now(UTC))

    await client.patch(
        f"{USERS}/{member.id}",
        json={"role": "admin", "email": "new.address@example.com"},
        headers=admin_headers,
    )

    [message] = fake_email.sent_to("new.address@example.com")
    assert message.subject == SET_PASSWORD


# --- Deactivation ---


async def test_deactivating_someone_cancels_their_link(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    member = await make_user()
    await send_invitation(db_session, fake_email, member)
    _, token = fake_email.last_invitation_link(member.email)

    await client.post(f"{USERS}/{member.id}/deactivate", headers=admin_headers)
    await client.post(f"{USERS}/{member.id}/reactivate", headers=admin_headers)

    assert (await client.post(CHECK, json={"token": token})).status_code == 400
    assert not await _has_invitation(db_session, member)
