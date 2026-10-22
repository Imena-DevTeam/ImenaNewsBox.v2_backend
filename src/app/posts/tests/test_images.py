from collections.abc import Awaitable, Callable

import pytest
from httpx import AsyncClient

from app.core.fakes import FakeSanity
from app.core.media import MAX_IMAGE_BYTES
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
IMAGES = "/api/v1/posts/images"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 64


def _file(data: bytes, name: str = "photo.png") -> dict[str, tuple[str, bytes, str]]:
    return {"image": (name, data, "image/png")}


@pytest.mark.parametrize(
    ("data", "content_type"),
    [(JPEG, "image/jpeg"), (PNG, "image/png"), (WEBP, "image/webp")],
    ids=["jpeg", "png", "webp"],
)
@pytest.mark.parametrize("role", [Role.SOCIAL_MEDIA_MANAGER, Role.ADMIN])
async def test_writers_upload_a_post_image(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    login_as: LoginAs,
    role: Role,
    data: bytes,
    content_type: str,
) -> None:
    headers = await login_as(await make_user(role=role))

    response = await client.post(IMAGES, files=_file(data), headers=headers)

    assert response.status_code == 201, response.text
    assert response.json() == {
        "asset_id": "image-fake1-1x1-jpg",
        "url": "https://cdn.sanity.test/image-fake1-1x1-jpg.jpg",
    }
    # The type comes from the file's content, not from what the browser claimed.
    assert fake_sanity.uploads == [(data, content_type)]


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (HEIC, 400, "unsupported_image"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", 400, "unsupported_image"),
        (b"just text", 400, "unsupported_image"),
        (PNG + b"\x00" * MAX_IMAGE_BYTES, 413, "image_too_large"),
    ],
    ids=["heic", "svg", "text", "too-large"],
)
async def test_bad_files_never_reach_sanity(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    login_as: LoginAs,
    data: bytes,
    status: int,
    code: str,
) -> None:
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    response = await client.post(IMAGES, files=_file(data), headers=headers)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert fake_sanity.uploads == []


async def test_members_cannot_upload(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    response = await client.post(
        IMAGES, files=_file(PNG), headers=await login_as(await make_user())
    )

    assert response.status_code == 403
    assert fake_sanity.uploads == []


async def test_the_file_is_required(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    assert (await client.post(IMAGES, headers=headers)).status_code == 422


async def test_sanity_down_is_a_502(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    fake_sanity.unavailable = True
    headers = await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))

    response = await client.post(IMAGES, files=_file(PNG), headers=headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "cms_unavailable"


async def test_uploading_needs_a_login(client: AsyncClient) -> None:
    assert (await client.post(IMAGES, files=_file(PNG))).status_code == 401
