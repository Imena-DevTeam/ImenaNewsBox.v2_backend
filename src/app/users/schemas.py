"""Pydantic request/response models (DTOs).

Users have two separate read schemas, never one with fields hidden on the fly:
- `UserPublicRead`: what every member sees about the others.
- `UserRead`: the whole account, for the user themselves and for admins.
"""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


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
