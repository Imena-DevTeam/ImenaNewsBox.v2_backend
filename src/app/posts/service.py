"""Business logic and data access.

Posts live in Sanity; there's no posts table. Reads go through one shared in-process cache,
keyed by query and never by user. The router checks the login before anything reaches it, and
every successful write (and the Sanity webhook) clears it. It only works because the free plan
runs a single instance.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, Final

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.media import detect_image_format
from app.core.sanity import Sanity
from app.posts.exceptions import (
    PostNotFound,
    SlugTaken,
    UnknownAuthor,
    UnknownImage,
    UnsupportedPostImage,
)
from app.posts.schemas import (
    AuthorRead,
    PostAuthor,
    PostCreate,
    PostImage,
    PostImageUploaded,
    PostRead,
    PostSummary,
)
from app.users import service as users_service
from app.users.exceptions import NotAWriter
from app.users.models import Role, User

CACHE_TTL_SECONDS: Final = 600
CACHE_MAX_ENTRIES: Final = 256

_cache: TTLCache[tuple[object, ...], Any] = TTLCache(
    maxsize=CACHE_MAX_ENTRIES, ttl=CACHE_TTL_SECONDS
)


def clear_cache() -> None:
    """Forget every cached read. Call after every successful write."""
    _cache.clear()


async def _cached_query(
    sanity: Sanity, key: tuple[object, ...], groq: str, params: dict[str, Any]
) -> Any:
    if key in _cache:
        return _cache[key]
    result = await sanity.query(groq, params)
    # "Not found" isn't cached, so made-up slugs can't push real posts out of the cache.
    if result is not None:
        _cache[key] = result
    return result


# --- GROQ ---

# Field names in our API, projected from Sanity's. `categories` holds a single string.
_SUMMARY = """
  "id": _id,
  title,
  "slug": slug.current,
  summary,
  "category": categories,
  "published_at": publishedAt,
  "image": image{
    "url": asset->url,
    "width": asset->metadata.dimensions.width,
    "height": asset->metadata.dimensions.height,
    hotspot,
    crop
  },
  "author": author->{"id": _id, name, "image_url": image.asset->url}
"""
# Only posts with a slug: posts are opened by slug, so one without could never be opened.
_LISTED = (
    '_type == "post" && defined(slug.current) && ($category == null || categories == $category)'
)


async def list_posts(
    sanity: Sanity, *, offset: int, limit: int, category: str | None = None
) -> tuple[list[PostSummary], int]:
    """Published posts, newest first."""
    # offset and limit are validated integers, so they can go straight into the slice.
    groq = (
        f"{{"
        f'"total": count(*[{_LISTED}]),'
        f'"items": *[{_LISTED}] | order(publishedAt desc, _id desc) '
        f"[{int(offset)}...{int(offset) + int(limit)}]{{{_SUMMARY}}}"
        f"}}"
    )
    result = await _cached_query(
        sanity, ("list", offset, limit, category), groq, {"category": category}
    )
    if not isinstance(result, dict):
        return [], 0
    items = [PostSummary.model_validate(item) for item in result.get("items") or []]
    return items, int(result.get("total") or 0)


async def get_post(sanity: Sanity, slug: str) -> PostRead:
    """One published post by its slug. Raises `PostNotFound`."""
    groq = (
        f'*[_type == "post" && slug.current == $slug] | order(_updatedAt desc)[0]'
        f'{{{_SUMMARY}, body, "updated_at": _updatedAt}}'
    )
    result = await _cached_query(sanity, ("post", slug), groq, {"slug": slug})
    if not isinstance(result, dict):
        raise PostNotFound()
    return PostRead.model_validate(result)


# --- Authors ---

_AUTHORS = (
    '*[_type == "author"] | order(lower(name) asc, _id asc)'
    '{"id": _id, name, "image_url": image.asset->url}'
)


async def list_authors(db: AsyncSession, sanity: Sanity) -> list[AuthorRead]:
    """Every published Sanity author by name, with the user each is linked to. Not cached:
    only admins use it, to link authors."""
    authors = await sanity.query(_AUTHORS) or []
    ids = [author["id"] for author in authors]
    linked: dict[str | None, uuid.UUID] = {}
    if ids:
        rows = await db.execute(
            select(User.sanity_author_id, User.id).where(User.sanity_author_id.in_(ids))
        )
        linked = {author_id: user_id for author_id, user_id in rows}
    return [
        AuthorRead.model_validate({**author, "user_id": linked.get(author["id"])})
        for author in authors
    ]


async def link_author(
    db: AsyncSession, sanity: Sanity, user_id: uuid.UUID, author_id: str | None
) -> User:
    """Credit a user's posts to an existing, published Sanity author (None unlinks). Commits.

    Only social media managers and admins can be linked. Raises `UserNotFound`,
    `NotAWriter`, `UnknownAuthor` or `AuthorAlreadyLinked`.
    """
    user = await users_service.get_user(db, user_id, include_deactivated=True)
    if author_id is not None:
        if not user.role.at_least(Role.SOCIAL_MEDIA_MANAGER):
            raise NotAWriter()
        found = await sanity.query('count(*[_type == "author" && _id == $id])', {"id": author_id})
        if not found:
            raise UnknownAuthor()
    return await users_service.set_sanity_author(db, user, author_id)


# --- Images ---

_POST_IMAGE_TYPES: Final = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


async def upload_image(sanity: Sanity, data: bytes) -> PostImageUploaded:
    """Store an image (already checked by `read_image`) in Sanity for a post to use.

    Nothing is cached or changed until a post uses it. An image no post ever uses stays in
    Sanity unused, like a lost Cloudinary delete.
    """
    content_type = _POST_IMAGE_TYPES.get(detect_image_format(data) or "")
    if content_type is None:
        raise UnsupportedPostImage()
    asset = await sanity.upload_image(data, content_type=content_type)
    return PostImageUploaded(asset_id=asset["_id"], url=asset["url"])


# --- Writing ---


async def _write_context(
    sanity: Sanity,
    *,
    post_id: str,
    slug: str | None = None,
    asset_id: str | None = None,
    author_id: str | None = None,
    with_post: bool = False,
) -> dict[str, Any]:
    """What a write needs to know, in one uncached query: whether the slug is free, the
    image asset, the author, and (for edits) the post as it is now. Only the parts asked for
    are queried, so no parameter is ever null."""
    parts: list[str] = []
    params: dict[str, Any] = {"id": post_id}
    if with_post:
        parts.append(
            f'"post": *[_type == "post" && _id == $id][0]'
            f'{{{_SUMMARY}, body, "updated_at": _updatedAt}}'
        )
    if slug is not None:
        parts.append(
            '"slug_taken": count(*[_type == "post" && slug.current == $slug && _id != $id]) > 0'
        )
        params["slug"] = slug
    if asset_id is not None:
        parts.append(
            '"asset": *[_type == "sanity.imageAsset" && _id == $asset][0]'
            '{url, "width": metadata.dimensions.width, "height": metadata.dimensions.height}'
        )
        params["asset"] = asset_id
    if author_id is not None:
        parts.append(
            '"author": *[_type == "author" && _id == $author][0]'
            '{"id": _id, name, "image_url": image.asset->url}'
        )
        params["author"] = author_id
    result = await sanity.query("{" + ", ".join(parts) + "}", params)
    return result if isinstance(result, dict) else {}


def _check_slug_and_image(context: dict[str, Any], *, slug: bool, image: bool) -> None:
    if slug and context.get("slug_taken"):
        raise SlugTaken()
    if image and not context.get("asset"):
        raise UnknownImage()


def _sanity_datetime(value: datetime) -> str:
    """Sanity's datetime format, as the Studio writes it: UTC with milliseconds."""
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def _image_field(asset_id: str) -> dict[str, Any]:
    return {"_type": "image", "asset": {"_type": "reference", "_ref": asset_id}}


