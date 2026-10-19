from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import EmailCode, UserSession
from app.auth.service import CODE_LIMIT_WINDOW, ENDED_SESSION_RETENTION
from app.core.email import CodePurpose
from app.core.security import CODE_TTL
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
MINUTE = timedelta(minutes=1)


async def _code(db: AsyncSession, user: User, *, age: timedelta) -> EmailCode:
    created = datetime.now(UTC) - age
    row = EmailCode(
        user_id=user.id,
        purpose=CodePurpose.LOGIN,
        code_hash="x",
        created_at=created,
        expires_at=created + CODE_TTL,
    )
    db.add(row)
    await db.flush()
    return row


async def _session(
    db: AsyncSession,
    user: User,
    label: str,
    *,
    expired: timedelta | None = None,
    revoked: timedelta | None = None,
) -> UserSession:
    now = datetime.now(UTC)
    row = UserSession(
        user_id=user.id,
        token_hash=f"{label:x<64}",
        device_label=label,
        expires_at=now - expired if expired else now + timedelta(days=30),
        revoked_at=now - revoked if revoked else None,
    )
    db.add(row)
    await db.flush()
    return row


async def test_a_login_request_clears_out_old_rows(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user()
    old_code = await _code(db_session, user, age=CODE_LIMIT_WINDOW + MINUTE)
    recent_code = await _code(db_session, user, age=CODE_LIMIT_WINDOW - MINUTE)
    await _session(db_session, user, "active")
    await _session(db_session, user, "just-expired", expired=MINUTE)
    await _session(db_session, user, "just-revoked", revoked=MINUTE)
    await _session(db_session, user, "long-expired", expired=ENDED_SESSION_RETENTION + MINUTE)
    await _session(db_session, user, "long-revoked", revoked=ENDED_SESSION_RETENTION + MINUTE)
    db_session.expunge_all()

    # Any login request does it, even one for a family name that doesn't exist.
    response = await client.post("/api/v1/auth/login", json={"family_name": "Nobody"})

    assert response.status_code == 200
    codes = set(await db_session.scalars(select(EmailCode.id)))
    assert recent_code.id in codes
    assert old_code.id not in codes
    labels = set(
        await db_session.scalars(
            select(UserSession.device_label).where(UserSession.user_id == user.id)
        )
    )
    assert labels == {"active", "just-expired", "just-revoked"}
