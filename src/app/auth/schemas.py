"""Pydantic request/response models (DTOs)."""

import uuid
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, SecretStr, StringConstraints

from app.core.google import ID_TOKEN_MAX_LENGTH
from app.core.pagination import PageParams
from app.core.security import CODE_LENGTH
from app.users.models import FAMILY_NAME_MAX_LENGTH

# Generous for a passphrase, but bounded so nobody can make us hash megabytes.
PASSWORD_MAX_LENGTH: Final = 256

FamilyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FAMILY_NAME_MAX_LENGTH)
]


# Admins only: members leave it out. SecretStr keeps it out of reprs and logs.
AdminPassword = Annotated[SecretStr, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class LoginRequest(BaseModel):
    family_name: FamilyName
    password: AdminPassword | None = None


class CodeSent(BaseModel):
    """The same answer whether or not the account exists."""

    message: str
    expires_in: int = Field(description="Seconds until the code expires.")
    resend_after: int = Field(description="Seconds before another code can be requested.")


# An emailed code as typed: spaces around it are ignored. [0-9], not \d, which also
# matches other scripts' digits.
Code = Annotated[
    str, StringConstraints(strip_whitespace=True, pattern=rf"^[0-9]{{{CODE_LENGTH}}}$")
]


class VerifyRequest(BaseModel):
    family_name: FamilyName
    code: Code


class GoogleLoginRequest(BaseModel):
    # The ID token ("credential") from Google's sign-in button.
    id_token: Annotated[SecretStr, Field(min_length=1, max_length=ID_TOKEN_MAX_LENGTH)]
    password: AdminPassword | None = None


class AccessToken(BaseModel):
    """Sent with every login and refresh. The refresh token is only ever in the cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until the access token expires.")


class SessionRead(BaseModel):
    """One logged-in device, as its owner (or an admin) sees it. Never includes token hashes."""

    id: uuid.UUID
    device_label: str
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    current: bool = Field(description="True for the device making this request.")


class SessionListParams(PageParams):
    user_id: uuid.UUID | None = Field(
        default=None, description="Admins only: list this user's sessions instead of yours."
    )
