"""Async engine, session factory, the shared declarative `Base` and the `get_session` dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Every model in the application inherits from this one Base."""


# pool_pre_ping: Neon scales to zero and drops idle connections, so check before reuse.
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session per request.

    Services commit explicitly, so a failed commit surfaces as an error response. Anything
    left uncommitted is rolled back when the session closes.
    """
    async with SessionLocal() as session:
        yield session
