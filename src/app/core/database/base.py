"""The single declarative `Base` that every SQLAlchemy model inherits from."""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit constraint names, so Alembic migrations get stable, predictable names instead of
# whatever Postgres generates (which makes later drops and renames guesswork).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Every `Mapped[datetime]` is a `timestamptz`: times are always stored with a time zone.
    type_annotation_map = {datetime: DateTime(timezone=True)}  # noqa: RUF012 (SQLAlchemy reads it once)


class TimestampMixin:
    """`created_at` and `updated_at`, both set by the database."""

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
