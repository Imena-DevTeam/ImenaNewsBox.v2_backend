"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Every route takes `CurrentUser` (or a role) first, so the login is checked before the posts
cache is consulted.
"""

import logging
from http import HTTPStatus
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Path, Query, Request, UploadFile

from app.auth.dependencies import AdminUser, CurrentUser, SocialMediaManagerUser
from app.core.config import Settings, get_settings
from app.core.database.session import DbSession
from app.core.media import read_image
from app.core.pagination import Page
from app.core.sanity import (
    WEBHOOK_SIGNATURE_HEADER,
    Sanity,
    get_sanity,
    verify_webhook_signature,
)
from app.posts import service
from app.posts.exceptions import WebhookTooLarge
from app.posts.schemas import (
    AuthorRead,
    PostCreate,
    PostImageUploaded,
    PostListParams,
    PostRead,
    PostSummary,
    PostUpdate,
)
from app.users.schemas import SANITY_ID_PATTERN

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])

SanityApi = Annotated[Sanity, Depends(get_sanity)]
# Studio's slugs are lowercase words with dashes, at most 200 characters by default.
Slug = Annotated[str, Path(min_length=1, max_length=200)]
# Writes use the post's Sanity ID, which never changes (a slug can).
PostId = Annotated[str, Path(pattern=SANITY_ID_PATTERN, max_length=128)]
# Sanity sends the changed document (or only what the webhook's projection asks for).
MAX_WEBHOOK_BYTES: Final = 1024 * 1024


@router.get("")
async def list_posts(
    user: CurrentUser, sanity: SanityApi, params: Annotated[PostListParams, Query()]
) -> Page[PostSummary]:
    """Published posts, newest first, without their body. `category` keeps one category's."""
    items, total = await service.list_posts(
        sanity, offset=params.offset, limit=params.limit, category=params.category
    )
    return Page.build(items, params=params, total=total)


@router.post("", status_code=HTTPStatus.CREATED)
async def create_post(
    body: PostCreate, writer: SocialMediaManagerUser, db: DbSession, sanity: SanityApi
) -> PostRead:
    """Social media managers and admins: publish a post, credited to you. Every field is
    required except `slug` (made from the title) and `published_at` (now)."""
    return await service.create_post(db, sanity, writer, body)


@router.post("/images", status_code=HTTPStatus.CREATED)
async def upload_image(
    image: UploadFile, writer: SocialMediaManagerUser, sanity: SanityApi
) -> PostImageUploaded:
    """Social media managers and admins: upload a post's image (multipart field `image`:
    JPEG, PNG or WebP, at most 5 MB), then send its `asset_id` with the post."""
    return await service.upload_image(sanity, await read_image(image))


@router.post("/webhook", status_code=HTTPStatus.NO_CONTENT)
async def sanity_webhook(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
    """Called by Sanity when a post or author changes (e.g. in the Studio): clears the posts
    cache. No login: the call is checked by its signature instead. The body is never used,
    only signed, so a valid call can do nothing but clear the cache."""
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_WEBHOOK_BYTES:
            raise WebhookTooLarge()
    verify_webhook_signature(
        bytes(body),
        request.headers.get(WEBHOOK_SIGNATURE_HEADER),
        settings.sanity_webhook_secret.get_secret_value(),
    )
    service.clear_cache()
    logger.info("Posts cache cleared by the Sanity webhook")


@router.get("/authors")
async def list_authors(admin: AdminUser, db: DbSession, sanity: SanityApi) -> list[AuthorRead]:
    """Admins: the Sanity authors and the member each is linked to, for linking them
    (`PUT /users/{id}/sanity-author`). Declared before `/{slug}`, so it's never read as a
    post's slug."""
    return await service.list_authors(db, sanity)


@router.get("/{slug}")
async def get_post(slug: Slug, user: CurrentUser, sanity: SanityApi) -> PostRead:
    """One published post, with its body, by its slug."""
    return await service.get_post(sanity, slug)


@router.patch("/{post_id}")
async def update_post(
    post_id: PostId, body: PostUpdate, writer: SocialMediaManagerUser, sanity: SanityApi
) -> PostRead:
    """Social media managers and admins: change any post's fields; send only what changes.
    Its author stays the original writer."""
    return await service.update_post(sanity, post_id, body)


@router.delete("/{post_id}", status_code=HTTPStatus.NO_CONTENT)
async def delete_post(post_id: PostId, writer: SocialMediaManagerUser, sanity: SanityApi) -> None:
    """Social media managers and admins: delete any post."""
    await service.delete_post(sanity, post_id)
