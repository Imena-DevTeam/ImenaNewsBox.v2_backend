"""Copying v1's MongoDB profiles into `users`, for `scripts/import_v1.py` only.

The script reads the profiles with pymongo and passes them in as plain dicts, so the app never
imports pymongo and these rules are tested like the rest of it.

Every imported member starts as a plain member: unverified, with no promotion, password,
Sanity author or sessions. Admins promote people in the app afterwards. Running it again is
safe: a user already in v2 is never overwritten, only their empty fields are filled.
"""

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Final
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.schemas import FamilyName
from app.users.models import SubFamily, User
from app.users.schemas import Birthday, DisplayName, Email

# v1 stored Cloudinary `secure_url`s:
# https://res.cloudinary.com/<cloud>/image/upload/[<transformations>/]v<version>/<public id>.<ext>
_CLOUDINARY_HOST: Final = "res.cloudinary.com"
_CLOUDINARY_PATH: Final = re.compile(
    r"^/(?P<cloud>[^/]+)/image/upload/(?:[^/]+/)*?v\d+/(?P<public_id>.+)\.\w+$"
)
# The length of `users.photo_url`.
_PHOTO_URL_MAX_LENGTH: Final = 1024
_BIRTHDAY: Final = TypeAdapter(Birthday)


class V1ImportError(Exception):
    """The import couldn't finish. The message says why (never an email)."""


class V1Profile(BaseModel):
    """One v1 `Profile` document, as the script hands it over."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True, frozen=True)

    source_id: str = Field(alias="_id")
    # When v1 created the profile (from its MongoDB ObjectId), kept as `created_at`.
    created_at: datetime | None = None
    display_name: DisplayName = Field(alias="name")
    family_name: FamilyName = Field(alias="familyName")
    email: Email
    birthday: datetime | None = None
    sub_family: str = Field(alias="subFam")
    photo: str | None = Field(default=None, alias="profilePic")


@dataclass(frozen=True)
class Note:
    who: str
    message: str


@dataclass
class ImportReport:
    """What the import did, or would do on a dry run. It names people by family name only,
    never by email, because it ends up in logs."""

    applied: bool
    read: int = 0
    created: list[str] = field(default_factory=list)
    # Already in v2: only their empty fields were filled.
    filled: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[Note] = field(default_factory=list)
    # Imported, but worth checking.
    notes: list[Note] = field(default_factory=list)
    # Photos on a Cloudinary account other than ours: kept as links, never deleted from there.
    photos_on_another_account: int = 0


@dataclass(frozen=True)
class _Photo:
    url: str
    public_id: str | None
    on_another_account: bool


@dataclass(frozen=True)
class _Row:
    profile: V1Profile
    sub_family_id: int
    birthday: date | None
    photo: _Photo | None
    notes: list[str]


async def import_v1_profiles(
    db: AsyncSession,
    documents: Iterable[Mapping[str, Any]],
    *,
    cloud_name: str,
    apply: bool,
) -> ImportReport:
    """Create a member for each v1 profile that's new, and fill the empty fields of those
    already in v2. Commits only when `apply` is true; otherwise it just reports.

    `cloud_name` is our Cloudinary account: only photos stored there keep their public ID, so
    replacing one later deletes the old file. Raises `V1ImportError`.
    """
    report = ImportReport(applied=apply)
    rows = await _check(db, documents, report, cloud_name)

    # Family scale: a few hundred users at most, so load them all once.
    users = list(await db.scalars(select(User)))
    by_family_name = {user.family_name.lower(): user for user in users}
    by_email = {user.email.lower(): user for user in users}

    new_users: list[User] = []
    fills: list[tuple[User, dict[str, Any]]] = []
    for row in rows:
        profile = row.profile
        existing = by_family_name.get(profile.family_name.lower())
        if existing is not None:
            if existing.email.lower() != profile.email.lower():
                report.skipped.append(
                    Note(
                        profile.family_name,
                        "already in v2 with a different email, so it was left alone",
                    )
                )
                continue
            changes = _empty_fields(existing, row)
            if not changes:
                report.unchanged.append(profile.family_name)
                continue
            fills.append((existing, changes))
            report.filled.append(profile.family_name)
            if "photo_url" in changes and row.photo is not None:
                report.photos_on_another_account += row.photo.on_another_account
        else:
            owner = by_email.get(profile.email.lower())
            if owner is not None:
                report.skipped.append(
                    Note(
                        profile.family_name,
                        f"its email already belongs to {owner.family_name} in v2",
                    )
                )
                continue
            new_users.append(_new_user(row))
            report.created.append(profile.family_name)
            if row.photo is not None:
                report.photos_on_another_account += row.photo.on_another_account
        report.notes.extend(Note(profile.family_name, note) for note in row.notes)

    if apply and (new_users or fills):
        for user, changes in fills:
            for name, value in changes.items():
                setattr(user, name, value)
        db.add_all(new_users)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise V1ImportError(
                "A family name or email was taken while the import ran. Run it again."
            ) from exc
    return report


async def _check(
    db: AsyncSession,
    documents: Iterable[Mapping[str, Any]],
    report: ImportReport,
    cloud_name: str,
) -> list[_Row]:
    """Validate the profiles, and skip any that are invalid, duplicated or in an unknown
    sub-family."""
    profiles: list[V1Profile] = []
    for document in documents:
        report.read += 1
        try:
            profiles.append(V1Profile.model_validate(document))
        except ValidationError as exc:
            report.skipped.append(Note(_who(document), _describe(exc)))

    # v2 needs a unique family name and email (both ignoring case), so v1 duplicates are all
    # left out: the script can't tell which one is right.
    family_names = Counter(profile.family_name.lower() for profile in profiles)
    emails = Counter(profile.email.lower() for profile in profiles)
    sub_families = {row.name.lower(): row for row in await db.scalars(select(SubFamily))}

    rows: list[_Row] = []
    for profile in profiles:
        if (count := family_names[profile.family_name.lower()]) > 1:
            report.skipped.append(
                Note(profile.family_name, f"the family name appears {count} times in v1")
            )
            continue
        if emails[profile.email.lower()] > 1:
            others = ", ".join(
                other.family_name
                for other in profiles
                if other is not profile and other.email.lower() == profile.email.lower()
            )
            report.skipped.append(
                Note(profile.family_name, f"it shares its email with {others} in v1")
            )
            continue
        sub_family = sub_families.get(profile.sub_family.lower())
        if sub_family is None:
            names = ", ".join(sorted(row.name for row in sub_families.values()))
            report.skipped.append(
                Note(
                    profile.family_name,
                    f"the sub-family {profile.sub_family!r} isn't one of {names}",
                )
            )
            continue
        notes: list[str] = []
        rows.append(
            _Row(
                profile=profile,
                sub_family_id=sub_family.id,
                birthday=_birthday(profile.birthday, notes),
                photo=_photo(profile.photo, cloud_name, notes),
                notes=notes,
            )
        )
    return rows


def _who(document: Mapping[str, Any]) -> str:
    """How the report names a profile that failed validation."""
    family_name = document.get("familyName")
    if isinstance(family_name, str) and family_name.strip():
        return family_name.strip()
    return f"v1 profile {document.get('_id')}"


def _describe(exc: ValidationError) -> str:
    """What's wrong, by field. Never the submitted values."""
    problems = []
    for error in exc.errors():
        name = ".".join(str(part) for part in error["loc"]) or "the profile"
        if error["type"] == "missing":
            problems.append(f"{name} is missing")
        elif name == "email":
            problems.append("email isn't a valid address")
        else:
            problems.append(f"{name}: {error['msg']}")
    return "; ".join(problems)


