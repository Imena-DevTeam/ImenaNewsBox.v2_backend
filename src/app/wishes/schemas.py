"""Pydantic request/response models (DTOs)."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from app.users.schemas import UserPublicRead
from app.wishes.models import WISH_MESSAGE_MAX_LENGTH

WishMessage = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=WISH_MESSAGE_MAX_LENGTH)
]


class WishCreate(BaseModel):
    """The year isn't sent: the server picks the recipient's next birthday."""

    model_config = ConfigDict(extra="forbid")

    recipient_id: uuid.UUID
    message: WishMessage


class WishUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: WishMessage


class WishRead(BaseModel):
    """Sender and recipient as every member sees them: no birth year."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sender: UserPublicRead
    recipient: UserPublicRead
    year: int
    message: str
    created_at: datetime
    updated_at: datetime
