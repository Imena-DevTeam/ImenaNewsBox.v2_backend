from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_password
from app.users.models import Promotion, Role, SubFamily, User

MakeUser = Callable[..., Awaitable[User]]


# --- Roles ---


@pytest.mark.parametrize(
    ("role", "minimum", "allowed"),
    [
        (Role.MEMBER, Role.MEMBER, True),
        (Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER, False),
        (Role.MEMBER, Role.ADMIN, False),
        (Role.SOCIAL_MEDIA_MANAGER, Role.MEMBER, True),
        (Role.SOCIAL_MEDIA_MANAGER, Role.SOCIAL_MEDIA_MANAGER, True),
        (Role.SOCIAL_MEDIA_MANAGER, Role.ADMIN, False),
        (Role.ADMIN, Role.MEMBER, True),
        (Role.ADMIN, Role.SOCIAL_MEDIA_MANAGER, True),
        (Role.ADMIN, Role.ADMIN, True),
    ],
)
def test_roles_are_cumulative(role: Role, minimum: Role, allowed: bool) -> None:
    assert role.at_least(minimum) is allowed


def test_string_comparison_of_roles_is_alphabetical_not_by_rank() -> None:
    # Why `at_least` exists: "admin" < "member" as strings.
    assert Role.ADMIN < Role.MEMBER
    assert Role.ADMIN.rank > Role.MEMBER.rank


# --- Users ---


async def test_new_user_defaults(make_user: MakeUser) -> None:
    user = await make_user()

    assert user.id.version == 7
    assert user.role is Role.MEMBER
    assert user.is_active
    assert user.email_verified_at is None
    assert user.password_hash is None
    assert user.promotion_id is None
    assert user.created_at.tzinfo is not None


async def test_factory_hashes_a_given_password(make_user: MakeUser) -> None:
    admin = await make_user(role=Role.ADMIN, password="a long admin password")

    assert admin.role is Role.ADMIN
    assert await verify_password("a long admin password", admin.password_hash)


async def test_role_is_stored_as_its_value(db_session: AsyncSession, make_user: MakeUser) -> None:
    user = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    stored = await db_session.scalar(text("SELECT role FROM users WHERE id = :id"), {"id": user.id})

    assert stored == "social_media_manager"


@pytest.mark.parametrize("duplicate", ["Mugisha", "mugisha", "MUGISHA"])
async def test_family_names_are_unique_ignoring_case(
    db_session: AsyncSession, make_user: MakeUser, duplicate: str
) -> None:
    await make_user(family_name="Mugisha")

    with pytest.raises(IntegrityError, match="uq_users_family_name_lower"):
        async with db_session.begin_nested():
            await make_user(family_name=duplicate)


async def test_deactivated_users_keep_their_family_name(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    await make_user(family_name="Keza", deactivated_at=datetime.now(UTC))

    with pytest.raises(IntegrityError, match="uq_users_family_name_lower"):
        async with db_session.begin_nested():
            await make_user(family_name="keza")


async def test_family_name_lookup_is_case_insensitive(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    user = await make_user(family_name="Ishimwe")

    found = await db_session.scalar(
        # The form login uses: it matches the unique index on lower(family_name).
        select(User).where(func.lower(User.family_name) == "ISHIMWE".lower())
    )

    assert found is user


async def test_relatives_may_share_an_email(make_user: MakeUser) -> None:
    first = await make_user(email="family@example.test")
    second = await make_user(email="family@example.test")

    assert first.id != second.id


async def test_a_sub_family_is_required(db_session: AsyncSession, make_user: MakeUser) -> None:
    with pytest.raises(IntegrityError, match="sub_family_id"):
        async with db_session.begin_nested():
            await make_user(sub_family_id=None)


async def test_sub_family_must_exist(db_session: AsyncSession, make_user: MakeUser) -> None:
    with pytest.raises(IntegrityError, match="fk_users_sub_family_id_sub_families"):
        async with db_session.begin_nested():
            await make_user(sub_family_id=999_999)


async def test_promotion_is_optional_but_must_exist(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    promotion = Promotion(name="Test promotion")
    db_session.add(promotion)
    await db_session.flush()

    user = await make_user(promotion_id=promotion.id)
    assert user.promotion_id == promotion.id

    with pytest.raises(IntegrityError, match="fk_users_promotion_id_promotions"):
        async with db_session.begin_nested():
            await make_user(promotion_id=999_999)


async def test_the_database_rejects_unknown_roles(
    db_session: AsyncSession, sub_family: SubFamily
) -> None:
    with pytest.raises(IntegrityError, match="ck_users_role"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO users (id, family_name, display_name, email, role, sub_family_id)"
                    " VALUES (gen_random_uuid(), 'Guest', 'Guest', 'g@example.test', 'guest', :sf)"
                ),
                {"sf": sub_family.id},
            )


async def test_lookup_names_are_unique(db_session: AsyncSession) -> None:
    db_session.add(SubFamily(name="Same"))
    await db_session.flush()

    with pytest.raises(IntegrityError, match="uq_sub_families_name"):
        async with db_session.begin_nested():
            db_session.add(SubFamily(name="Same"))
            await db_session.flush()


async def test_deactivation_makes_a_user_inactive(make_user: MakeUser) -> None:
    user = await make_user(deactivated_at=datetime.now(UTC))

    assert not user.is_active


async def test_repr_holds_no_personal_data(make_user: MakeUser) -> None:
    user = await make_user(family_name="Uwase", email="uwase@example.test")

    assert repr(user) == f"<User {user.id}>"
