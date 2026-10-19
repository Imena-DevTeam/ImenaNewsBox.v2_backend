"""The shared database fixtures: migrations ran, and each test's writes are rolled back."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


async def test_session_queries_the_test_database(db_session: AsyncSession) -> None:
    database = await db_session.scalar(text("SELECT current_database()"))

    assert isinstance(database, str)
    assert database.endswith("_test")


async def test_committed_writes_stay_inside_the_test(
    db_session: AsyncSession, database_url: str
) -> None:
    # Postgres DDL is transactional, so even a committed CREATE TABLE is rolled back.
    await db_session.execute(text("CREATE TABLE rollback_probe (id integer)"))
    await db_session.execute(text("INSERT INTO rollback_probe VALUES (1)"))
    await db_session.commit()

    assert await db_session.scalar(text("SELECT count(*) FROM rollback_probe")) == 1

    # A separate connection can't see it: the "commit" only released a savepoint.
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as other:
        visible = await other.scalar(text("SELECT to_regclass('rollback_probe')"))
    await engine.dispose()

    assert visible is None
