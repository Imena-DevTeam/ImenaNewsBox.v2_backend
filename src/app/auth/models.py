"""Login sessions (one per device) and emailed codes."""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database.base import Base
from app.core.email import CodePurpose
from app.users.models import User

# HMAC-SHA256 hex digests (see `core/security.py`).
HASH_LENGTH: Final = 64
DEVICE_LABEL_MAX_LENGTH: Final = 100


class UserSession(Base):
    """One logged-in device. Only hashes of its refresh tokens are stored.

    On every refresh the token rotates: the new hash replaces `token_hash`, and the old one
    moves to `previous_token_hash` so a reused (likely stolen) token can be recognised.
    """

    __tablename__ = "user_sessions"

    # Also the access token's `sid` claim, so "this device" can be found in the list.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), unique=True)
    previous_token_hash: Mapped[str | None] = mapped_column(String(HASH_LENGTH), unique=True)
    rotated_at: Mapped[datetime | None]
    device_label: Mapped[str] = mapped_column(String(DEVICE_LABEL_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_used_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Pushed back on every refresh, so a device used at least every 90 days stays logged in.
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]

    user: Mapped[User] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<UserSession {self.id} user={self.user_id}>"


class EmailCode(Base):
    """A 6-digit code emailed to a user, stored only as an HMAC."""

    __tablename__ = "email_codes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    purpose: Mapped[CodePurpose] = mapped_column(
        Enum(
            CodePurpose,
            name="code_purpose",
            native_enum=False,
            create_constraint=True,
            length=32,
            values_callable=lambda purposes: [purpose.value for purpose in purposes],
        )
    )
    code_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    attempts: Mapped[int] = mapped_column(default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    consumed_at: Mapped[datetime | None]

    def __repr__(self) -> str:
        return f"<EmailCode {self.id} user={self.user_id} purpose={self.purpose}>"


# "The latest code for this user and purpose": used for verification and the resend cooldown.
Index(
    "ix_email_codes_user_id_purpose_created_at",
    EmailCode.user_id,
    EmailCode.purpose,
    EmailCode.created_at,
)
