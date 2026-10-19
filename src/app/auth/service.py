"""Business logic and data access."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import DEVICE_LABEL_MAX_LENGTH, UserSession
from app.core.config import get_settings
from app.core.security import generate_refresh_token, hash_refresh_token
from app.users.models import User


@dataclass(frozen=True, slots=True)
class NewSession:
    session: UserSession
    # The only copy of the raw token: it goes in the cookie and is never stored.
    refresh_token: str


async def create_session(db: AsyncSession, user: User, *, device_label: str) -> NewSession:
    """Start a session for a user who has just proved who they are. Flushes, doesn't commit."""
    refresh_token = generate_refresh_token()
    lifetime = timedelta(seconds=get_settings().refresh_token_expires_in)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_refresh_token(refresh_token),
        device_label=device_label[:DEVICE_LABEL_MAX_LENGTH],
        expires_at=datetime.now(UTC) + lifetime,
    )
    db.add(session)
    await db.flush()
    return NewSession(session=session, refresh_token=refresh_token)
