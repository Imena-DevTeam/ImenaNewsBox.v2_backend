"""Shared pytest fixtures for every test in the repo (feature tests and tests/).

Lives at the repo root so it applies to src/app/<feature>/tests/ as well as tests/.

Database tests run against real Postgres at `TEST_DATABASE_URL`. Once per run, its schema is
reset and migrated to head. Each test then runs inside an outer transaction that is rolled
back afterwards. Sessions join it with savepoints, so services can `commit()` normally and
nothing leaks between tests.
"""

import asyncio
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database.session import get_session
from app.main import app

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
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """An HTTP client for the app, with every request using the test's session."""

    async def _get_test_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _get_test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.pop(get_session, None)
