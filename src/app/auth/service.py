"""Business logic and data access."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

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


# --- Device labels ---

# First match wins, so more specific tokens come first (Edge and Opera also say "Chrome", and
# every Chromium browser also says "Safari").
_BROWSERS: Final = (
    ("Edg", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser/", "Samsung Internet"),
    ("Firefox/", "Firefox"),
    ("FxiOS/", "Firefox"),
    ("CriOS/", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
_SYSTEMS: Final = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("Windows", "Windows"),
    ("CrOS", "ChromeOS"),
    ("Mac OS X", "macOS"),
    ("Linux", "Linux"),
)
UNKNOWN_DEVICE: Final = "Unknown device"


def describe_device(user_agent: str | None) -> str:
    """A short label like "Safari on iPhone" for the sessions list, from the User-Agent.

    Only a hint to help people recognise their devices; it's never used for security.
    """
    if not user_agent:
        return UNKNOWN_DEVICE
    browser = next((name for token, name in _BROWSERS if token in user_agent), None)
    system = next((name for token, name in _SYSTEMS if token in user_agent), None)
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or UNKNOWN_DEVICE
