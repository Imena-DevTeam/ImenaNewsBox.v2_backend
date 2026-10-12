
from sqlalchemy import async_sessionmaker, create_async_engine

from app.core.config import get_settings

config = get_settings

engine = create_async_engine(config.database_url, pool_pre_ping=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db_connection():
     async with SessionLocal as session:
        try:
           yield session
           await session.commit()

        except Exception:
           await session.rollback()
           raise
