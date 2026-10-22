"""Business logic and data access.

Posts live in Sanity; there's no posts table. Reads go through one shared in-process cache,
keyed by query and never by user. The router checks the login before anything reaches it, and
every successful write (and the Sanity webhook) clears it. It only works because the free plan
runs a single instance.
"""

import uuid
from typing import Any, Final

from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanity import Sanity
from app.posts.exceptions import PostNotFound, UnknownAuthor
from app.posts.schemas import AuthorRead, PostRead, PostSummary
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
