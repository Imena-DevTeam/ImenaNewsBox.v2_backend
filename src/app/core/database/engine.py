"""The async engine and session factory, built from the pooled `DATABASE_URL`."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings


config = get_settings()

# pool_pre_ping: Neon scales to zero and drops idle connections, so check before reuse.
engine = create_async_engine(config.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
