"""Pydantic request/response models (DTOs)."""

from typing import Annotated, Final

from pydantic import BaseModel, Field, SecretStr, StringConstraints

from app.users.models import FAMILY_NAME_MAX_LENGTH

# Generous for a passphrase, but bounded so nobody can make us hash megabytes.
PASSWORD_MAX_LENGTH: Final = 256

FamilyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FAMILY_NAME_MAX_LENGTH)
]


class LoginRequest(BaseModel):
    family_name: FamilyName
    # Admins only: members leave it out. SecretStr keeps it out of reprs and logs.
    password: Annotated[SecretStr, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)] | None = (
        None
    )


class CodeSent(BaseModel):
    """The same answer whether or not the account exists."""

    message: str
    expires_in: int = Field(description="Seconds until the code expires.")
    resend_after: int = Field(description="Seconds before another code can be requested.")
