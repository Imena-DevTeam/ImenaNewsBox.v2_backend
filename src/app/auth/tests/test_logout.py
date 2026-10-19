from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserSession
from app.auth.router import REFRESH_COOKIE_PATH
from app.auth.service import create_session
from app.auth.tests.helpers import SignIn, post_with_cookie, refresh_cookie
from app.core.security import generate_refresh_token
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
LOGOUT = "/api/v1/auth/logout"
REFRESH = "/api/v1/auth/refresh"


async def _sessions(db: AsyncSession, user: User) -> list[UserSession]:
    rows = await db.scalars(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .order_by(UserSession.created_at, UserSession.id)
        .execution_options(populate_existing=True)
    )
    return list(rows)


def _assert_cookie_cleared(response: Response) -> None:
    cookie = refresh_cookie(response)
    assert cookie is not None
    assert cookie.value == ""
    assert int(cookie["max-age"]) == 0
    assert cookie["path"] == REFRESH_COOKIE_PATH


async def test_logout_revokes_the_session_and_clears_the_cookie(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    token = await sign_in(user)

    response = await post_with_cookie(client, LOGOUT, token)

    assert response.status_code == 204
    assert response.content == b""
    _assert_cookie_cleared(response)
    [session] = await _sessions(db_session, user)
    assert session.revoked_at is not None
    refreshed = await post_with_cookie(client, REFRESH, token)
    assert refreshed.json()["error"]["code"] == "session_ended"


async def test_other_devices_stay_logged_in(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    phone = await sign_in(user)
    # Codes are limited to one a minute, so the laptop's session is made directly.
    laptop = await create_session(db_session, user, device_label="laptop")

    await post_with_cookie(client, LOGOUT, phone)

    phone_session, laptop_session = await _sessions(db_session, user)
    assert phone_session.revoked_at is not None
    assert laptop_session.revoked_at is None
    assert (await post_with_cookie(client, REFRESH, laptop.refresh_token)).status_code == 200


async def test_the_replaced_token_also_logs_out(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    old = await sign_in(user)
    assert (await post_with_cookie(client, REFRESH, old)).status_code == 200

    await post_with_cookie(client, LOGOUT, old)

    [session] = await _sessions(db_session, user)
    assert session.revoked_at is not None


@pytest.mark.parametrize("token", [None, generate_refresh_token()], ids=["no-cookie", "unknown"])
async def test_logout_always_succeeds(client: AsyncClient, token: str | None) -> None:
    response = await post_with_cookie(client, LOGOUT, token)

    assert response.status_code == 204
    _assert_cookie_cleared(response)


@pytest.mark.parametrize("origin", [None, "https://evil.example"], ids=["missing", "other-site"])
async def test_other_origins_cannot_log_a_device_out(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    origin: str | None,
) -> None:
    user = await make_user()
    token = await sign_in(user)

    response = await post_with_cookie(client, LOGOUT, token, origin=origin)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    [session] = await _sessions(db_session, user)
    assert session.revoked_at is None
