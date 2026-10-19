"""Fixtures for the auth endpoint tests (plain helpers live in `helpers.py`)."""

import pytest
from httpx import AsyncClient

from app.auth.tests.helpers import RequestCode
from app.core.fakes import FakeEmailSender
from app.users.models import User


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