def _updated_at(result: dict[str, Any], post_id: str) -> datetime | None:
    # The mutate API returns the saved documents; the fake doesn't.
    for item in result.get("results") or []:
        document = item.get("document") or {}
        if document.get("_id") == post_id and document.get("_updatedAt"):
            return datetime.fromisoformat(document["_updatedAt"])
    return None


def author_id_for(user: User) -> str:
    """The Sanity author a writer's posts are credited to: theirs if linked, otherwise one
    named after their user ID (created with their first post)."""
    return user.sanity_author_id or f"author-{user.id}"


async def create_post(db: AsyncSession, sanity: Sanity, writer: User, data: PostCreate) -> PostRead:
    """Publish a post credited to its writer. Clears the posts cache.

    One Sanity transaction makes sure the writer's author exists, gives it their current
    display name and creates the post, so a failure leaves nothing half-written. A writer
    who wasn't linked to an author is linked to the new one afterwards (`createIfNotExists`
    makes a retry safe if that step fails). Raises `SlugTaken` or `UnknownImage`.
    """
    assert data.slug is not None  # PostCreate fills it in from the title
    post_id = str(uuid.uuid4())
    author_id = author_id_for(writer)
    context = await _write_context(
        sanity, post_id=post_id, slug=data.slug, asset_id=data.image_asset_id, author_id=author_id
    )
    _check_slug_and_image(context, slug=True, image=True)

    published_at = data.published_at or datetime.now(UTC)
    body = [block.model_dump(exclude_none=True) for block in data.body]
    result = await sanity.mutate(
        [
            {
                "createIfNotExists": {
                    "_id": author_id,
                    "_type": "author",
                    "name": writer.display_name,
                }
            },
            {"patch": {"id": author_id, "set": {"name": writer.display_name}}},
            {
                "create": {
                    "_id": post_id,
                    "_type": "post",
                    "title": data.title,
                    "slug": {"_type": "slug", "current": data.slug},
                    "summary": data.summary,
                    "categories": data.category,
                    "publishedAt": _sanity_datetime(published_at),
                    "author": {"_type": "reference", "_ref": author_id},
                    "image": _image_field(data.image_asset_id),
                    "body": body,
                }
            },
        ]
    )
    clear_cache()
    if writer.sanity_author_id is None:
        await users_service.set_sanity_author(db, writer, author_id)

    author = context.get("author") or {}
    return PostRead(
        id=post_id,
        title=data.title,
        slug=data.slug,
        summary=data.summary,
        category=data.category,
        published_at=published_at,
        image=PostImage.model_validate(context["asset"]),
        author=PostAuthor(
            id=author_id, name=writer.display_name, image_url=author.get("image_url")
        ),
        body=body,
        updated_at=_updated_at(result, post_id),
    )
