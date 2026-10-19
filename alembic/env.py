"""Alembic environment: runs migrations over the async engine.

The database URL is `DATABASE_URL_DIRECT` (Neon's unpooled URL) from `core/config.py`. Tests
pass their own URL through `config.attributes["url"]`, so nothing is read from alembic.ini.
"""

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Every feature's models must be imported here, or autogenerate won't see their tables.
import app.auth.models
import app.users.models
import app.wishes.models  # noqa: F401
from app.core.config import get_settings
from app.core.database.base import Base

config = context.config

# Keep the app's loggers enabled when migrations run inside the test process.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("url")
    return url if isinstance(url, str) else get_settings().database_url_direct


def run_migrations_offline() -> None:
    """Emit the migration SQL as a script instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = create_async_engine(_database_url(), poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    # psycopg's async mode can't run on Windows' default (Proactor) event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(run_async_migrations(), loop_factory=loop_factory)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
