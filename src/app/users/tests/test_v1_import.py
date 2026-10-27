from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import Role, SubFamily, User
from app.users.v1_import import ImportReport, Note, import_v1_profiles

MakeUser = Callable[..., Awaitable[User]]

OUR_CLOUD = "our-cloud"
PHOTO = "https://res.cloudinary.com/v1-cloud/image/upload/v1760449281/hnb-profiles/abc123.png"
OUR_PHOTO = PHOTO.replace("v1-cloud", OUR_CLOUD)
V1_CREATED = datetime(2025, 9, 26, 8, 30, tzinfo=UTC)


def _profile(family_name: str = "AMATA", **overrides: Any) -> dict[str, Any]:
    """A v1 profile as the script hands it over (see the example in docs/domain.md)."""
    return {
        "_id": f"id-{family_name}",
        "created_at": V1_CREATED,
        "name": f"{family_name.title()} Member",
        "familyName": family_name,
        "email": f"{family_name.lower()}@example.com",
        "birthday": datetime(2008, 10, 24, tzinfo=UTC),
        "subFam": "Light",
        "profilePic": PHOTO,
        "__v": 0,
    } | overrides


async def _import(db: AsyncSession, *profiles: dict[str, Any], apply: bool = True) -> ImportReport:
    return await import_v1_profiles(db, profiles, cloud_name=OUR_CLOUD, apply=apply)


async def _user(db: AsyncSession, family_name: str) -> User | None:
    return await db.scalar(select(User).where(User.family_name == family_name))


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(User)) or 0


async def _sub_family(db: AsyncSession, name: str) -> SubFamily:
    row = await db.scalar(select(SubFamily).where(SubFamily.name == name))
    assert row is not None, "the sub-families come from a migration"
    return row


# --- New members ---


async def test_a_profile_becomes_a_plain_member(db_session: AsyncSession) -> None:
    report = await _import(db_session, _profile())

    assert report.created == ["AMATA"]
    user = await _user(db_session, "AMATA")
    assert user is not None
    # Family names keep v1's capitals.
    assert user.display_name == "Amata Member"
    assert user.email == "amata@example.com"
    assert user.birthday == date(2008, 10, 24)
    assert user.sub_family_id == (await _sub_family(db_session, "LIGHT")).id
    assert user.photo_url == PHOTO
    assert user.created_at == V1_CREATED
    # Everything v1 didn't have starts empty, and admins promote people in the app.
    assert user.role is Role.MEMBER
    assert user.email_verified_at is None
    assert user.promotion_id is None
    assert user.password_hash is None
    assert user.sanity_author_id is None
    assert user.is_active


async def test_a_dry_run_writes_nothing(db_session: AsyncSession) -> None:
    before = await _count(db_session)

    report = await _import(db_session, _profile(), apply=False)

    assert not report.applied
    assert report.created == ["AMATA"]
    assert await _count(db_session) == before


async def test_running_it_again_changes_nothing(db_session: AsyncSession) -> None:
    profiles = [_profile("AMATA"), _profile("KARABO")]
    await _import(db_session, *profiles)
    count = await _count(db_session)

    report = await _import(db_session, *profiles)

    assert report.created == []
    assert report.unchanged == ["AMATA", "KARABO"]
    assert await _count(db_session) == count


@pytest.mark.parametrize("written", ["light", " LIGHT ", "Light"])
async def test_sub_families_match_ignoring_case_and_spaces(
    db_session: AsyncSession, written: str
) -> None:
    await _import(db_session, _profile(subFam=written))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert user.sub_family_id == (await _sub_family(db_session, "LIGHT")).id


async def test_an_unknown_sub_family_is_skipped(db_session: AsyncSession) -> None:
    report = await _import(db_session, _profile(subFam="Lite"))

    assert report.created == []
    [skipped] = report.skipped
    assert skipped.who == "AMATA"
    assert "'Lite' isn't one of" in skipped.message
    assert "HOPE, LIGHT" in skipped.message
    assert await _user(db_session, "AMATA") is None


