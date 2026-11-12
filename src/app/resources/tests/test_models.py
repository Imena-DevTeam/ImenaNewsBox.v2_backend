"""What the database itself guarantees about documents."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.resources.models import Resource, ResourceCategory
from app.users.models import User


def _resource(uploader: User, public_id: str) -> Resource:
    return Resource(
        title="Charter",
        category=ResourceCategory.DOCUMENTS,
        file_name="charter.pdf",
        file_size=10,
        file_public_id=public_id,
        uploaded_by_id=uploader.id,
    )


async def test_defaults(db_session: AsyncSession, writer: User) -> None:
    resource = _resource(writer, "test/one")
    db_session.add(resource)
    await db_session.flush()
    await db_session.refresh(resource)

    assert resource.featured is False
    assert resource.description is None
    assert resource.created_at is not None


async def test_each_file_belongs_to_one_document(db_session: AsyncSession, writer: User) -> None:
    db_session.add(_resource(writer, "test/same"))
    await db_session.flush()
    db_session.add(_resource(writer, "test/same"))

    with pytest.raises(IntegrityError, match="uq_resources_file_public_id"):
        await db_session.flush()


async def test_only_known_categories_are_stored(db_session: AsyncSession, writer: User) -> None:
    db_session.add(_resource(writer, "test/cat"))
    await db_session.flush()

    with pytest.raises(IntegrityError, match="ck_resources_category"):
        await db_session.execute(text("UPDATE resources SET category = 'recipes'"))
