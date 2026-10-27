"""Copy v1's MongoDB profiles into `users`. Safe to run more than once.

    uv run --group import python scripts/import_v1.py           # dry run: only reports
    uv run --group import python scripts/import_v1.py --apply   # writes

It reads V1_MONGODB_URI (read-only) and writes to DATABASE_URL, from environment variables
or `.env`. Seed the first admin before the import. See docs/infrastructure.md, "Importing
v1's members".
"""

import argparse
import asyncio
import logging
import sys
from typing import Any

from app.core.config import get_settings
from app.core.database.engine import SessionLocal, engine
from app.users.v1_import import ImportReport, V1ImportError, import_v1_profiles

logger = logging.getLogger("import_v1")

# Mongoose stores the `Profile` model in the `profiles` collection.
COLLECTION = "profiles"
FIELDS = ["name", "familyName", "email", "birthday", "subFam", "profilePic"]


class MongoReadError(Exception):
    pass


def read_profiles(uri: str) -> list[dict[str, Any]]:
    """Every v1 profile, as plain dicts. Only reads."""
    # Imported here: pymongo is only in the `import` dependency group.
    from pymongo import MongoClient
    from pymongo.errors import ConfigurationError, PyMongoError

    try:
        with MongoClient[dict[str, Any]](
            uri, tz_aware=True, serverSelectionTimeoutMS=15_000
        ) as client:
            try:
                database = client.get_default_database()
            except ConfigurationError as exc:
                raise MongoReadError(
                    "V1_MONGODB_URI has no database name: add it after the host, as in "
                    "mongodb+srv://user:password@host/<database>."
                ) from exc
            profiles = []
            for document in database[COLLECTION].find({}, projection=FIELDS):
                # The ObjectId is kept as `legacy_id`, and records when v1 created the profile.
                object_id = document.pop("_id")
                profiles.append(
                    {**document, "_id": str(object_id), "created_at": object_id.generation_time}
                )
            return profiles
    except PyMongoError as exc:
        # Only the type: messages can include the host or connection details.
        raise MongoReadError(f"Couldn't read v1's MongoDB ({type(exc).__name__}).") from exc


def log_report(report: ImportReport) -> None:
    verb = "Created" if report.applied else "Would create"
    logger.info("Read %d v1 profiles.", report.read)
    logger.info("%s %d members: %s", verb, len(report.created), ", ".join(report.created) or "-")
    if report.filled:
        verb = "Filled" if report.applied else "Would fill"
        logger.info(
            "%s empty fields of %d users already in v2: %s",
            verb,
            len(report.filled),
            ", ".join(report.filled),
        )
    if report.unchanged:
        logger.info("Already in v2, nothing to change: %d", len(report.unchanged))
    if report.skipped:
        logger.warning("Skipped %d (fix them in v1, or add them in the app):", len(report.skipped))
        for note in report.skipped:
            logger.warning("  - %s: %s", note.who, note.message)
    if report.notes:
        logger.warning("To check after the import:")
        for note in report.notes:
            logger.warning("  - %s: %s", note.who, note.message)
    if report.photos_on_another_account:
        logger.info(
            "Photos on another Cloudinary account: %d. They stay as links, and replacing one "
            "never deletes it from there.",
            report.photos_on_another_account,
        )
    if not report.applied:
        logger.info("Dry run: nothing was written. Run again with --apply to write.")


async def run(profiles: list[dict[str, Any]], *, apply: bool) -> int:
    settings = get_settings()
    try:
        async with SessionLocal() as db:
            report = await import_v1_profiles(
                db, profiles, cloud_name=settings.cloudinary_cloud_name, apply=apply
            )
    except V1ImportError as exc:
        logger.error("Not imported: %s", exc)
        return 1
    finally:
        await engine.dispose()
    log_report(report)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy v1's MongoDB profiles into users.")
    parser.add_argument(
        "--apply", action="store_true", help="write to the database (without it: a dry run)"
    )
    args = parser.parse_args()

    uri = get_settings().v1_mongodb_uri.get_secret_value()
    if not uri:
        logger.error("Set V1_MONGODB_URI to v1's MongoDB connection string.")
        return 1
    try:
        profiles = read_profiles(uri)
    except MongoReadError as exc:
        logger.error("%s", exc)
        return 1

    # psycopg's async mode can't run on Windows' default (Proactor) event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(run(profiles, apply=args.apply), loop_factory=loop_factory)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
