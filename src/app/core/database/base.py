"""The single declarative `Base` that every SQLAlchemy model inherits from."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Every model in the application inherits from this one Base."""
