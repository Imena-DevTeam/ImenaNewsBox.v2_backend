"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Every route takes `CurrentUser` (or a role) first, so the login is checked before the posts
cache is consulted.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.auth.dependencies import AdminUser, CurrentUser
from app.core.database.session import DbSession
from app.core.pagination import Page
from app.core.sanity import Sanity, get_sanity
from app.posts import service
from app.posts.schemas import AuthorRead, PostListParams, PostRead, PostSummary

router = APIRouter(prefix="/posts", tags=["posts"])

SanityApi = Annotated[Sanity, Depends(get_sanity)]
# Studio's slugs are lowercase words with dashes, at most 200 characters by default.
Slug = Annotated[str, Path(min_length=1, max_length=200)]


@router.get("")
async def list_posts(
    user: CurrentUser, sanity: SanityApi, params: Annotated[PostListParams, Query()]
) -> Page[PostSummary]:
    """Published posts, newest first, without their body. `category` keeps one category's."""
    items, total = await service.list_posts(
        sanity, offset=params.offset, limit=params.limit, category=params.category
    )
    return Page.build(items, params=params, total=total)


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
