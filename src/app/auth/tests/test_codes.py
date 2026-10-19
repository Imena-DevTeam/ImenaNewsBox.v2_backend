from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import CodeRecentlySent, InvalidCode, TooManyCodes
from app.auth.models import EmailCode
from app.auth.service import CODE_MAX_ATTEMPTS, CODES_PER_DAY, consume_code, issue_code
from app.core.email import CodePurpose, EmailNotConfigured, EmailNotSent, NotConfiguredEmailSender
from app.core.fakes import FakeEmailSender
from app.core.security import CODE_TTL, hash_code
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
LOGIN = CodePurpose.LOGIN


async def _issue(db: AsyncSession, email: FakeEmailSender, user: User) -> str:
    await issue_code(db, email, user, LOGIN, to=user.email)
    return email.last_code(user.email)


async def _age_codes(db: AsyncSession, user: User, by: timedelta) -> None:
    """Pretend the user's codes were created (and expire) `by` earlier."""
    await db.execute(
        update(EmailCode)
        .where(EmailCode.user_id == user.id)
        .values(created_at=EmailCode.created_at - by, expires_at=EmailCode.expires_at - by)
    )


async def _code_count(db: AsyncSession, user: User) -> int:
    count = await db.scalar(select(func.count()).where(EmailCode.user_id == user.id))
    return count or 0


# --- Issuing ---


async def test_issue_emails_a_code_and_stores_only_its_hash(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    before = datetime.now(UTC)

    code = await _issue(db_session, fake_email, user)

    row = await db_session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    assert row is not None
    assert row.purpose == LOGIN
    assert row.code_hash == hash_code(code)
    assert code not in row.code_hash
    assert row.attempts == 0
    assert row.consumed_at is None
    assert before <= row.created_at <= datetime.now(UTC)
    assert row.expires_at == row.created_at + CODE_TTL


async def test_a_second_code_within_a_minute_is_refused(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    await _issue(db_session, fake_email, user)

    with pytest.raises(CodeRecentlySent):
        await _issue(db_session, fake_email, user)

    assert len(fake_email.sent_to(user.email)) == 1


async def test_a_new_code_is_allowed_after_a_minute(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    await _issue(db_session, fake_email, user)
    await _age_codes(db_session, user, timedelta(seconds=61))

    await _issue(db_session, fake_email, user)

    assert len(fake_email.sent_to(user.email)) == 2


async def test_the_cooldown_is_per_purpose(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    await _issue(db_session, fake_email, user)

    await issue_code(db_session, fake_email, user, CodePurpose.EMAIL_CHANGE, to="new@x.test")

    assert len(fake_email.sent_to("new@x.test")) == 1


async def test_codes_per_day_are_capped(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    now = datetime.now(UTC)
    # Spread over the last day; the oldest is one minute from leaving the 24-hour window.
    for index in range(CODES_PER_DAY):
        created = now - timedelta(days=1) + timedelta(minutes=1, hours=2 * index)
        db_session.add(
            EmailCode(
                user_id=user.id,
                purpose=LOGIN,
                code_hash="x",
                created_at=created,
                expires_at=created + CODE_TTL,
            )
        )
    await db_session.flush()

    with pytest.raises(TooManyCodes):
        await _issue(db_session, fake_email, user)

    # The oldest code leaves the 24-hour window, which frees one slot.
    await _age_codes(db_session, user, timedelta(minutes=2))
    await _issue(db_session, fake_email, user)


@pytest.mark.parametrize(
    ("sender", "error"),
    [
        (FakeEmailSender(failures_left=2), EmailNotSent),
        (NotConfiguredEmailSender(), EmailNotConfigured),
    ],
    ids=["provider-down", "not-configured"],
)
async def test_a_code_that_could_not_be_sent_is_dropped(
    db_session: AsyncSession,
    make_user: MakeUser,
    sender: FakeEmailSender | NotConfiguredEmailSender,
    error: type[Exception],
) -> None:
    user = await make_user()

    with pytest.raises(error):
        await issue_code(db_session, sender, user, LOGIN, to=user.email)

    # Nothing was received, so nothing counts towards the cooldown.
    assert await _code_count(db_session, user) == 0


async def test_the_user_can_retry_right_after_a_failed_send(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    fake_email.failures_left = 2
    with pytest.raises(EmailNotSent):
        await _issue(db_session, fake_email, user)

    await _issue(db_session, fake_email, user)

    assert len(fake_email.sent_to(user.email)) == 1


# --- Consuming ---


async def test_the_right_code_is_consumed_once(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    code = await _issue(db_session, fake_email, user)

    await consume_code(db_session, user, LOGIN, code)

    row = await db_session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    assert row is not None
    assert row.consumed_at is not None
    with pytest.raises(InvalidCode):
        await consume_code(db_session, user, LOGIN, code)


async def test_wrong_codes_are_counted(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    code = await _issue(db_session, fake_email, user)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    with pytest.raises(InvalidCode):
        await consume_code(db_session, user, LOGIN, wrong)

    row = await db_session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    assert row is not None
    assert row.attempts == 1
    assert row.consumed_at is None


async def test_the_code_dies_after_too_many_wrong_attempts(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    code = await _issue(db_session, fake_email, user)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    for _ in range(CODE_MAX_ATTEMPTS):
        with pytest.raises(InvalidCode):
            await consume_code(db_session, user, LOGIN, wrong)

    with pytest.raises(InvalidCode):
        await consume_code(db_session, user, LOGIN, code)


async def test_an_expired_code_is_refused(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    code = await _issue(db_session, fake_email, user)
    await _age_codes(db_session, user, CODE_TTL)

    with pytest.raises(InvalidCode):
        await consume_code(db_session, user, LOGIN, code)


async def test_only_the_newest_code_works(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    old = await _issue(db_session, fake_email, user)
    await _age_codes(db_session, user, timedelta(seconds=61))
    new = await _issue(db_session, fake_email, user)

    if old != new:
        with pytest.raises(InvalidCode):
            await consume_code(db_session, user, LOGIN, old)
    await consume_code(db_session, user, LOGIN, new)


async def test_a_code_only_works_for_its_purpose(
    db_session: AsyncSession, fake_email: FakeEmailSender, make_user: MakeUser
) -> None:
    user = await make_user()
    code = await _issue(db_session, fake_email, user)

    with pytest.raises(InvalidCode):
        await consume_code(db_session, user, CodePurpose.EMAIL_CHANGE, code)


async def test_no_code_at_all_is_refused(db_session: AsyncSession, make_user: MakeUser) -> None:
    with pytest.raises(InvalidCode):
        await consume_code(db_session, await make_user(), LOGIN, "123456")