def _birthday(value: datetime | None, notes: list[str]) -> date | None:
    if value is None:
        notes.append("v1 had no birthday")
        return None
    # v1 stored dates as midnight UTC. pymongo returns them without a zone unless told to.
    stored = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
    if stored.time() != time(0):
        notes.append("the v1 birthday had a time of day, so the date may be a day off")
    try:
        return _BIRTHDAY.validate_python(stored.date())
    except ValidationError:
        notes.append("the v1 birthday was in the future or before 1900, so it was left empty")
        return None


def _photo(url: str | None, cloud_name: str, notes: list[str]) -> _Photo | None:
    if not url:
        return None
    parts = urlsplit(url)
    if not parts.scheme:
        # v1 saved some uploads on its own disk (`images/...`), which has since been wiped.
        notes.append("the photo was a file on v1's server, which no longer exists")
        return None
    if parts.scheme != "https" or parts.hostname != _CLOUDINARY_HOST:
        notes.append("the photo wasn't a Cloudinary link, so it was left out")
        return None
    if len(url) > _PHOTO_URL_MAX_LENGTH:
        notes.append("the photo link was too long, so it was left out")
        return None
    match = _CLOUDINARY_PATH.match(parts.path)
    if match is None:
        return _Photo(url=url, public_id=None, on_another_account=False)
    if cloud_name and match["cloud"] == cloud_name:
        return _Photo(url=url, public_id=match["public_id"], on_another_account=False)
    return _Photo(url=url, public_id=None, on_another_account=True)


def _new_user(row: _Row) -> User:
    profile = row.profile
    user = User(
        family_name=profile.family_name,
        display_name=profile.display_name,
        email=profile.email,
        birthday=row.birthday,
        sub_family_id=row.sub_family_id,
    )
    if row.photo is not None:
        user.photo_url, user.photo_public_id = row.photo.url, row.photo.public_id
    if profile.created_at is not None:
        user.created_at = profile.created_at
    return user


def _empty_fields(user: User, row: _Row) -> dict[str, Any]:
    """The v1 values for the fields this existing user hasn't set yet."""
    changes: dict[str, Any] = {}
    # The seed names the first admin after their family name until they choose a name.
    if user.display_name == user.family_name != row.profile.display_name:
        changes["display_name"] = row.profile.display_name
    if user.birthday is None and row.birthday is not None:
        changes["birthday"] = row.birthday
    if user.photo_url is None and row.photo is not None:
        changes["photo_url"] = row.photo.url
        changes["photo_public_id"] = row.photo.public_id
    return changes
