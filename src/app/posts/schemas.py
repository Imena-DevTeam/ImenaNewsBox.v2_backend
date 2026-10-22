"""Pydantic request/response models (DTOs).

Read schemas are lenient: a post made in Sanity Studio may be missing any field (the Studio
requires none), and it should still show instead of breaking the list. Writes through our API
require every field.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.pagination import PageParams


class PostImage(BaseModel):
    url: str | None = None
    width: int | None = None
    height: int | None = None
    # Sanity's cropping data, for frontends that crop around the important part.
    hotspot: dict[str, Any] | None = None
    crop: dict[str, Any] | None = None


class PostAuthor(BaseModel):
    id: str
    name: str | None = None
    image_url: str | None = None


class PostSummary(BaseModel):
    """A post in the list: everything but the body."""

    id: str
    title: str | None = None
    slug: str | None = None
    summary: str | None = None
    # One free-text category (stored in Sanity's `categories` field).
    category: str | None = None
    published_at: datetime | None = None
    image: PostImage | None = None
    author: PostAuthor | None = None


class PostRead(PostSummary):
    body: list[dict[str, Any]] = Field(
        default_factory=list, description="Portable Text blocks, as Sanity stores them."
    )
    updated_at: datetime | None = None


class PostListParams(PageParams):
    category: str | None = Field(
        default=None, max_length=100, description="Only posts in this category (exact match)."
    )


class AuthorRead(BaseModel):
    """A Sanity author, and the member it's linked to (if any)."""

    id: str
    name: str | None = None
    image_url: str | None = None
    user_id: uuid.UUID | None = None
