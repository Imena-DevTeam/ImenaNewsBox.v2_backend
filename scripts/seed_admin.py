"""Create the first admin from the FIRST_ADMIN_* settings. Safe to run more than once.

    uv run python scripts/seed_admin.py

It uses the same settings as the app (environment variables, or `.env`), so point
DATABASE_URL at the database to seed. Render's free plan has no shell, so run it from your own
machine against Neon. See docs/infrastructure.md, "Seeding the first admin".
"""

import asyncio
import logging
import sys

from app.core.config import get_settings
from app.core.database.engine import SessionLocal, engine
from app.users.service import FirstAdminError, SeedResult, seed_first_admin

logger = logging.getLogger("seed_admin")


async def main() -> int:
    settings = get_settings()
    try:
        async with SessionLocal() as db:
            result = await seed_first_admin(
                db,
                family_name=settings.first_admin_family_name,
                email=settings.first_admin_email,
                password=settings.first_admin_password.get_secret_value(),
                sub_family=settings.first_admin_sub_family,
            )
    except FirstAdminError as exc:
        logger.error("Not seeded: %s", exc)
        return 1
    finally:
        await engine.dispose()

    if result is SeedResult.CREATED:
        logger.info(
            "Created the admin %r. Log in with the family name and password.",
            settings.first_admin_family_name,
        )
    else:
        logger.info(
            "The admin %r already exists; nothing changed.", settings.first_admin_family_name
        )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # psycopg's async mode can't run on Windows' default (Proactor) event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    sys.exit(asyncio.run(main(), loop_factory=loop_factory))
