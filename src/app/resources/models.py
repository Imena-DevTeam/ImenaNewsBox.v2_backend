"""Family resources: one row per shared PDF. The file itself is a private Cloudinary asset."""

import uuid
from enum import StrEnum
from typing import Final

from sqlalchemy import Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base, TimestampMixin
from app.users.models import User

TITLE_MAX_LENGTH: Final = 200
DESCRIPTION_MAX_LENGTH: Final = 2000
FILE_NAME_MAX_LENGTH: Final = 255


class ResourceCategory(StrEnum):
    """The fixed list of categories, from the frontend's Resources page. Changed by a PR (and
    a migration for the check constraint), never by users."""

    DOCUMENTS = "documents"
    GUIDES = "guides"
    FAMILY_RESOURCES = "family_resources"
    EDUCATIONAL_MATERIALS = "educational_materials"


class Resource(TimestampMixin, Base):
    __tablename__ = "resources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    description: Mapped[str | None] = mapped_column(String(DESCRIPTION_MAX_LENGTH))
    category: Mapped[ResourceCategory] = mapped_column(
        Enum(
            ResourceCategory,
            name="category",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=lambda categories: [category.value for category in categories],
        )
    )
    # Shown in the page's spotlight. Any number can be featured.
    featured: Mapped[bool] = mapped_column(default=False, server_default="false")

    # The name it's downloaded under (cleaned up, always ending in .pdf) and its size in bytes.
    file_name: Mapped[str] = mapped_column(String(FILE_NAME_MAX_LENGTH))
    file_size: Mapped[int]
    # The private Cloudinary asset. Never sent to clients: files are streamed by the API.
    file_public_id: Mapped[str] = mapped_column(String(255), unique=True)

    # Who uploaded it. Users are never deleted, so this always points somewhere.
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    uploaded_by: Mapped[User] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<Resource {self.id} {self.category}>"


# The list is newest first, often within one category.
Index("ix_resources_category_created_at", Resource.category, Resource.created_at)
