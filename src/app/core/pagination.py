"""Shared pagination for list endpoints: `?page=&limit=` in, `Page[T]` out.

Usage in a router:

    @router.get("", response_model=Page[UserPublicRead])
    async def list_users(params: Pagination, session: DbSession) -> Page[UserPublicRead]:
        items, total = await service.list_users(session, offset=params.offset, limit=params.limit)
        return Page.build(items, params=params, total=total)
"""

from collections.abc import Sequence
from typing import Annotated, Self

from fastapi import Query
from pydantic import BaseModel, Field

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, description="1-based page number.")
    limit: int = Field(
        default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT, description=f"At most {MAX_LIMIT}."
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.limit


# Declares `page` and `limit` as query parameters on any endpoint that takes it.
Pagination = Annotated[PageParams, Query()]


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    limit: int

    @classmethod
    def build(cls, items: Sequence[T], *, params: PageParams, total: int) -> Self:
        return cls(items=list(items), total=total, page=params.page, limit=params.limit)
