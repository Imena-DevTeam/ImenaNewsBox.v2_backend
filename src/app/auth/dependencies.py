"""Who is calling, and what they may do: the only place roles and ownership are checked.

Usage in a router or service:

    @router.get("/me")
    async def me(user: CurrentUser) -> UserRead: ...

    @router.post("")
    async def create_user(admin: AdminUser, ...) -> UserRead: ...

    # After loading a resource:
    require_owner_or_admin(user, wish.sender_id)
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.auth.exceptions import InsufficientRole, NotAuthenticated, NotOwner, SessionEnded
from app.auth.models import UserSession
from app.core.database.session import DbSession
from app.core.security import decode_access_token
from app.users.models import Role, User

# auto_error=False: a missing or non-Bearer header gets our own 401 body, not FastAPI's.
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class Authenticated:
    user: User
    session_id: uuid.UUID


async def get_authenticated(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Authenticated:
    """Check the access token, then load the user and their session in one query.

    The role and deactivation come from the database on every request and are never cached,
    so revoking a session, deactivating a user or changing a role takes effect immediately.
    """
    if credentials is None:
        raise NotAuthenticated()
    claims = decode_access_token(credentials.credentials)

    # Compare expiry times against the app's clock, which is the one that sets them. The
    # database's now() can drift from it and is frozen at the start of the transaction.
    now = datetime.now(UTC)
    user = await db.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            User.id == claims.user_id,
            User.deactivated_at.is_(None),
            UserSession.id == claims.session_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )
    if user is None:
        raise SessionEnded()
    return Authenticated(user=user, session_id=claims.session_id)


async def get_current_user(
    auth: Annotated[Authenticated, Depends(get_authenticated)],
) -> User:
    return auth.user


def require_role(minimum: Role) -> Callable[..., Awaitable[User]]:
    """A dependency that admits `minimum` and every role above it."""

    async def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
        if not user.role.at_least(minimum):
            raise InsufficientRole()
        return user

    return dependency


def require_owner_or_admin(user: User, owner_id: uuid.UUID, *, allow_admin: bool = True) -> None:
    """The single ownership check: the owner may act, and so may admins unless
    `allow_admin=False` (e.g. only a wish's sender may edit it). Otherwise 403."""
    if user.id == owner_id:
        return
    if allow_admin and user.role.at_least(Role.ADMIN):
        return
    raise NotOwner()


CurrentAuth = Annotated[Authenticated, Depends(get_authenticated)]
CurrentUser = Annotated[User, Depends(get_current_user)]
SocialMediaManagerUser = Annotated[User, Depends(require_role(Role.SOCIAL_MEDIA_MANAGER))]
AdminUser = Annotated[User, Depends(require_role(Role.ADMIN))]
