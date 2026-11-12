"""Business logic and data access.

Anyone logged in reads; social media managers and admins write (the router checks the
role). Any of them may change or delete any document: there's no ownership rule here.
"""

import re
import unicodedata
import uuid
from collections.abc import AsyncGenerator
from typing import Final

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.media import MediaStore
from app.resources.exceptions import ResourceNotFound
from app.resources.models import FILE_NAME_MAX_LENGTH, Resource, ResourceCategory
from app.resources.schemas import ResourceUpdate
from app.users.models import User

FALLBACK_FILE_NAME: Final = "document.pdf"
_PDF_EXTENSION: Final = ".pdf"
# Characters that mean something in a path, on any system.
_PATH_CHARACTERS: Final = re.compile(r'[\\/:*?"<>|]')


def clean_file_name(name: str | None) -> str:
    """The name a document downloads under: the uploaded file's own name without any folder,
    control or path characters, ending in .pdf and at most 255 characters."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C")
    name = _PATH_CHARACTERS.sub("_", name).strip(" .")
    stem = name[: -len(_PDF_EXTENSION)] if name.lower().endswith(_PDF_EXTENSION) else name
    stem = stem.strip(" .")[: FILE_NAME_MAX_LENGTH - len(_PDF_EXTENSION)]
    return f"{stem}{_PDF_EXTENSION}" if stem else FALLBACK_FILE_NAME


def _with_uploader(statement: Select[Resource]) -> Select[Resource]:
    return statement.options(selectinload(Resource.uploaded_by)).execution_options(
        populate_existing=True
    )


async def list_resources(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
    category: ResourceCategory | None = None,
    featured: bool | None = None,
) -> tuple[list[Resource], int]:
    """Documents, newest first, optionally of one category or featured or not."""
    conditions = []
    if category is not None:
        conditions.append(Resource.category == category)
    if featured is not None:
        conditions.append(Resource.featured.is_(featured))
    total = await db.scalar(select(func.count()).select_from(Resource).where(*conditions))
    resources = await db.scalars(
        _with_uploader(
            select(Resource)
            .where(*conditions)
            .order_by(Resource.created_at.desc(), Resource.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return list(resources), total or 0


async def get_resource(db: AsyncSession, resource_id: uuid.UUID) -> Resource:
    """Raises `ResourceNotFound`."""
    resource = await db.scalar(_with_uploader(select(Resource).where(Resource.id == resource_id)))
    if resource is None:
        raise ResourceNotFound()
    return resource


async def create_resource(
    db: AsyncSession,
    media: MediaStore,
    uploader: User,
    *,
    data: bytes,
    file_name: str | None,
    title: str,
    description: str | None,
    category: ResourceCategory,
    featured: bool,
) -> Resource:
    """Store a checked PDF (see `core.media.read_document`) and describe it. Commits.

    The file is uploaded first; if saving the row then fails, it's deleted again, so no
    unreachable file is left behind.
    """
    public_id = await media.upload_document(data)
    resource = Resource(
        title=title,
        description=description,
        category=category,
        featured=featured,
        file_name=clean_file_name(file_name),
        file_size=len(data),
        file_public_id=public_id,
        uploaded_by_id=uploader.id,
    )
    db.add(resource)
    try:
        await db.commit()
    except Exception:
        await media.delete_document(public_id)
        raise
    return await get_resource(db, resource.id)


async def update_resource(
    db: AsyncSession, resource_id: uuid.UUID, changes: ResourceUpdate
) -> Resource:
    """Change a document's title, description, category or featured flag. Commits."""
    resource = await get_resource(db, resource_id)
    for name, value in changes.model_dump(exclude_unset=True).items():
        setattr(resource, name, value)
    await db.commit()
    return await get_resource(db, resource.id)


async def replace_file(
    db: AsyncSession,
    media: MediaStore,
    resource_id: uuid.UUID,
    *,
    data: bytes,
    file_name: str | None,
) -> tuple[Resource, str]:
    """Swap a document's PDF for a new one (a corrected version, say). Commits.

    Returns the document and the old file's public ID, for the caller to delete in the
    background. The new file is saved first, so a failed upload never loses the old one.
    """
    resource = await get_resource(db, resource_id)
    public_id = await media.upload_document(data)
    replaced = resource.file_public_id
    resource.file_public_id = public_id
    resource.file_name = clean_file_name(file_name)
    resource.file_size = len(data)
    try:
        await db.commit()
    except Exception:
        await media.delete_document(public_id)
        raise
    return await get_resource(db, resource.id), replaced


async def delete_resource(db: AsyncSession, resource_id: uuid.UUID) -> str:
    """Delete a document. Commits. Returns its file's public ID, for the caller to delete in
    the background (a lost delete only leaves an unreachable file behind)."""
    resource = await get_resource(db, resource_id)
    public_id = resource.file_public_id
    await db.delete(resource)
    await db.commit()
    return public_id


async def open_file(
    db: AsyncSession, media: MediaStore, resource_id: uuid.UUID
) -> tuple[Resource, AsyncGenerator[bytes]]:
    """A document and its PDF's bytes as they arrive from storage. Raises `ResourceNotFound`
    or `MediaUnavailable` before any byte is sent."""
    resource = await get_resource(db, resource_id)
    return resource, await media.open_document(resource.file_public_id)
