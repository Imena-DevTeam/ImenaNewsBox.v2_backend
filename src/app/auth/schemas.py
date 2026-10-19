"""Pydantic request/response models (DTOs)."""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, SecretStr, StringConstraints

from app.core.google import ID_TOKEN_MAX_LENGTH
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


class VerifyRequest(BaseModel):
    family_name: FamilyName
    # [0-9], not \d, which also matches other scripts' digits.
    code: Annotated[
        str, StringConstraints(strip_whitespace=True, pattern=rf"^[0-9]{{{CODE_LENGTH}}}$")
    ]


class GoogleLoginRequest(BaseModel):
    # The ID token ("credential") from Google's sign-in button.
    id_token: Annotated[SecretStr, Field(min_length=1, max_length=ID_TOKEN_MAX_LENGTH)]
    password: AdminPassword | None = None


class AccessToken(BaseModel):
    """Sent with every login and refresh. The refresh token is only ever in the cookie."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until the access token expires.")
