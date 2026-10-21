"""Birthday wishes: one member wishing another a happy birthday, kept as history."""

import uuid
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base, TimestampMixin
from app.users.models import User

WISH_MESSAGE_MAX_LENGTH: Final = 2000


class Wish(TimestampMixin, Base):
    __tablename__ = "wishes"
    __table_args__ = (
        # One wish per sender, per recipient, per birthday. The database enforces it, so two
        # requests at once can't both get through.
        UniqueConstraint(
            "sender_id", "recipient_id", "year", name="uq_wishes_sender_recipient_year"
        ),
        CheckConstraint("sender_id <> recipient_id", name="not_self"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    sender_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    recipient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    # The year of the birthday it's for, not the year of birth (which stays private).
    year: Mapped[int]
    message: Mapped[str] = mapped_column(String(WISH_MESSAGE_MAX_LENGTH))

    sender: Mapped[User] = relationship(foreign_keys=[sender_id], lazy="raise")
    recipient: Mapped[User] = relationship(foreign_keys=[recipient_id], lazy="raise")

    def __repr__(self) -> str:
        # No message: it's personal.
        return f"<Wish {self.id} {self.sender_id} -> {self.recipient_id} {self.year}>"
