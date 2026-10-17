"""The `get_session` FastAPI dependency."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.engine import SessionLocal, engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session per request.

    Services commit explicitly, so a failed commit surfaces as an error response. Anything
    left uncommitted is rolled back when the session closes.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()

        except Exception:
            await session.rollback()
            raise



DbSession = Annotated[AsyncSession, Depends(get_session)]

async def dispose_engine() -> None:
    await engine.dispose()
