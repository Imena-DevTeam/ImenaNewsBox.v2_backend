"""The `get_session` FastAPI dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.engine import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session per request.

    Services commit explicitly, so a failed commit surfaces as an error response. Anything
    left uncommitted is rolled back when the session closes.
    """
    async with SessionLocal() as session:
        yield session
