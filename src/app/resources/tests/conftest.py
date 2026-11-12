"""Fixtures for the resources tests: a writer (social media manager) and a reader (member)."""

import pytest

from app.resources.tests.helpers import LoginAs, MakeUser
from app.users.models import Role, User


@pytest.fixture
async def writer(make_user: MakeUser) -> User:
    return await make_user(role=Role.SOCIAL_MEDIA_MANAGER, display_name="Ganza")


@pytest.fixture
async def writer_headers(writer: User, login_as: LoginAs) -> dict[str, str]:
    return await login_as(writer)


@pytest.fixture
async def member_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user())
