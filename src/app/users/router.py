"""HTTP routes: parse input, call the service, shape the response. No business logic here.

Fixed paths (`/sub-families`, ...) are declared before `/{user_id}`, which would otherwise
try to read them as user IDs.
"""

import uuid
from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, UploadFile

from app.auth import service as auth_service
from app.auth.dependencies import AdminUser, CurrentUser, require_owner_or_admin
from app.auth.schemas import CodeSent
from app.core.database.session import DbSession
from app.core.email import EmailSender, get_email_sender
from app.core.media import MediaStore, get_media_store, read_image
from app.core.pagination import Page, Pagination
from app.core.sanity import Sanity, get_sanity
from app.core.security import CODE_TTL
from app.posts import service as posts_service
from app.users import service
from app.users.models import Role
from app.users.schemas import (
    AccountListParams,
    AuthorLink,
    EmailConfirm,
    PasswordSet,
    ProfileUpdate,
    PromotionRead,
    SubFamilyRead,
    UserCreate,
    UserPublicRead,
    UserRead,
    UserUpdate,
)

router = APIRouter(prefix="/users", tags=["users"])

Media = Annotated[MediaStore, Depends(get_media_store)]
Email = Annotated[EmailSender, Depends(get_email_sender)]
SanityApi = Annotated[Sanity, Depends(get_sanity)]


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


@router.patch("/me")
async def update_me(body: ProfileUpdate, user: CurrentUser, db: DbSession) -> UserRead:
    """Change your own display name or birthday. Admins change everything else."""
    return UserRead.model_validate(await service.update_profile(db, user, body))


@router.post("/me/email/resend")
async def resend_email_confirmation(user: CurrentUser, db: DbSession, sender: Email) -> CodeSent:
    """Email a new code to confirm your address (after an admin changed it, say)."""
    await service.send_email_confirmation(db, sender, user)
    return CodeSent(
        message="A confirmation code is on its way.",
        expires_in=int(CODE_TTL.total_seconds()),
        resend_after=int(auth_service.CODE_RESEND_COOLDOWN.total_seconds()),
    )


@router.post("/me/email/verify")
async def confirm_email(body: EmailConfirm, user: CurrentUser, db: DbSession) -> UserRead:
    """Confirm your email address with the code sent to it."""
    return UserRead.model_validate(await service.confirm_email(db, user, body.code))


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
    user_id: uuid.UUID, body: UserUpdate, admin: AdminUser, db: DbSession, sender: Email
) -> UserRead:
    """Admins: change any of a user's fields. Send only what changes. A new email
    address gets a confirmation code."""
    account = await service.update_user(db, admin, user_id, body, sender)
    return UserRead.model_validate(account)


@router.post("/{user_id}/deactivate")
async def deactivate_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession) -> UserRead:
    """Admins: block a user's logins and sign them out of every device straight away."""
    return UserRead.model_validate(await service.deactivate_user(db, admin, user_id))


@router.post("/{user_id}/reactivate")
async def reactivate_user(user_id: uuid.UUID, admin: AdminUser, db: DbSession) -> UserRead:
    """Admins: let a deactivated user log in again (on each device, from scratch)."""
    return UserRead.model_validate(await service.reactivate_user(db, user_id))


@router.put("/{user_id}/password", status_code=HTTPStatus.NO_CONTENT)
async def set_admin_password(
    user_id: uuid.UUID, body: PasswordSet, admin: AdminUser, db: DbSession
) -> None:
    """Admins: set an admin's password (at least 12 characters), e.g. after promoting them."""
    await service.set_admin_password(db, user_id, body.password.get_secret_value())


@router.put("/{user_id}/sanity-author")
async def link_sanity_author(
    user_id: uuid.UUID, body: AuthorLink, admin: AdminUser, db: DbSession, sanity: SanityApi
) -> UserRead:
    """Admins: credit a user's blog posts to an existing Sanity author (`null` unlinks), so
    someone who wrote posts in the Studio keeps one author. Each author links to one user."""
    account = await posts_service.link_author(db, sanity, user_id, body.author_id)
    return UserRead.model_validate(account)


@router.put("/{user_id}/photo")
async def set_photo(
    user_id: uuid.UUID,
    photo: UploadFile,
    user: CurrentUser,
    db: DbSession,
    media: Media,
    background: BackgroundTasks,
) -> UserRead:
    """Upload a profile photo (multipart field `photo`: JPEG, PNG, WebP or HEIC, at most
    5 MB): your own, or anyone's for admins. The photo it replaces is deleted afterwards."""
    data = await read_image(photo)
    account, replaced = await service.set_photo(db, user, user_id, data, media)
    if replaced is not None:
        background.add_task(media.delete_image, replaced)
    return UserRead.model_validate(account)


@router.delete("/{user_id}/photo")
async def remove_photo(
    user_id: uuid.UUID, user: CurrentUser, db: DbSession, media: Media, background: BackgroundTasks
) -> UserRead:
    """Remove a profile photo: your own, or anyone's for admins."""
    account, removed = await service.remove_photo(db, user, user_id)
    if removed is not None:
        background.add_task(media.delete_image, removed)
    return UserRead.model_validate(account)
