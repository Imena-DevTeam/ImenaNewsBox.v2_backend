"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Fixed paths (`/sub-families`, ...) are declared before `/{user_id}`, which would otherwise
try to read them as user IDs.
"""

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query

from app.auth.dependencies import AdminUser, CurrentUser, require_owner_or_admin
from app.core.database.session import DbSession
from app.core.pagination import Page, Pagination
from app.users import service
from app.users.models import Role
from app.users.schemas import (
    AccountListParams,
    PromotionRead,
    SubFamilyRead,
    UserCreate,
    UserPublicRead,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(user: CurrentUser, db: DbSession, params: Pagination) -> Page[UserPublicRead]:
    """The member directory: active members in alphabetical order of display name."""
    users, total = await service.list_users(db, offset=params.offset, limit=params.limit)
    items = [UserPublicRead.model_validate(member) for member in users]
    return Page.build(items, params=params, total=total)


@router.post("", status_code=HTTPStatus.CREATED)
async def create_user(body: UserCreate, admin: AdminUser, db: DbSession) -> UserRead:
    """Admins: register a member. To make an admin who can log in, also set a password."""
    return UserRead.model_validate(await service.create_user(db, body))


@router.get("/me")
async def get_me(user: CurrentUser, db: DbSession) -> UserRead:
    """Your own account, with the private fields."""
    return UserRead.model_validate(await service.get_user(db, user.id))


@router.get("/accounts")
async def list_accounts(
    admin: AdminUser, db: DbSession, params: Annotated[AccountListParams, Query()]
) -> Page[UserRead]:
    """Admins: every account with its private fields, for managing users."""
    users, total = await service.list_users(
        db,
        offset=params.offset,
        limit=params.limit,
        include_deactivated=params.include_deactivated,
    )
    items = [UserRead.model_validate(account) for account in users]
    return Page.build(items, params=params, total=total)


@router.get("/sub-families")
async def list_sub_families(user: CurrentUser, db: DbSession) -> list[SubFamilyRead]:
    """The fixed list of sub-families. Short and fixed, so not paginated."""
    return [SubFamilyRead.model_validate(row) for row in await service.list_sub_families(db)]


@router.get("/promotions")
async def list_promotions(user: CurrentUser, db: DbSession) -> list[PromotionRead]:
    """The fixed list of promotions. Short and fixed, so not paginated."""
    return [PromotionRead.model_validate(row) for row in await service.list_promotions(db)]


@router.get("/{user_id}")
async def get_user(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> UserPublicRead:
    """One active member, as every member sees them."""
    return UserPublicRead.model_validate(await service.get_user(db, user_id))


@router.get("/{user_id}/account")
async def get_account(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> UserRead:
    """One account with its private fields: your own, or anyone's for admins (deactivated
    users included)."""
    require_owner_or_admin(user, user_id)
    account = await service.get_user(
        db, user_id, include_deactivated=user.role.at_least(Role.ADMIN)
    )
    return UserRead.model_validate(account)


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, db: DbSession
) -> UserRead:
    """Admins: change any of a user's fields. Send only what changes."""
    return UserRead.model_validate(await service.update_user(db, admin, user_id, body))


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession) -> UserRead:
    """Admins: block a user's logins and sign them out of every device straight away."""
    return UserRead.model_validate(await service.deactivate_user(db, admin, user_id))


@router.post("/{user_id}/reactivate")
async def reactivate_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession) -> UserRead:
    """Admins: let a deactivated user log in again (on each device, from scratch)."""
    return UserRead.model_validate(await service.reactivate_user(db, user_id))
