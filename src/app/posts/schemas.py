"""Pydantic request/response models (DTOs).

Read schemas are lenient: a post made in Sanity Studio may be missing any field (the Studio
requires none), and it should still show instead of breaking the list. Writes through our API
require every field.
"""

import re
import secrets
import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

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


# --- Writing posts ---
#
# Studio requires no fields, but posts written through our API need all of them. These limits
# are ours, not Sanity's: generous for a family blog, and small enough to stop abuse.

TITLE_MAX_LENGTH: Final = 200
SUMMARY_MAX_LENGTH: Final = 500
CATEGORY_MAX_LENGTH: Final = 100
SLUG_MAX_LENGTH: Final = 200
MAX_BLOCKS: Final = 500
MAX_SPANS_PER_BLOCK: Final = 200
MAX_SPAN_LENGTH: Final = 10_000
MAX_LINKS_PER_BLOCK: Final = 50
HREF_MAX_LENGTH: Final = 2000
LINK_SCHEMES: Final = frozenset({"http", "https", "mailto"})

# What the Studio's body editor offers (see docs/domain.md), stored under Sanity's default
# names. Anything else is refused, so the frontend can render every post it gets.
BlockStyle = Literal["normal", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"]
ListItem = Literal["bullet", "number"]
DECORATORS: Final = frozenset({"strong", "em", "code", "underline", "strike-through"})

Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)
]
Summary = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=SUMMARY_MAX_LENGTH)
]
Category = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=CATEGORY_MAX_LENGTH)
]
# Lowercase words joined by single dashes, e.g. "visit-to-ikuzwe".
Slug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=SLUG_MAX_LENGTH),
]
# Sanity image asset IDs look like "image-<hash>-6048x6048-jpg" (from `POST /posts/images`).
ImageAssetId = Annotated[
    str, StringConstraints(pattern=r"^image-[A-Za-z0-9]+-\d+x\d+-[a-z0-9]+$", max_length=128)
]
# Portable Text keys only need to be unique within their array.
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]+$", max_length=64)]


def slugify(text: str) -> str:
    """A slug from a title: accents dropped, lowercase, everything else turned into dashes.
    Empty if the title has no letters or digits at all (e.g. only emoji)."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].rstrip("-")


def _check_href(href: str) -> str:
    # Only web and email links: a "javascript:" link would run code when clicked.
    scheme = urlsplit(href).scheme.lower()
    if scheme not in LINK_SCHEMES:
        raise ValueError("links must start with http://, https:// or mailto:")
    return href


def _new_key() -> str:
    return secrets.token_hex(6)


class _PortableText(BaseModel):
    # Portable Text names its fields `_type`, `_key`, `markDefs`, `listItem`; Python can't
    # use leading underscores for fields, so they're aliases. Unknown fields are refused.
    model_config = ConfigDict(extra="forbid", validate_by_name=True, serialize_by_alias=True)


class Span(_PortableText):
    type: Literal["span"] = Field(alias="_type")
    key: Key | None = Field(default=None, alias="_key")
    text: str = Field(max_length=MAX_SPAN_LENGTH)
    marks: list[str] = Field(default_factory=list, max_length=20)


class LinkMark(_PortableText):
    type: Literal["link"] = Field(alias="_type")
    key: Key = Field(alias="_key")
    href: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=HREF_MAX_LENGTH),
        AfterValidator(_check_href),
    ]


class Block(_PortableText):
    """One paragraph, heading, quote or list item: text spans, with bold and the like as
    `marks`, and links as `markDefs` that spans point to by key."""

    type: Literal["block"] = Field(alias="_type")
    key: Key | None = Field(default=None, alias="_key")
    style: BlockStyle = "normal"
    children: list[Span] = Field(min_length=1, max_length=MAX_SPANS_PER_BLOCK)
    mark_defs: list[LinkMark] = Field(
        default_factory=list, alias="markDefs", max_length=MAX_LINKS_PER_BLOCK
    )
    list_item: ListItem | None = Field(default=None, alias="listItem")
    level: int | None = Field(default=None, ge=1, le=10)

    @model_validator(mode="after")
    def _check_marks_and_lists(self) -> Self:
        link_keys = [link.key for link in self.mark_defs]
        if len(set(link_keys)) != len(link_keys):
            raise ValueError("each link in a block needs its own _key")
        allowed = DECORATORS | set(link_keys)
        for span in self.children:
            unknown = set(span.marks) - allowed
            if unknown:
                raise ValueError(f"unknown marks: {', '.join(sorted(unknown))}")
        if self.list_item is None and self.level is not None:
            raise ValueError("level is only for list items")
        if self.list_item is not None and self.level is None:
            self.level = 1
        return self


def _check_body(blocks: list[Block]) -> list[Block]:
    if not any(span.text.strip() for block in blocks for span in block.children):
        raise ValueError("the body can't be empty")
    # Sanity needs a unique key on every array item; editors usually send them, and missing
    # ones are filled in here.
    keys = [block.key for block in blocks if block.key is not None]
    if len(set(keys)) != len(keys):
        raise ValueError("each block needs its own _key")
    for block in blocks:
        block.key = block.key or _new_key()
        for span in block.children:
            span.key = span.key or _new_key()
    return blocks


Body = Annotated[
    list[Block], Field(min_length=1, max_length=MAX_BLOCKS), AfterValidator(_check_body)
]


class PostCreate(BaseModel):
    """A new post: every field is required, except `slug` (made from the title) and
    `published_at` (now). Upload the image first with `POST /posts/images`."""

    model_config = ConfigDict(extra="forbid")

    title: Title
    slug: Slug | None = None
    summary: Summary
    category: Category
    published_at: AwareDatetime | None = None
    image_asset_id: ImageAssetId
    body: Body

    @model_validator(mode="after")
    def _slug_from_title(self) -> Self:
        if self.slug is None:
            self.slug = slugify(self.title)
            if not self.slug:
                raise ValueError("slug: this title has no letters or digits, so send a slug")
        return self


class PostUpdate(BaseModel):
    """Send only what changes. Nothing can be cleared: every post field stays required."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    slug: Slug | None = None
    summary: Summary | None = None
    category: Category | None = None
    published_at: AwareDatetime | None = None
    image_asset_id: ImageAssetId | None = None
    body: Body | None = None

    @model_validator(mode="after")
    def _nothing_cleared(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class PostImageUploaded(BaseModel):
    """Send `asset_id` as the post's `image_asset_id`."""

    asset_id: str
    url: str
