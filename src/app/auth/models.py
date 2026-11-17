"""Login sessions (one per device), emailed codes, invitation links and admins' password
reset requests."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, func
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
    """A 5-digit code emailed to a user, stored only as an HMAC."""

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


class Invitation(Base):
    """The one invitation link a user may hold, stored only as an HMAC of its token.

    The user's id is the key, so a new invitation replaces the old one (whose link then stops
    working), and using the link deletes the row.
    """

    __tablename__ = "invitations"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(HASH_LENGTH), unique=True)
    # Both set with the app's clock (see CLAUDE.md, "Conventions").
    created_at: Mapped[datetime]
    expires_at: Mapped[datetime]

    def __repr__(self) -> str:
        return f"<Invitation user={self.user_id}>"


class ResetOutcome(StrEnum):
    SENT = "sent"
    DECLINED = "declined"


class PasswordResetRequest(Base):
    """An admin who can't log in, asking the other admins for a new password.

    Open while `resolved_at` is null, and each admin has at most one open request. Another
    admin answers it: approving emails the requester's invitation link, which sets a new
    password, to the address already on their account (decision 77).
    """

    __tablename__ = "password_reset_requests"
    __table_args__ = (
        CheckConstraint("(resolved_at IS NULL) = (outcome IS NULL)", name="outcome_when_resolved"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # All set with the app's clock (see CLAUDE.md, "Conventions").
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None]
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[ResetOutcome | None] = mapped_column(
        Enum(
            ResetOutcome,
            name="outcome",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda outcomes: [outcome.value for outcome in outcomes],
        )
    )

    user: Mapped[User] = relationship(foreign_keys=[user_id], lazy="raise")

    def __repr__(self) -> str:
        return f"<PasswordResetRequest {self.id} user={self.user_id}>"


# One open request per admin: asking again while one is open changes nothing.
Index(
    "uq_password_reset_requests_open_user_id",
    PasswordResetRequest.user_id,
    unique=True,
    postgresql_where=PasswordResetRequest.resolved_at.is_(None),
)

# "The latest code for this user and purpose": used for verification and the resend cooldown.
Index(
    "ix_email_codes_user_id_purpose_created_at",
    EmailCode.user_id,
    EmailCode.purpose,
    EmailCode.created_at,
)
