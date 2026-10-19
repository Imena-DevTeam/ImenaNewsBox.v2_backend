from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import EmailCode, UserSession
from app.auth.router import REFRESH_COOKIE_PATH
from app.auth.tests.helpers import RequestCode, refresh_cookie
from app.core.config import get_settings
from app.core.security import decode_access_token, hash_refresh_token
from app.main import app
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
VERIFY = "/api/v1/auth/verify"
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


async def test_the_right_code_logs_the_device_in(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    request_code: RequestCode,
) -> None:
    user = await make_user()
    code = await request_code(user)

    response = await client.post(
        VERIFY,
        json={"family_name": user.family_name, "code": code},
        headers={"User-Agent": IPHONE},
    )

    assert response.status_code == 200
    body = response.json()
    settings = get_settings()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_expires_in
    assert set(body) == {"access_token", "token_type", "expires_in"}

    session = await db_session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert session is not None
    assert session.device_label == "Safari on iPhone"
    claims = decode_access_token(body["access_token"])
    assert (claims.user_id, claims.session_id) == (user.id, session.id)

    cookie = refresh_cookie(response)
    assert cookie is not None
    assert session.token_hash == hash_refresh_token(cookie.value)
    assert cookie.value not in response.text


async def test_the_refresh_cookie_is_locked_down(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    user = await make_user()
    code = await request_code(user)

    response = await client.post(VERIFY, json={"family_name": user.family_name, "code": code})

    cookie = refresh_cookie(response)
    assert cookie is not None
    assert cookie["httponly"] is True
    assert cookie["secure"] is True
    assert cookie["samesite"].lower() == "lax"
    assert cookie["path"] == REFRESH_COOKIE_PATH
    assert cookie["domain"] == ""
    assert int(cookie["max-age"]) == get_settings().refresh_token_expires_in


def test_the_cookie_path_is_where_the_auth_routes_are_mounted() -> None:
    assert app.url_path_for("verify") == f"{REFRESH_COOKIE_PATH}/verify"


async def test_the_first_code_verifies_the_email(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    user = await make_user()
    assert user.email_verified_at is None
    code = await request_code(user)

    await client.post(VERIFY, json={"family_name": user.family_name, "code": code})

    assert user.email_verified_at is not None


async def test_an_earlier_verification_is_kept(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    verified_at = datetime.now(UTC) - timedelta(days=30)
    user = await make_user(email_verified_at=verified_at)
    code = await request_code(user)

    await client.post(VERIFY, json={"family_name": user.family_name, "code": code})

    assert user.email_verified_at == verified_at


async def test_an_admin_logs_in_with_password_then_code(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    admin = await make_user(role=Role.ADMIN, password="admin password")
    code = await request_code(admin, password="admin password")

    response = await client.post(VERIFY, json={"family_name": admin.family_name, "code": code})

    assert response.status_code == 200
    assert refresh_cookie(response) is not None


async def test_a_wrong_code_is_refused_and_counted(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    request_code: RequestCode,
) -> None:
    user = await make_user()
    code = await request_code(user)

    response = await client.post(
        VERIFY, json={"family_name": user.family_name, "code": _wrong(code)}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_code"
    assert refresh_cookie(response) is None
    row = await db_session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    assert row is not None
    assert row.attempts == 1
    assert user.email_verified_at is None


async def test_a_code_works_only_once(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    user = await make_user()
    body = {"family_name": user.family_name, "code": await request_code(user)}
    assert (await client.post(VERIFY, json=body)).status_code == 200

    response = await client.post(VERIFY, json=body)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_code"


async def test_a_code_is_tied_to_its_user(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    alice, bob = await make_user(), await make_user()
    code = await request_code(alice)

    response = await client.post(VERIFY, json={"family_name": bob.family_name, "code": code})

    assert response.json()["error"]["code"] == "invalid_code"


async def test_an_unknown_family_name_is_refused(client: AsyncClient) -> None:
    response = await client.post(VERIFY, json={"family_name": "Nobody", "code": "123456"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_code"


async def test_a_user_deactivated_after_the_email_cannot_log_in(
    client: AsyncClient,
    db_session: AsyncSession,
    make_user: MakeUser,
    request_code: RequestCode,
) -> None:
    user = await make_user()
    code = await request_code(user)
    user.deactivated_at = datetime.now(UTC)
    await db_session.flush()

    response = await client.post(VERIFY, json={"family_name": user.family_name, "code": code})

    assert response.json()["error"]["code"] == "invalid_code"


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "١٢٣٤٥٦", ""])
async def test_malformed_codes_are_rejected(client: AsyncClient, code: str) -> None:
    response = await client.post(VERIFY, json={"family_name": "Mugisha", "code": code})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


async def test_spaces_around_the_code_are_ignored(
    client: AsyncClient, make_user: MakeUser, request_code: RequestCode
) -> None:
    user = await make_user()
    code = await request_code(user)

    response = await client.post(
        VERIFY, json={"family_name": user.family_name, "code": f" {code} "}
    )

    assert response.status_code == 200
