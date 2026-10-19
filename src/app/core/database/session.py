"""The `get_session` FastAPI dependency."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database.engine import SessionLocal, engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session per request.

    Services commit explicitly, so a failed commit surfaces as an error response. This never
    commits: anything left uncommitted is rolled back when the session closes (decision 31).
    """
    async with SessionLocal() as session:
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


async def dispose_engine() -> None:
    await engine.dispose()
