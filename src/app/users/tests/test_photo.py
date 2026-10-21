import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.core.fakes import FakeMediaStore
from app.core.media import MAX_IMAGE_BYTES
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
OLD_PHOTO = {
    "photo_public_id": "test/profile-photos/old",
    "photo_url": "https://media.test/old.jpg",
}


def _photo_url(user: User) -> str:
    return f"/api/v1/users/{user.id}/photo"


def _file(data: bytes = PNG, name: str = "me.png") -> dict[str, tuple[str, bytes, str]]:
    return {"photo": (name, data, "image/png")}


# --- Uploading ---


async def test_you_upload_your_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.put(_photo_url(user), files=_file(), headers=await login_as(user))

    assert response.status_code == 200
    [(public_id, data)] = fake_media.uploads.items()
    assert data == PNG
    assert response.json()["photo_url"] == f"https://media.test/{public_id}.jpg"
    assert user.photo_public_id == public_id
    assert fake_media.deleted == []


async def test_a_replaced_photo_is_deleted_afterwards(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(**OLD_PHOTO)

    response = await client.put(
        _photo_url(user), files=_file(JPEG, "me.jpg"), headers=await login_as(user)
    )

    assert response.status_code == 200
    assert response.json()["photo_url"] != OLD_PHOTO["photo_url"]
    assert fake_media.deleted == [OLD_PHOTO["photo_public_id"]]


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"just some text, not an image", 400, "unsupported_image"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", 400, "unsupported_image"),
        (PNG + b"\x00" * MAX_IMAGE_BYTES, 413, "image_too_large"),
    ],
    ids=["text", "svg", "too-large"],
)
async def test_bad_files_are_refused_and_the_old_photo_kept(
    client: AsyncClient,
    fake_media: FakeMediaStore,
    make_user: MakeUser,
    login_as: LoginAs,
    data: bytes,
    status: int,
    code: str,
) -> None:
    user = await make_user(**OLD_PHOTO)

    response = await client.put(_photo_url(user), files=_file(data), headers=await login_as(user))

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert (fake_media.uploads, fake_media.deleted) == ({}, [])
    assert user.photo_url == OLD_PHOTO["photo_url"]


async def test_a_failed_upload_keeps_the_old_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(**OLD_PHOTO)
    fake_media.fail_uploads = True

    response = await client.put(_photo_url(user), files=_file(), headers=await login_as(user))

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "media_unavailable"
    assert user.photo_url == OLD_PHOTO["photo_url"]
    assert fake_media.deleted == []


async def test_the_file_is_required(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.put(_photo_url(user), headers=await login_as(user))

    assert response.status_code == 422


async def test_members_cannot_change_someone_elses_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    other = await make_user(**OLD_PHOTO)
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    response = await client.put(_photo_url(other), files=_file(), headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "not_owner"
    assert fake_media.uploads == {}


async def test_admins_can_change_anyones_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    other = await make_user(**OLD_PHOTO)
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.put(_photo_url(other), files=_file(), headers=headers)

    assert response.status_code == 200
    assert fake_media.deleted == [OLD_PHOTO["photo_public_id"]]


async def test_admins_can_change_a_deactivated_users_photo(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    gone = await make_user(deactivated_at=datetime.now(UTC))
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.put(_photo_url(gone), files=_file(), headers=headers)

    assert response.status_code == 200


async def test_an_unknown_user_is_not_found(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.put(
        f"/api/v1/users/{uuid.uuid7()}/photo", files=_file(), headers=headers
    )

    assert response.status_code == 404


# --- Removing ---


async def test_you_remove_your_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(**OLD_PHOTO)

    response = await client.delete(_photo_url(user), headers=await login_as(user))

    assert response.status_code == 200
    assert response.json()["photo_url"] is None
    assert user.photo_public_id is None
    assert fake_media.deleted == [OLD_PHOTO["photo_public_id"]]


async def test_removing_a_missing_photo_changes_nothing(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user()

    response = await client.delete(_photo_url(user), headers=await login_as(user))

    assert response.status_code == 200
    assert fake_media.deleted == []


async def test_members_cannot_remove_someone_elses_photo(
    client: AsyncClient, fake_media: FakeMediaStore, make_user: MakeUser, login_as: LoginAs
) -> None:
    other = await make_user(**OLD_PHOTO)

    response = await client.delete(_photo_url(other), headers=await login_as(await make_user()))

    assert response.status_code == 403
    assert fake_media.deleted == []


async def test_photos_need_a_login(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()

    assert (await client.put(_photo_url(user), files=_file())).status_code == 401
    assert (await client.delete(_photo_url(user))).status_code == 401
