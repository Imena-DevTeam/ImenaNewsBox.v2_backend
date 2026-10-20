"""Pydantic request/response models (DTOs).

Users have two separate read schemas, never one with fields hidden on the fly:
- `UserPublicRead`: what every member sees about the others.
- `UserRead`: the whole account, for the user themselves and for admins.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.auth.schemas import FamilyName
from app.core.pagination import PageParams
from app.users.models import DISPLAY_NAME_MAX_LENGTH, EMAIL_MAX_LENGTH, Role

EARLIEST_BIRTHDAY = date(1900, 1, 1)


def _check_email_length(email: str) -> str:
    if len(email) > EMAIL_MAX_LENGTH:
        raise ValueError(f"must be at most {EMAIL_MAX_LENGTH} characters")
    return email


def _check_birthday(birthday: date) -> date:
    if birthday > datetime.now(UTC).date():
        raise ValueError("can't be in the future")
    if birthday < EARLIEST_BIRTHDAY:
        raise ValueError(f"can't be before {EARLIEST_BIRTHDAY.year}")
    return birthday


DisplayName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=DISPLAY_NAME_MAX_LENGTH)
]
# email-validator (with FastAPI) checks the format and lowercases the domain.
Email = Annotated[EmailStr, AfterValidator(_check_email_length)]
Birthday = Annotated[date, AfterValidator(_check_birthday)]


class SubFamilyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PromotionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class MonthDay(BaseModel):
    """A birthday without its year, which only the user and admins may see."""

    month: int
    day: int


class UserPublicRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family_name: str
    display_name: str
    photo_url: str | None
    sub_family: SubFamilyRead
    promotion: PromotionRead | None
    birthday: MonthDay | None

    @field_validator("birthday", mode="before")
    @classmethod
    def _drop_the_year(cls, value: Any) -> Any:
        # Convert here, so the full date can't reach the response by any route.
        if isinstance(value, date):
            return MonthDay(month=value.month, day=value.day)
        return value


class UserRead(BaseModel):
    """The whole account: for the user themselves and for admins. Never the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    family_name: str
    display_name: str
    photo_url: str | None
    sub_family: SubFamilyRead
    promotion: PromotionRead | None
    birthday: date | None
    email: str
    email_verified_at: datetime | None
    role: Role
    has_password: bool = Field(description="Admins can't log in until a password is set.")
    deactivated_at: datetime | None
    created_at: datetime


class AccountListParams(PageParams):
    include_deactivated: bool = Field(default=False, description="Also list deactivated users.")


class UserCreate(BaseModel):
    """Admins only: there's no self-signup. Admins get a password separately."""

    # A misspelt or unexpected field (say, "password") is an error, not silently dropped.
    model_config = ConfigDict(extra="forbid")

    family_name: FamilyName
    display_name: DisplayName
    email: Email
    sub_family_id: int
    promotion_id: int | None = None
    birthday: Birthday | None = None
    role: Role = Role.MEMBER


class UserUpdate(BaseModel):
    """Admins: send only the fields that change. `promotion_id` and `birthday` can be set to
    null to clear them; the other fields can't be null."""

    model_config = ConfigDict(extra="forbid")

    family_name: FamilyName | None = None
    display_name: DisplayName | None = None
    email: Email | None = None
    sub_family_id: int | None = None
    promotion_id: int | None = None
    birthday: Birthday | None = None
    role: Role | None = None

    @model_validator(mode="after")
    def _required_fields_stay_set(self) -> Self:
        for name in ("family_name", "display_name", "email", "sub_family_id", "role"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} can't be null")
        return self
