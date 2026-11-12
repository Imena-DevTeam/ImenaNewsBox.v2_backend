"""HTTP routes: parse input, call the service, shape the response. No business logic here."""

import uuid
from http import HTTPStatus
from typing import Annotated, Final
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, UploadFile
from fastapi.responses import StreamingResponse

from app.auth.dependencies import CurrentUser, SocialMediaManagerUser
from app.core.database.session import DbSession
from app.core.media import MediaStore, get_media_store, read_document
from app.core.pagination import Page
from app.resources import service
from app.resources.schemas import (
    FileParams,
    ResourceListParams,
    ResourceRead,
    ResourceUpdate,
    ResourceUpload,
)

router = APIRouter(prefix="/resources", tags=["resources"])

Media = Annotated[MediaStore, Depends(get_media_store)]
PDF: Final = "application/pdf"


@router.get("")
async def list_resources(
    user: CurrentUser, db: DbSession, params: Annotated[ResourceListParams, Query()]
) -> Page[ResourceRead]:
    """The family's documents, newest first. `category` and `featured` narrow the list."""
    resources, total = await service.list_resources(
        db,
        offset=params.offset,
        limit=params.limit,
        category=params.category,
        featured=params.featured,
    )
    items = [ResourceRead.model_validate(resource) for resource in resources]
    return Page.build(items, params=params, total=total)


@router.post("", status_code=HTTPStatus.CREATED)
async def upload_resource(
    body: Annotated[ResourceUpload, Form()],
    user: SocialMediaManagerUser,
    db: DbSession,
    media: Media,
) -> ResourceRead:
    """Social media managers and admins: share a PDF (at most 10 MB), sent as
    `multipart/form-data` with `file`, `title`, `category`, and optionally `description` and
    `featured`."""
    data = await read_document(body.file)
    resource = await service.create_resource(
        db,
        media,
        user,
        data=data,
        file_name=body.file.filename,
        title=body.title,
        description=body.description,
        category=body.category,
        featured=body.featured,
    )
    return ResourceRead.model_validate(resource)


@router.get("/{resource_id}")
async def get_resource(resource_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ResourceRead:
    """One document's details."""
    return ResourceRead.model_validate(await service.get_resource(db, resource_id))


@router.get(
    "/{resource_id}/file",
    response_class=StreamingResponse,
    responses={HTTPStatus.OK: {"content": {PDF: {}}, "description": "The PDF."}},
)
async def download_file(
    resource_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    media: Media,
    params: Annotated[FileParams, Query()],
) -> StreamingResponse:
    """The PDF itself, streamed from private storage: shown in the browser, or saved with
    `?download=true`. It needs the `Authorization` header like every other call, so the
    frontend fetches it and shows it from a blob."""
    resource, chunks = await service.open_file(db, media, resource_id)
    disposition = "attachment" if params.download else "inline"
    return StreamingResponse(
        chunks,
        media_type=PDF,
        headers={
            "Content-Disposition": _content_disposition(disposition, resource.file_name),
            # Never let a browser guess another type from the bytes.
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/{resource_id}")
async def update_resource(
    resource_id: uuid.UUID, body: ResourceUpdate, user: SocialMediaManagerUser, db: DbSession
) -> ResourceRead:
    """Social media managers and admins: change a document's details. Send only what
    changes."""
    return ResourceRead.model_validate(await service.update_resource(db, resource_id, body))


@router.put("/{resource_id}/file")
async def replace_file(
    resource_id: uuid.UUID,
    file: UploadFile,
    user: SocialMediaManagerUser,
    db: DbSession,
    media: Media,
    background: BackgroundTasks,
) -> ResourceRead:
    """Social media managers and admins: replace a document's PDF (multipart field `file`,
    at most 10 MB), keeping its details. The old file is deleted afterwards."""
    data = await read_document(file)
    resource, replaced = await service.replace_file(
        db, media, resource_id, data=data, file_name=file.filename
    )
    background.add_task(media.delete_document, replaced)
    return ResourceRead.model_validate(resource)


@router.delete("/{resource_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_resource(
    resource_id: uuid.UUID,
    user: SocialMediaManagerUser,
    db: DbSession,
    media: Media,
    background: BackgroundTasks,
) -> None:
    """Social media managers and admins: delete a document and, afterwards, its file."""
    public_id = await service.delete_resource(db, resource_id)
    background.add_task(media.delete_document, public_id)


def _content_disposition(disposition: str, file_name: str) -> str:
    """The header, with the name both as plain ASCII (for old clients) and in full UTF-8
    (RFC 6266), so a name like "Itegeko nshinga ry'umuryango.pdf" survives."""
    fallback = file_name.encode("ascii", "replace").decode().replace("?", "_")
    fallback = fallback.replace("\\", "_").replace('"', "_")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name, safe='')}"
