"""Fixtures for the auth endpoint tests (plain helpers live in `helpers.py`)."""

from collections.abc import Iterator

import pytest
from httpx import AsyncClient

from app.auth.tests.helpers import FRONTEND, RequestCode, SignIn, refresh_cookie
from app.core.config import get_settings
from app.core.fakes import FakeEmailSender
from app.main import app
from app.users.models import User


@pytest.fixture(autouse=True)
def allowed_origin() -> Iterator[str]:
    """Make `FRONTEND` the only allowed origin, whatever CORS_ORIGINS says locally or in CI."""
    settings = get_settings().model_copy(update={"cors_origins": [FRONTEND]})
    app.dependency_overrides[get_settings] = lambda: settings
    yield FRONTEND
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def request_code(client: AsyncClient, fake_email: FakeEmailSender) -> RequestCode:
    """`code = await request_code(user, password=None)`: step one, then read the email."""

    async def _request_code(user: User, *, password: str | None = None) -> str:
        body = {"family_name": user.family_name}
        if password is not None:
            body["password"] = password
        response = await client.post("/api/v1/auth/login", json=body)
        assert response.status_code == 200, response.text
        return fake_email.last_code(user.email)

    return _request_code


@pytest.fixture
def sign_in(client: AsyncClient, request_code: RequestCode) -> SignIn:
    """`refresh_token = await sign_in(user)`: log a device in through the real endpoints."""

    async def _sign_in(user: User) -> str:
        code = await request_code(user)
        response = await client.post(
            "/api/v1/auth/verify", json={"family_name": user.family_name, "code": code}
        )
        assert response.status_code == 200, response.text
        cookie = refresh_cookie(response)
        assert cookie is not None
        return cookie.value

    return _sign_in