# --- Photos ---


async def test_a_photo_on_our_cloudinary_keeps_its_public_id(db_session: AsyncSession) -> None:
    report = await _import(db_session, _profile(profilePic=OUR_PHOTO))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert (user.photo_url, user.photo_public_id) == (OUR_PHOTO, "hnb-profiles/abc123")
    assert report.photos_on_another_account == 0


async def test_a_photo_on_another_account_is_only_a_link(db_session: AsyncSession) -> None:
    report = await _import(db_session, _profile())

    user = await _user(db_session, "AMATA")
    assert user is not None
    # Without a public ID, replacing the photo never tries to delete it from our account.
    assert (user.photo_url, user.photo_public_id) == (PHOTO, None)
    assert report.photos_on_another_account == 1


@pytest.mark.parametrize(
    ("photo", "note"),
    [
        ("images/1758878365676-profilePic.png", "a file on v1's server"),
        ("https://example.test/me.png", "wasn't a Cloudinary link"),
        (PHOTO.replace("https", "http"), "wasn't a Cloudinary link"),
        (PHOTO.replace(".png", "-" + "x" * 1024 + ".png"), "too long"),
    ],
    ids=["local-file", "other-site", "plain-http", "too-long"],
)
async def test_unusable_photos_are_left_out(
    db_session: AsyncSession, photo: str, note: str
) -> None:
    report = await _import(db_session, _profile(profilePic=photo))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert (user.photo_url, user.photo_public_id) == (None, None)
    [found] = report.notes
    assert note in found.message


# --- Birthdays ---


async def test_a_birthday_with_a_time_of_day_is_flagged(db_session: AsyncSession) -> None:
    # Midnight in Kigali, which is 22:00 the day before in UTC.
    report = await _import(db_session, _profile(birthday=datetime(2008, 10, 23, 22, tzinfo=UTC)))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert user.birthday == date(2008, 10, 23)
    assert report.notes == [
        Note("AMATA", "the v1 birthday had a time of day, so the date may be a day off")
    ]


async def test_a_birthday_without_a_time_zone_is_utc(db_session: AsyncSession) -> None:
    report = await _import(db_session, _profile(birthday=datetime(2008, 10, 24)))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert user.birthday == date(2008, 10, 24)
    assert report.notes == []


@pytest.mark.parametrize(
    ("birthday", "note"),
    [
        (None, "v1 had no birthday"),
        (datetime(2999, 1, 1, tzinfo=UTC), "in the future or before 1900"),
        (datetime(1850, 1, 1, tzinfo=UTC), "in the future or before 1900"),
    ],
    ids=["missing", "future", "too-old"],
)
async def test_an_unusable_birthday_is_left_empty(
    db_session: AsyncSession, birthday: datetime | None, note: str
) -> None:
    report = await _import(db_session, _profile(birthday=birthday))

    user = await _user(db_session, "AMATA")
    assert user is not None
    assert user.birthday is None
    [found] = report.notes
    assert note in found.message


# --- Invalid and duplicated profiles ---


@pytest.mark.parametrize(
    ("overrides", "who", "reason"),
    [
        ({"familyName": "  "}, "v1 profile id-AMATA", "familyName: String should have at least"),
        ({"email": "not an email"}, "AMATA", "email isn't a valid address"),
        ({"name": None}, "AMATA", "name: Input should be a valid string"),
        ({"subFam": 7}, "AMATA", "subFam: Input should be a valid string"),
    ],
    ids=["blank-family-name", "bad-email", "no-name", "odd-sub-family"],
)
async def test_invalid_profiles_are_skipped(
    db_session: AsyncSession, overrides: dict[str, Any], who: str, reason: str
) -> None:
    profile = _profile() | overrides

    report = await _import(db_session, profile, _profile("KARABO"))

    assert report.read == 2
    assert report.created == ["KARABO"]
    [skipped] = report.skipped
    assert skipped.who == who
    assert reason in skipped.message


