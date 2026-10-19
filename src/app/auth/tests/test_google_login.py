from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import UserSession
from app.auth.tests.helpers import post_with_cookie, refresh_cookie
from app.core.fakes import FakeGoogleVerifier
from app.core.google import NotConfiguredGoogleVerifier, get_google_verifier
from app.core.security import decode_access_token, hash_refresh_token
from app.main import app
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
GOOGLE = "/api/v1/auth/google"
PASSWORD = "admin password"
ANDROID_CHROME = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Mobile Safari/537.36"
)


async def test_a_first_time_member_gets_in_and_is_verified(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleVerifier,
    make_user: MakeUser,
) -> None:
    user = await make_user()
    assert user.email_verified_at is None

    response = await client.post(
        GOOGLE,
        json={"id_token": fake_google.token_for(user.email)},
        headers={"User-Agent": ANDROID_CHROME},
    )

    assert response.status_code == 200
    assert user.email_verified_at is not None
    session = await db_session.scalar(select(UserSession).where(UserSession.user_id == user.id))
    assert session is not None
    assert session.device_label == "Chrome on Android"
    claims = decode_access_token(response.json()["access_token"])
    assert (claims.user_id, claims.session_id) == (user.id, session.id)
    cookie = refresh_cookie(response)
    assert cookie is not None
    assert session.token_hash == hash_refresh_token(cookie.value)


async def test_the_session_refreshes_like_any_other(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    user = await make_user()
    response = await client.post(GOOGLE, json={"id_token": fake_google.token_for(user.email)})
    cookie = refresh_cookie(response)
    assert cookie is not None

    refreshed = await post_with_cookie(client, "/api/v1/auth/refresh", cookie.value)

    assert refreshed.status_code == 200


async def test_an_earlier_verification_is_kept(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    verified_at = datetime.now(UTC) - timedelta(days=30)
    user = await make_user(email_verified_at=verified_at)

    response = await client.post(GOOGLE, json={"id_token": fake_google.token_for(user.email)})

    assert response.status_code == 200
    assert user.email_verified_at == verified_at


async def test_the_email_match_ignores_case(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    user = await make_user(email="Ineza.Member@Example.test")

    response = await client.post(
        GOOGLE, json={"id_token": fake_google.token_for("ineza.member@example.test")}
    )

    assert response.status_code == 200
    assert user.email_verified_at is not None


async def test_social_media_managers_need_no_password(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    user = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    response = await client.post(GOOGLE, json={"id_token": fake_google.token_for(user.email)})

    assert response.status_code == 200


async def test_an_admin_with_the_right_password_gets_in(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN, password=PASSWORD)

    response = await client.post(
        GOOGLE, json={"id_token": fake_google.token_for(admin.email), "password": PASSWORD}
    )

    assert response.status_code == 200
    assert admin.email_verified_at is not None


async def test_an_admin_without_a_password_is_asked_for_it(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN, password=PASSWORD)

    response = await client.post(GOOGLE, json={"id_token": fake_google.token_for(admin.email)})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "admin_password_required"
    assert refresh_cookie(response) is None
    assert admin.email_verified_at is None


async def test_an_admin_with_a_wrong_password_is_refused(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    admin = await make_user(role=Role.ADMIN, password=PASSWORD)

    response = await client.post(
        GOOGLE, json={"id_token": fake_google.token_for(admin.email), "password": "wrong"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"
    assert refresh_cookie(response) is None


@pytest.mark.parametrize("deactivated", [False, True], ids=["unknown", "deactivated"])
async def test_an_unregistered_email_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_google: FakeGoogleVerifier,
    make_user: MakeUser,
    deactivated: bool,
) -> None:
    email = "stranger@example.test"
    if deactivated:
        await make_user(email=email, deactivated_at=datetime.now(UTC))

    response = await client.post(GOOGLE, json={"id_token": fake_google.token_for(email)})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "google_account_not_registered"
    assert await db_session.scalar(select(UserSession)) is None
    # No account is ever created by signing in.
    users = await db_session.scalars(select(User).where(User.email == email))
    assert len(list(users)) == int(deactivated)


async def test_a_bad_google_token_is_refused(client: AsyncClient) -> None:
    response = await client.post(GOOGLE, json={"id_token": "not-from-google"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_google_token"


async def test_google_being_down_is_reported(
    client: AsyncClient, fake_google: FakeGoogleVerifier, make_user: MakeUser
) -> None:
    user = await make_user()
    token = fake_google.token_for(user.email)
    fake_google.unavailable = True

    response = await client.post(GOOGLE, json={"id_token": token})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "google_unavailable"


async def test_without_a_client_id_google_sign_in_is_off(client: AsyncClient) -> None:
    app.dependency_overrides[get_google_verifier] = NotConfiguredGoogleVerifier

    response = await client.post(GOOGLE, json={"id_token": "anything"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "google_not_configured"


@pytest.mark.parametrize(
    "body",
    [{}, {"id_token": ""}, {"id_token": "x" * 5000}, {"id_token": "x", "password": ""}],
    ids=["missing", "empty", "huge", "empty-password"],
)
async def test_invalid_input_is_rejected(client: AsyncClient, body: dict[str, str]) -> None:
    response = await client.post(GOOGLE, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
