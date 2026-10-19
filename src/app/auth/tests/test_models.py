from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import DEVICE_LABEL_MAX_LENGTH, EmailCode, UserSession
from app.auth.service import create_session
from app.core.config import get_settings
from app.core.email import CodePurpose
from app.core.security import generate_code, hash_code, hash_refresh_token
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]


async def test_create_session_stores_only_the_hash(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user()

    new = await create_session(db_session, user, device_label="Safari on iPhone")

    session = new.session
    assert session.id.version == 7
    assert session.token_hash == hash_refresh_token(new.refresh_token)
    assert new.refresh_token not in session.token_hash
    assert session.previous_token_hash is None
    assert session.revoked_at is None
    assert session.device_label == "Safari on iPhone"
    assert session.created_at.tzinfo is not None


async def test_session_lasts_the_refresh_lifetime(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    before = datetime.now(UTC)

    new = await create_session(db_session, await make_user(), device_label="x")

    lifetime = timedelta(seconds=get_settings().refresh_token_expires_in)
    assert before + lifetime <= new.session.expires_at <= datetime.now(UTC) + lifetime


async def test_long_device_labels_are_cut(db_session: AsyncSession, make_user: MakeUser) -> None:
    new = await create_session(db_session, await make_user(), device_label="x" * 500)

    assert len(new.session.device_label) == DEVICE_LABEL_MAX_LENGTH


async def test_each_session_gets_its_own_token(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user()

    first = await create_session(db_session, user, device_label="phone")
    second = await create_session(db_session, user, device_label="laptop")

    assert first.refresh_token != second.refresh_token
    assert first.session.id != second.session.id


async def test_token_hashes_are_unique(db_session: AsyncSession, make_user: MakeUser) -> None:
    user = await make_user()
    first = await create_session(db_session, user, device_label="phone")

    with pytest.raises(IntegrityError, match="uq_user_sessions_token_hash"):
        async with db_session.begin_nested():
            db_session.add(
                UserSession(
                    user_id=user.id,
                    token_hash=first.session.token_hash,
                    device_label="copy",
                    expires_at=datetime.now(UTC),
                )
            )
            await db_session.flush()


async def test_email_code_defaults(db_session: AsyncSession, make_user: MakeUser) -> None:
    user = await make_user()
    code = EmailCode(
        user_id=user.id,
        purpose=CodePurpose.LOGIN,
        code_hash=hash_code(generate_code()),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db_session.add(code)
    await db_session.flush()

    assert code.attempts == 0
    assert code.consumed_at is None
    stored = await db_session.scalar(
        text("SELECT purpose FROM email_codes WHERE id = :id"), {"id": code.id}
    )
    assert stored == "login"


async def test_the_database_rejects_unknown_code_purposes(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user()

    with pytest.raises(IntegrityError, match="ck_email_codes_code_purpose"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO email_codes (id, user_id, purpose, code_hash, expires_at)"
                    " VALUES (gen_random_uuid(), :user_id, 'reset', 'x', now())"
                ),
                {"user_id": user.id},
            )
