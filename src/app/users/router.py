"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Fixed paths (`/sub-families`, ...) are declared before `/{user_id}`, which would otherwise
try to read them as user IDs.
"""

import uuid

from fastapi import APIRouter

from app.auth.dependencies import CurrentUser
from app.core.database.session import DbSession
from app.core.pagination import Page, Pagination
from app.users import service
from app.users.schemas import PromotionRead, SubFamilyRead, UserPublicRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(user: CurrentUser, db: DbSession, params: Pagination) -> Page[UserPublicRead]:
    """The member directory: active members in alphabetical order of display name."""
    users, total = await service.list_users(db, offset=params.offset, limit=params.limit)
    items = [UserPublicRead.model_validate(member) for member in users]
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
