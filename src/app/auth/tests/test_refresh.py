from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserSession
from app.auth.router import REFRESH_COOKIE_PATH
from app.auth.service import REFRESH_GRACE
from app.auth.tests.helpers import SignIn, post_with_cookie, refresh_cookie
from app.core.config import get_settings
from app.core.security import decode_access_token, generate_refresh_token, hash_refresh_token
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
REFRESH = "/api/v1/auth/refresh"


async def _refresh(client: AsyncClient, token: str | None, **kwargs: str | None) -> Response:
    return await post_with_cookie(client, REFRESH, token, **kwargs)


def _new_token(response: Response) -> str:
    assert response.status_code == 200, response.text
    cookie = refresh_cookie(response)
    assert cookie is not None
    return cookie.value


async def _session_of(db: AsyncSession, user: User) -> UserSession:
    session = await db.scalar(
        select(UserSession)
        .where(UserSession.user_id == user.id)
        .execution_options(populate_existing=True)
    )
    assert session is not None
    return session


async def test_refresh_rotates_the_token(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    old = await sign_in(user)
    session = await _session_of(db_session, user)
    first_expiry = session.expires_at

    response = await _refresh(client, old)

    new = _new_token(response)
    assert new != old
    body = response.json()
    assert set(body) == {"access_token", "token_type", "expires_in"}
    claims = decode_access_token(body["access_token"])
    assert (claims.user_id, claims.session_id) == (user.id, session.id)

    session = await _session_of(db_session, user)
    assert session.token_hash == hash_refresh_token(new)
    assert session.previous_token_hash == hash_refresh_token(old)
    assert session.rotated_at is not None
    assert session.last_used_at == session.rotated_at
    assert session.expires_at > first_expiry
    assert session.revoked_at is None


async def test_the_new_cookie_is_locked_down_too(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    response = await _refresh(client, await sign_in(await make_user()))

    cookie = refresh_cookie(response)
    assert cookie is not None
    assert (cookie["httponly"], cookie["secure"]) == (True, True)
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == REFRESH_COOKIE_PATH
    assert cookie["domain"] == ""
    assert int(cookie["max-age"]) == get_settings().refresh_token_expires_in


async def test_each_new_token_can_be_refreshed_again(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    token = await sign_in(await make_user())

    for _ in range(3):
        token = _new_token(await _refresh(client, token))


async def test_two_tabs_refreshing_at_once_get_a_retry(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    old = await sign_in(user)
    new = _new_token(await _refresh(client, old))

    response = await _refresh(client, old)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "refresh_conflict"
    assert refresh_cookie(response) is None
    assert (await _session_of(db_session, user)).revoked_at is None
    # The retry, with the cookie the first tab received, works.
    _new_token(await _refresh(client, new))


async def test_an_old_token_reused_later_revokes_the_session(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    user = await make_user()
    old = await sign_in(user)
    new = _new_token(await _refresh(client, old))
    session = await _session_of(db_session, user)
    assert session.rotated_at is not None
    session.rotated_at -= REFRESH_GRACE + timedelta(seconds=1)
    await db_session.flush()

    response = await _refresh(client, old)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_ended"
    assert (await _session_of(db_session, user)).revoked_at is not None
    # The thief and the owner are both logged out of that session.
    assert (await _refresh(client, new)).json()["error"]["code"] == "session_ended"


async def test_an_unknown_token_is_refused(client: AsyncClient) -> None:
    response = await _refresh(client, generate_refresh_token())

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_ended"


async def test_no_cookie_is_not_authenticated(client: AsyncClient) -> None:
    response = await _refresh(client, None)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"


@pytest.mark.parametrize("origin", [None, "https://evil.example"], ids=["missing", "other-site"])
async def test_other_origins_cannot_refresh(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    origin: str | None,
) -> None:
    user = await make_user()
    token = await sign_in(user)

    response = await _refresh(client, token, origin=origin)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
    assert (await _session_of(db_session, user)).token_hash == hash_refresh_token(token)


@pytest.mark.parametrize("change", ["revoked", "expired", "deactivated"])
async def test_ended_sessions_cannot_refresh(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    change: str,
) -> None:
    user = await make_user()
    token = await sign_in(user)
    past = datetime.now(UTC) - timedelta(seconds=1)
    if change == "deactivated":
        user.deactivated_at = past
    else:
        session = await _session_of(db_session, user)
        if change == "revoked":
            session.revoked_at = past
        else:
            session.expires_at = past
    await db_session.flush()

    response = await _refresh(client, token)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "session_ended"