async def test_a_missing_field_is_named(db_session: AsyncSession) -> None:
    profile = _profile()
    del profile["email"]

    report = await _import(db_session, profile)

    assert report.skipped == [Note("AMATA", "email is missing")]


async def test_family_names_used_twice_in_v1_are_all_skipped(db_session: AsyncSession) -> None:
    report = await _import(
        db_session,
        _profile("AMATA"),
        _profile("Amata", email="other@example.com"),
        _profile("KARABO"),
    )

    assert report.created == ["KARABO"]
    assert [note.who for note in report.skipped] == ["AMATA", "Amata"]
    assert all("appears 2 times in v1" in note.message for note in report.skipped)


async def test_emails_shared_in_v1_are_all_skipped(db_session: AsyncSession) -> None:
    report = await _import(
        db_session,
        _profile("AMATA", email="family@example.com"),
        _profile("KARABO", email="Family@Example.com"),
        _profile("NEZA"),
    )

    assert report.created == ["NEZA"]
    assert report.skipped == [
        Note("AMATA", "it shares its email with KARABO in v1"),
        Note("KARABO", "it shares its email with AMATA in v1"),
    ]


# --- People already in v2 ---


async def test_the_seeded_admin_only_gets_their_empty_fields_filled(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    # As `seed_first_admin` leaves them: named after their family name, no birthday or photo.
    admin = await make_user(
        role=Role.ADMIN,
        family_name="AMATA",
        display_name="AMATA",
        email="Amata@example.com",
        password_hash="a hash",
    )
    sub_family_id = admin.sub_family_id

    report = await _import(db_session, _profile())

    assert report.filled == ["AMATA"]
    assert admin.display_name == "Amata Member"
    assert admin.birthday == date(2008, 10, 24)
    assert admin.photo_url == PHOTO
    # Everything else stays as it was.
    assert admin.role is Role.ADMIN
    assert admin.password_hash == "a hash"
    assert admin.email == "Amata@example.com"
    assert admin.sub_family_id == sub_family_id
    assert admin.created_at != V1_CREATED


async def test_what_a_member_set_in_v2_is_never_overwritten(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    member = await make_user(
        family_name="AMATA",
        display_name="Flo",
        email="amata@example.com",
        birthday=date(2008, 10, 25),
        photo_url="https://res.cloudinary.com/our-cloud/image/upload/v1/new.png",
        photo_public_id="new",
    )

    report = await _import(db_session, _profile())

    assert report.unchanged == ["AMATA"]
    assert (member.display_name, member.birthday, member.photo_public_id) == (
        "Flo",
        date(2008, 10, 25),
        "new",
    )


async def test_a_family_name_with_another_email_is_left_alone(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    member = await make_user(family_name="amata", display_name="amata")

    report = await _import(db_session, _profile())

    assert report.skipped == [
        Note("AMATA", "already in v2 with a different email, so it was left alone")
    ]
    assert member.birthday is None


async def test_an_email_used_by_another_v2_user_is_skipped(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    await make_user(family_name="KARABO", email="AMATA@example.com")

    report = await _import(db_session, _profile())

    assert report.skipped == [Note("AMATA", "its email already belongs to KARABO in v2")]
    assert await _user(db_session, "AMATA") is None


async def test_the_report_never_contains_an_email(
    db_session: AsyncSession, make_user: MakeUser
) -> None:
    await make_user(family_name="TAKEN", email="neza@example.com")
    profile_without_name = _profile("GONE")
    del profile_without_name["familyName"]

    report = await _import(
        db_session,
        _profile("AMATA", email="shared@example.com"),
        _profile("KARABO", email="shared@example.com"),
        _profile("NEZA"),
        _profile("BAD", email="bad@@example.com"),
        profile_without_name,
        _profile("TAKEN", email="taken@example.com"),
    )

    assert len(report.skipped) == 6
    assert "@" not in str(asdict(report))
