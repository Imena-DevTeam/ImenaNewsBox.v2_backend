"""Admins' password reset requests: a locked-out admin asks, another admin answers."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Invitation, PasswordResetRequest, ResetOutcome
from app.auth.service import RESET_REQUEST_RETENTION, delete_stale_rows
from app.core.fakes import FakeEmailSender
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
REQUESTS = "/api/v1/auth/reset-requests"
PASSWORD = "an admin password"
SET_PASSWORD = "Set your Imena NewsBox admin password"


async def _admin(make_user: MakeUser, **fields: object) -> User:
    return await make_user(role=Role.ADMIN, password=PASSWORD, **fields)


async def _ask(client: AsyncClient, user: User) -> None:
    response = await client.post(REQUESTS, json={"family_name": user.family_name})
    assert response.status_code == 202, response.text


async def _requests(db: AsyncSession, user: User) -> list[PasswordResetRequest]:
    rows = await db.scalars(
        select(PasswordResetRequest)
        .where(PasswordResetRequest.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _open_request_id(db: AsyncSession, user: User) -> uuid.UUID:
    [request] = [row for row in await _requests(db, user) if row.resolved_at is None]
    return request.id


# --- Asking ---


async def test_an_admin_asks_and_the_other_admins_are_emailed(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
) -> None:
    locked_out = await _admin(make_user)
    other = await _admin(make_user)
    third = await _admin(make_user)

    await _ask(client, locked_out)

    [request] = await _requests(db_session, locked_out)
    assert request.resolved_at is None
    assert {message.to for message in fake_email.sent} == {other.email, third.email}
    message = fake_email.sent_to(other.email)[0]
    assert locked_out.display_name in message.subject
    assert locked_out.family_name in message.text
    assert "https://newsbox.example/admin" in message.text


async def test_the_answer_is_the_same_for_everybody(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
) -> None:
    admin = await _admin(make_user)
    await _admin(make_user)
    member = await make_user()
    gone = await make_user(role=Role.ADMIN, password=PASSWORD, deactivated_at=datetime.now(UTC))

    answers = [
        await client.post(REQUESTS, json={"family_name": name})
        for name in (admin.family_name, member.family_name, gone.family_name, "Nobody")
    ]

    assert {response.status_code for response in answers} == {202}
    assert len({response.text for response in answers}) == 1
    assert await _requests(db_session, member) == []
    assert await _requests(db_session, gone) == []
    assert all(message.to != member.email for message in fake_email.sent)


async def test_asking_again_while_one_is_open_changes_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
) -> None:
    admin = await _admin(make_user)
    await _admin(make_user)
    await _ask(client, admin)
    sent = len(fake_email.sent)

    await _ask(client, admin)

    assert len(await _requests(db_session, admin)) == 1
    assert len(fake_email.sent) == sent


async def test_a_failed_email_still_saves_the_request(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
) -> None:
    admin = await _admin(make_user)
    await _admin(make_user)
    fake_email.failures_left = 2

    await _ask(client, admin)

    assert len(await _requests(db_session, admin)) == 1
    assert fake_email.sent == []


async def test_the_family_name_ignores_case_and_spaces(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    admin = await _admin(make_user)
    await _admin(make_user)

    response = await client.post(REQUESTS, json={"family_name": f"  {admin.family_name.upper()}"})

    assert response.status_code == 202
    assert len(await _requests(db_session, admin)) == 1


# --- Listing ---


async def test_admins_see_the_others_open_requests_but_not_their_own(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    first = await _admin(make_user)
    second = await _admin(make_user)
    await _ask(client, first)
    await _ask(client, second)

    response = await client.get(REQUESTS, headers=await login_as(first))

    assert response.status_code == 200
    [item] = response.json()
    assert item["user"] == {
        "id": str(second.id),
        "family_name": second.family_name,
        "display_name": second.display_name,
        "email": second.email,
    }
    assert "requested_at" in item


async def test_a_request_from_someone_no_longer_an_admin_is_not_listed(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    viewer = await _admin(make_user)
    asker = await _admin(make_user)
    await _admin(make_user)
    await _ask(client, asker)
    asker.role = Role.MEMBER
    await db_session.flush()

    response = await client.get(REQUESTS, headers=await login_as(viewer))

    assert response.json() == []


async def test_only_admins_list_requests(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    writer = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    assert (await client.get(REQUESTS)).status_code == 401
    response = await client.get(REQUESTS, headers=await login_as(writer))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"


# --- Approving ---


async def test_approving_emails_a_password_link_to_the_address_on_record(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    approver = await _admin(make_user)
    asker = await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    fake_email.sent.clear()

    response = await client.post(
        f"{REQUESTS}/{request_id}/approve", headers=await login_as(approver)
    )

    assert response.status_code == 204
    [message] = fake_email.sent
    assert message.to == asker.email
    assert message.subject == SET_PASSWORD
    page, token = fake_email.last_invitation_link(asker.email)
    assert page == "https://newsbox.example/invitation"
    assert token
    [request] = await _requests(db_session, asker)
    assert request.outcome is ResetOutcome.SENT
    assert request.resolved_by_id == approver.id
    assert await db_session.get(Invitation, asker.id) is not None


async def test_an_admin_cannot_answer_their_own_request(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    asker = await _admin(make_user)
    await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    headers = await login_as(asker)

    for action in ("approve", "decline"):
        response = await client.post(f"{REQUESTS}/{request_id}/{action}", headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "cannot_answer_own_reset_request"
    [request] = await _requests(db_session, asker)
    assert request.resolved_at is None


async def test_an_answered_request_cannot_be_answered_again(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    approver = await _admin(make_user)
    asker = await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    headers = await login_as(approver)
    declined = await client.post(f"{REQUESTS}/{request_id}/decline", headers=headers)
    assert declined.status_code == 204, declined.text

    response = await client.post(f"{REQUESTS}/{request_id}/approve", headers=headers)

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "reset_request_not_found"


async def test_an_unknown_request_is_not_found(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    response = await client.post(
        f"{REQUESTS}/{uuid.uuid7()}/approve", headers=await login_as(await _admin(make_user))
    )

    assert response.status_code == 404


async def test_a_link_sent_a_moment_ago_is_not_sent_again(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    approver = await _admin(make_user)
    asker = await _admin(make_user, invited_at=datetime.now(UTC))
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)

    response = await client.post(
        f"{REQUESTS}/{request_id}/approve", headers=await login_as(approver)
    )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "invitation_recently_sent"


async def test_a_failed_email_leaves_the_request_open(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    approver = await _admin(make_user)
    asker = await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    fake_email.failures_left = 2

    response = await client.post(
        f"{REQUESTS}/{request_id}/approve", headers=await login_as(approver)
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "email_not_sent"
    [request] = await _requests(db_session, asker)
    assert request.resolved_at is None


async def test_only_admins_answer_requests(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    asker = await _admin(make_user)
    await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    writer = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    for action in ("approve", "decline"):
        path = f"{REQUESTS}/{request_id}/{action}"
        assert (await client.post(path)).status_code == 401
        assert (await client.post(path, headers=await login_as(writer))).status_code == 403


# --- Declining ---


async def test_declining_closes_the_request_and_sends_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    decliner = await _admin(make_user)
    asker = await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    fake_email.sent.clear()

    response = await client.post(
        f"{REQUESTS}/{request_id}/decline", headers=await login_as(decliner)
    )

    assert response.status_code == 204
    assert fake_email.sent == []
    [request] = await _requests(db_session, asker)
    assert request.outcome is ResetOutcome.DECLINED
    assert request.resolved_by_id == decliner.id


async def test_after_an_answer_the_admin_can_ask_again(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    decliner = await _admin(make_user)
    asker = await _admin(make_user)
    await _ask(client, asker)
    request_id = await _open_request_id(db_session, asker)
    declined = await client.post(
        f"{REQUESTS}/{request_id}/decline", headers=await login_as(decliner)
    )
    assert declined.status_code == 204, declined.text

    await _ask(client, asker)

    requests = await _requests(db_session, asker)
    assert len(requests) == 2
    assert sum(request.resolved_at is None for request in requests) == 1


# --- Cleanup ---


async def test_old_answered_requests_are_deleted(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    asker = await _admin(make_user)
    answerer = await _admin(make_user)
    long_ago = datetime.now(UTC) - RESET_REQUEST_RETENTION - timedelta(days=1)
    db_session.add_all(
        [
            PasswordResetRequest(
                user_id=asker.id,
                created_at=long_ago,
                resolved_at=long_ago,
                resolved_by_id=answerer.id,
                outcome=ResetOutcome.DECLINED,
            ),
            PasswordResetRequest(user_id=asker.id, created_at=long_ago),
        ]
    )
    await db_session.flush()

    await delete_stale_rows(db_session)

    [kept] = await _requests(db_session, asker)
    assert kept.resolved_at is None
