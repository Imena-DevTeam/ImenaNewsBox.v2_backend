"""Business logic and data access."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    CodeRecentlySent,
    InvalidCode,
    InvalidCredentials,
    RefreshConflict,
    SessionEnded,
    TooManyCodes,
)
from app.auth.models import DEVICE_LABEL_MAX_LENGTH, EmailCode, UserSession
from app.core.config import get_settings
from app.core.email import CodePurpose, EmailSender, send_code
from app.core.security import (
    CODE_TTL,
    create_access_token,
    generate_code,
    generate_refresh_token,
    hash_code,
    hash_refresh_token,
    verify_code,
    verify_password,
)
from app.users.models import Role, User

logger = logging.getLogger(__name__)

# How long the token a refresh replaced still gets a "retry" answer instead of revoking the
# session. Long enough for a slow phone, short enough to be useless to a thief.
REFRESH_GRACE: Final = timedelta(seconds=30)
CODE_RESEND_COOLDOWN: Final = timedelta(seconds=60)
CODE_MAX_ATTEMPTS: Final = 5
# Caps guessing: 10 codes x 5 attempts is 50 guesses a day out of a million possible codes.
# It also stops one person's inbox (and our email quota) from being flooded.
CODES_PER_DAY: Final = 10


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


# --- Emailed codes ---


async def issue_code(
    db: AsyncSession, sender: EmailSender, user: User, purpose: CodePurpose, *, to: str
) -> None:
    """Create a code and email it to `to`. Commits.

    Allows one code per minute and `CODES_PER_DAY` per user and purpose. Only the newest code
    works, so a new one replaces any earlier one. If the email can't be sent, the code is
    deleted again, so the user can retry straight away.
    """
    now = datetime.now(UTC)
    # Lock the user's row, so two requests at once can't both get past the limits below.
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())
    recent = list(
        await db.scalars(
            select(EmailCode.created_at)
            .where(
                EmailCode.user_id == user.id,
                EmailCode.purpose == purpose,
                EmailCode.created_at > now - timedelta(days=1),
            )
            .order_by(EmailCode.created_at.desc())
        )
    )
    if recent and now - recent[0] < CODE_RESEND_COOLDOWN:
        raise CodeRecentlySent()
    if len(recent) >= CODES_PER_DAY:
        raise TooManyCodes()

    code = generate_code()
    row = EmailCode(
        user_id=user.id,
        purpose=purpose,
        code_hash=hash_code(code),
        created_at=now,
        expires_at=now + CODE_TTL,
    )
    db.add(row)
    # Commit before sending, so no lock is held while the provider responds. A request that
    # arrives meanwhile sees this code and gets the cooldown error.
    await db.commit()
    try:
        await send_code(sender, to=to, code=code, purpose=purpose)
    except Exception:
        await db.delete(row)
        await db.commit()
        raise


async def consume_code(db: AsyncSession, user: User, purpose: CodePurpose, code: str) -> None:
    """Check `code` against the user's newest code for `purpose`, and mark it used.

    Raises `InvalidCode` if it's wrong, used, expired or out of attempts. A wrong guess is
    counted and committed straight away; a match is only flushed, for the caller to commit.
    """
    now = datetime.now(UTC)
    latest = await db.scalar(
        select(EmailCode)
        .where(EmailCode.user_id == user.id, EmailCode.purpose == purpose)
        .order_by(EmailCode.created_at.desc(), EmailCode.id.desc())
        .limit(1)
        # Locked, so two requests can't both use the code or both spend the same attempt.
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        latest is None
        or latest.consumed_at is not None
        or latest.expires_at <= now
        or latest.attempts >= CODE_MAX_ATTEMPTS
    ):
        raise InvalidCode()
    if not verify_code(code, latest.code_hash):
        latest.attempts += 1
        await db.commit()
        raise InvalidCode()
    latest.consumed_at = now
    await db.flush()


# --- Logging in ---


async def find_active_user(db: AsyncSession, family_name: str) -> User | None:
    """The active user with this family name, ignoring case (uses its unique index)."""
    return await db.scalar(
        select(User).where(
            func.lower(User.family_name) == func.lower(family_name),
            User.deactivated_at.is_(None),
        )
    )


async def start_login(
    db: AsyncSession, sender: EmailSender, *, family_name: str, password: str | None
) -> None:
    """Step one on a new device: email a login code if the details are right.

    Members send only their family name. Admins also send their password, which is checked
    before any code goes out. Unknown or deactivated users, and admins without a password,
    get the same response as everyone else but no email, so the response doesn't reveal which
    accounts exist or who is an admin. A wrong password is the only error, and it's the same
    for unknown users and members (and costs the same Argon2 time).
    """
    user = await find_active_user(db, family_name)
    is_admin = user is not None and user.role.at_least(Role.ADMIN)
    if password is not None:
        admin_hash = user.password_hash if user is not None and is_admin else None
        if not await verify_password(password, admin_hash):
            raise InvalidCredentials()
    if user is None or (is_admin and password is None):
        return
    await issue_code(db, sender, user, CodePurpose.LOGIN, to=user.email)


@dataclass(frozen=True, slots=True)
class SignedIn:
    access_token: str
    access_expires_at: datetime
    # Goes only in the cookie, never in a response body.
    refresh_token: str


async def start_session(db: AsyncSession, user: User, *, device_label: str) -> SignedIn:
    """Log `user` in on this device, once they've proved who they are (a code or Google).

    Shared by every way of logging in. Flushes; the caller commits.
    """
    new = await create_session(db, user, device_label=device_label)
    access_token, expires_at = create_access_token(user.id, new.session.id)
    return SignedIn(
        access_token=access_token, access_expires_at=expires_at, refresh_token=new.refresh_token
    )


def mark_email_verified(user: User) -> None:
    """A code (or Google) reached this address, so it's real. Keeps the first time."""
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)


