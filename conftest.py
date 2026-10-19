"""Shared pytest fixtures for every test in the repo (feature tests and tests/).

Lives at the repo root so it applies to src/app/<feature>/tests/ as well as tests/.

Database tests run against real Postgres at `TEST_DATABASE_URL`. Once per run, its schema is
reset and migrated to head. Each test then runs inside an outer transaction that is rolled
back afterwards. Sessions join it with savepoints, so services can `commit()` normally and
nothing leaks between tests.
"""

import asyncio
import sys
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.service import create_session
from app.core.config import get_settings
from app.core.database.session import get_session
from app.core.email import get_email_sender
from app.core.fakes import FakeEmailSender, FakeMediaStore, FakeSanity
from app.core.media import get_media_store
from app.core.sanity import get_sanity
from app.core.security import create_access_token, hash_password
from app.main import app
from app.users.models import Role, SubFamily, User

if sys.platform == "win32":
    # psycopg's async mode can't run on Windows' default (Proactor) event loop.
    def pytest_asyncio_loop_factories(
        config: pytest.Config, item: pytest.Item
    ) -> Mapping[str, Callable[[], asyncio.AbstractEventLoop]]:
        return {"selector": asyncio.SelectorEventLoop}


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database URL, reset and migrated to head once per test run."""
    url = get_settings().test_database_url
    if not url:
        pytest.fail(
            "TEST_DATABASE_URL is not set. Start the test database with "
            "`docker compose --profile test up -d db_test` and set it in .env."
        )
    parsed = make_url(url)
    if not parsed.username or not parsed.database:
        pytest.fail(
            "TEST_DATABASE_URL has no user or database name. If it uses ${...} references, "
            "define those variables above it in .env: they only expand from earlier lines."
        )
    database = parsed.database
    if not database.endswith("_test"):
        pytest.fail(
            f"TEST_DATABASE_URL must point at a database ending in '_test' (got {database!r})."
        )

    # Start from an empty schema, so migrations edited during development apply cleanly.
    sync_engine = create_engine(url, poolclass=NullPool)
    with sync_engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    sync_engine.dispose()

    alembic_config = Config(Path(__file__).parent / "alembic.ini")
    alembic_config.attributes["url"] = url
    command.upgrade(alembic_config, "head")
    return url


@pytest.fixture
async def db_connection(database_url: str) -> AsyncIterator[AsyncConnection]:
    """A connection inside an outer transaction that is rolled back after the test."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()
    await engine.dispose()


@pytest.fixture
async def db_session(db_connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    """A session whose commits only release a savepoint inside the test's transaction."""
    session = AsyncSession(
        bind=db_connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    yield session
    await session.close()


@pytest.fixture
async def sub_family(db_session: AsyncSession) -> SubFamily:
    """A sub-family row. The real list is seeded by a later migration; tests make their own."""
    row = SubFamily(name=f"Test sub-family {uuid.uuid4().hex[:8]}")
    db_session.add(row)
    await db_session.flush()
    return row


@pytest.fixture
def make_user(db_session: AsyncSession, sub_family: SubFamily) -> Callable[..., Awaitable[User]]:
    """A factory: `await make_user(role=Role.ADMIN, password="...", display_name="...")`.

    Every field has a unique default, so tests only set what they care about. A password is
    only hashed when given, because hashing is slow.
    """

    async def _make_user(
        *, role: Role = Role.MEMBER, password: str | None = None, **fields: Any
    ) -> User:
        suffix = uuid.uuid4().hex[:8]
        values: dict[str, Any] = {
            "family_name": f"Family{suffix}",
            "display_name": f"Member {suffix}",
            "email": f"member-{suffix}@example.test",
            "sub_family_id": sub_family.id,
            "role": role,
        }
        if password is not None:
            values["password_hash"] = await hash_password(password)
        values.update(fields)
        user = User(**values)
        db_session.add(user)
        await db_session.flush()
        return user

    return _make_user


@pytest.fixture
def login_as(db_session: AsyncSession) -> Callable[[User], Awaitable[dict[str, str]]]:
    """Start a real session for a user and return its `Authorization` header:
    `headers = await login_as(await make_user(role=Role.ADMIN))`."""

    async def _login_as(user: User) -> dict[str, str]:
        new = await create_session(db_session, user, device_label="pytest")
        token, _ = create_access_token(user.id, new.session.id)
        return {"Authorization": f"Bearer {token}"}

    return _login_as


@pytest.fixture
def fake_email() -> FakeEmailSender:
    return FakeEmailSender()


@pytest.fixture
def fake_media() -> FakeMediaStore:
    return FakeMediaStore()


@pytest.fixture
def fake_sanity() -> FakeSanity:
    return FakeSanity()


@pytest.fixture
async def client(
    db_session: AsyncSession,
    fake_email: FakeEmailSender,
    fake_media: FakeMediaStore,
    fake_sanity: FakeSanity,
) -> AsyncIterator[AsyncClient]:
    """An HTTP client for the app. Every request uses the test's session and the fakes above,
    so no test reaches a real email provider, Cloudinary or Sanity."""

    async def _get_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    overrides: dict[Callable[..., Any], Callable[..., Any]] = {
        get_session: _get_test_session,
        get_email_sender: lambda: fake_email,
        get_media_store: lambda: fake_media,
        get_sanity: lambda: fake_sanity,
    }
    app.dependency_overrides.update(overrides)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    for dependency in overrides:
        app.dependency_overrides.pop(dependency, None)
