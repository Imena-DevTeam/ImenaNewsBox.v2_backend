"""Pydantic request/response models (DTOs)."""

import unicodedata
import uuid
from datetime import datetime
from typing import Annotated, Self

from fastapi import UploadFile
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from app.core.pagination import PageParams
from app.resources.models import DESCRIPTION_MAX_LENGTH, TITLE_MAX_LENGTH, ResourceCategory


def _clean_description(description: str) -> str | None:
    """Trim it, keep its line breaks, and turn a blank description into none at all."""
    description = description.replace("\r\n", "\n").replace("\r", "\n").strip()
    if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in description):
        raise ValueError("can't contain control characters")
    if len(description) > DESCRIPTION_MAX_LENGTH:
        raise ValueError(f"must be at most {DESCRIPTION_MAX_LENGTH} characters")
    return description or None


Title = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)
]
# Several lines are fine. Blank (or only spaces) clears it, like null.
Description = Annotated[str, AfterValidator(_clean_description)]


class ResourceUpload(BaseModel):
    """A new document, sent as `multipart/form-data`: the PDF in `file`, and the rest as
    form fields."""

    model_config = ConfigDict(extra="forbid")

    file: UploadFile = Field(description="The PDF, at most 10 MB.")
    title: Title
    description: Description | None = None
    category: ResourceCategory
    featured: bool = False


class ResourceUpdate(BaseModel):
    """Send only what changes. `description` can be null (or blank) to clear it; the other
    fields can't be null. The file is replaced with `PUT /resources/{id}/file`."""

    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    description: Description | None = None
    category: ResourceCategory | None = None
    featured: bool | None = None

    @model_validator(mode="after")
    def _required_fields_stay_set(self) -> Self:
        for name in ("title", "category", "featured"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self


class UploaderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str


class ResourceRead(BaseModel):
    """A document's details. The file itself comes from `GET /resources/{id}/file`; its
    storage ID never leaves the server."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None
    category: ResourceCategory
    featured: bool
    file_name: str = Field(description="The name it downloads under; always ends in .pdf.")
    file_size: int = Field(description="In bytes.")
    uploaded_by: UploaderRead
    created_at: datetime = Field(description="When it was published.")
    updated_at: datetime


class ResourceListParams(PageParams):
    category: ResourceCategory | None = Field(
        default=None, description="Only this category's documents."
    )
    featured: bool | None = Field(
        default=None, description="true: only featured documents; false: only the others."
    )


class FileParams(BaseModel):
    download: bool = Field(
        default=False,
        description="true asks the browser to save the file instead of showing it.",
    )