async def verify_login(
    db: AsyncSession, *, family_name: str, code: str, device_label: str
) -> SignedIn:
    """Step two: check the emailed code and start a session. Commits."""
    user = await find_active_user(db, family_name)
    if user is None:
        raise InvalidCode()
    await consume_code(db, user, CodePurpose.LOGIN, code)
    mark_email_verified(user)
    signed_in = await start_session(db, user, device_label=device_label)
    await db.commit()
    return signed_in


# --- Refreshing ---


async def refresh_session(db: AsyncSession, refresh_token: str) -> SignedIn:
    """Swap a refresh token for a new access token and a new refresh token. Commits.

    The token rotates on every use and the session's 90 days start again. If the token it
    replaced comes back within `REFRESH_GRACE`, that's a second tab refreshing at the same
    moment, so the caller gets `RefreshConflict` and retries with the new cookie. If it comes
    back later, someone else has a copy, so the whole session is revoked.
    """
    now = datetime.now(UTC)
    token_hash = hash_refresh_token(refresh_token)
    row = (
        await db.execute(
            select(UserSession, User)
            .join(User, UserSession.user_id == User.id)
            .where(
                or_(
                    UserSession.token_hash == token_hash,
                    UserSession.previous_token_hash == token_hash,
                )
            )
            # Locked, so two refreshes of one session run one after the other.
            .with_for_update(of=UserSession)
            .execution_options(populate_existing=True)
        )
    ).first()
    if row is None:
        raise SessionEnded()
    session, user = row
    if session.revoked_at is not None or session.expires_at <= now or not user.is_active:
        raise SessionEnded()

    if session.token_hash != token_hash:
        if session.rotated_at is not None and now - session.rotated_at <= REFRESH_GRACE:
            raise RefreshConflict()
        session.revoked_at = now
        await db.commit()
        logger.warning("A replaced refresh token was reused, so session %s was revoked", session.id)
        raise SessionEnded()

    new_token = generate_refresh_token()
    session.previous_token_hash = session.token_hash
    session.token_hash = hash_refresh_token(new_token)
    session.rotated_at = now
    session.last_used_at = now
    session.expires_at = now + timedelta(seconds=get_settings().refresh_token_expires_in)
    access_token, expires_at = create_access_token(user.id, session.id, now=now)
    await db.commit()
    return SignedIn(
        access_token=access_token, access_expires_at=expires_at, refresh_token=new_token
    )


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
