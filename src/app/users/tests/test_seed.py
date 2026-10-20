from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.users.models import Role, SubFamily, User
from app.users.service import FirstAdminError, SeedResult, seed_first_admin

MakeUser = Callable[..., Awaitable[User]]
PASSWORD = "a long admin passphrase"


def _args(row: SubFamily, /, **overrides: str) -> dict[str, Any]:
    return {
        "family_name": "Founder",
        "email": "founder@example.test",
        "password": PASSWORD,
        "sub_family": row.name,
    } | overrides


async def _admins(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).where(User.role == Role.ADMIN)) or 0


async def test_the_first_admin_is_created(db_session: AsyncSession, sub_family: SubFamily) -> None:
    result = await seed_first_admin(db_session, **_args(sub_family))

    assert result is SeedResult.CREATED
    admin = await db_session.scalar(select(User).where(User.family_name == "Founder"))
    assert admin is not None
    assert admin.role is Role.ADMIN
    assert admin.email == "founder@example.test"
    assert admin.display_name == "Founder"
    assert admin.sub_family_id == sub_family.id
    assert admin.email_verified_at is None
    assert admin.is_active
    assert await verify_password(PASSWORD, admin.password_hash)


async def test_running_it_again_changes_nothing(
    db_session: AsyncSession, sub_family: SubFamily
) -> None:
    await seed_first_admin(db_session, **_args(sub_family))
    admin = await db_session.scalar(select(User).where(User.family_name == "Founder"))
    assert admin is not None
    password_hash = admin.password_hash

    result = await seed_first_admin(
        db_session,
        **_args(
            sub_family, family_name=" founder ", email="FOUNDER@example.test", password="x" * 20
        ),
    )

    assert result is SeedResult.ALREADY_EXISTS
    assert admin.password_hash == password_hash
    assert await _admins(db_session) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"role": Role.MEMBER, "email": "founder@example.test"},
        {"role": Role.ADMIN, "email": "someone-else@example.test"},
        {"role": Role.ADMIN, "email": "founder@example.test", "deactivated_at": datetime.now(UTC)},
    ],
    ids=["a-member", "another-email", "deactivated"],
)
async def test_an_existing_family_name_is_never_taken_over(
    db_session: AsyncSession, sub_family: SubFamily, make_user: MakeUser, fields: dict[str, Any]
) -> None:
    existing = await make_user(family_name="Founder", **fields)

    with pytest.raises(FirstAdminError, match="already exists"):
        await seed_first_admin(db_session, **_args(sub_family))

    assert existing.role is fields["role"]


async def test_an_email_used_by_someone_else_is_refused(
    db_session: AsyncSession, sub_family: SubFamily, make_user: MakeUser
) -> None:
    await make_user(email="Founder@Example.test")

    with pytest.raises(FirstAdminError, match="belongs to another user") as exc_info:
        await seed_first_admin(db_session, **_args(sub_family))

    # Emails are never put in messages, since they end up in logs.
    assert "@" not in str(exc_info.value)


async def test_the_sub_family_must_already_exist(
    db_session: AsyncSession, sub_family: SubFamily
) -> None:
    with pytest.raises(FirstAdminError, match="no sub-family named 'Imaginary'"):
        await seed_first_admin(db_session, **_args(sub_family, sub_family="Imaginary"))

    assert await db_session.scalar(select(func.count()).select_from(SubFamily)) == 1
    assert await _admins(db_session) == 0


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"password": "short"}, "at least 12 characters"),
        ({"email": "not-an-email"}, "must be an email address"),
        ({"family_name": "  "}, "Set FIRST_ADMIN_FAMILY_NAME"),
        ({"sub_family": ""}, "Set FIRST_ADMIN_FAMILY_NAME and FIRST_ADMIN_SUB_FAMILY"),
    ],
    ids=["short-password", "bad-email", "no-family-name", "no-sub-family"],
)
async def test_missing_or_weak_settings_are_refused(
    db_session: AsyncSession, sub_family: SubFamily, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(FirstAdminError, match=message):
        await seed_first_admin(db_session, **_args(sub_family, **overrides))

    assert await _admins(db_session) == 0
