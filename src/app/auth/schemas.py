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
# For a password being set (logging in accepts any length, so old passwords keep working).
PASSWORD_MIN_LENGTH: Final = 8
# Invitation tokens are 43 characters; this only bounds what we hash.
INVITATION_TOKEN_MAX_LENGTH: Final = 128

FamilyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=FAMILY_NAME_MAX_LENGTH)
]


# Admins only: members leave it out. SecretStr keeps it out of reprs and logs.
AdminPassword = Annotated[SecretStr, Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
# A password an admin chooses.
NewPassword = Annotated[
    SecretStr, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)
]
InvitationToken = Annotated[SecretStr, Field(min_length=1, max_length=INVITATION_TOKEN_MAX_LENGTH)]


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


class InvitationCheck(BaseModel):
    """The token from the invitation link (after `#token=`)."""

    token: InvitationToken


class InvitationRead(BaseModel):
    """What the invitation page shows before it's accepted. Only the invited person has the
    link, so it's their own details."""

    family_name: str = Field(description="What they log in with.")
    display_name: str
    needs_password: bool = Field(
        description="Admins must choose a password (at least 8 characters) to accept."
    )


class InvitationAccept(BaseModel):
    token: InvitationToken
    password: NewPassword | None = Field(
        default=None, description="Admins only, and required for them. Ignored for members."
    )


class SessionListParams(PageParams):
    user_id: uuid.UUID | None = Field(
        default=None, description="Admins only: list this user's sessions instead of yours."
    )
