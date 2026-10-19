import io
from typing import Any

import cloudinary.exceptions
import pytest
from fastapi import UploadFile
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.media import (
    MAX_IMAGE_BYTES,
    CloudinaryMediaStore,
    ImageTooLarge,
    MediaNotConfigured,
    MediaUnavailable,
    UnsupportedImage,
    check_image,
    detect_image_format,
    read_image,
)

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 100
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 100


def _configured() -> Settings:
    return get_settings().model_copy(
        update={
            "cloudinary_cloud_name": "demo-cloud",
            "cloudinary_api_key": "key-123",
            "cloudinary_api_secret": SecretStr("shh"),
            "environment": "test",
        }
    )


def _unconfigured() -> Settings:
    return get_settings().model_copy(update={"cloudinary_cloud_name": ""})


@pytest.mark.parametrize(
    ("data", "expected"),
    [(JPEG, "jpeg"), (PNG, "png"), (WEBP, "webp"), (HEIC, "heic"), (b"GIF89a....", None)],
)
def test_detects_formats_from_content(data: bytes, expected: str | None) -> None:
    assert detect_image_format(data) == expected


def test_rejects_non_images_even_with_an_image_name() -> None:
    with pytest.raises(UnsupportedImage):
        check_image(b"<script>alert(1)</script>")


def test_rejects_images_over_the_limit() -> None:
    with pytest.raises(ImageTooLarge) as exc_info:
        check_image(JPEG + b"\x00" * MAX_IMAGE_BYTES)

    assert exc_info.value.status_code == 413


async def test_read_image_stops_one_byte_past_the_limit() -> None:
    upload = UploadFile(io.BytesIO(JPEG + b"\x00" * (2 * MAX_IMAGE_BYTES)), filename="a.jpg")

    with pytest.raises(ImageTooLarge):
        await read_image(upload)

    assert upload.file.tell() == MAX_IMAGE_BYTES + 1


async def test_read_image_returns_the_bytes() -> None:
    assert await read_image(UploadFile(io.BytesIO(PNG), filename="a.png")) == PNG


async def test_upload_passes_credentials_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_upload(file: Any, **options: Any) -> dict[str, Any]:
        calls.append((file, options))
        return {"public_id": "imena-newsbox/test/profile-photos/abc", "secure_url": "https://x"}

    monkeypatch.setattr("cloudinary.uploader.upload", fake_upload)

    image = await CloudinaryMediaStore(_configured()).upload_profile_photo(JPEG)

    assert image.public_id == "imena-newsbox/test/profile-photos/abc"
    assert image.url == "https://x"
    file, options = calls[0]
    assert file == ("photo", JPEG)
    assert options["cloud_name"] == "demo-cloud"
    assert options["api_key"] == "key-123"
    assert options["api_secret"] == "shh"
    assert options["folder"] == "imena-newsbox/test/profile-photos"
    assert options["overwrite"] is False


async def test_upload_checks_the_image_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cloudinary.uploader.upload", pytest.fail)

    with pytest.raises(UnsupportedImage):
        await CloudinaryMediaStore(_configured()).upload_profile_photo(b"not an image")


async def test_upload_errors_become_media_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_upload(file: Any, **options: Any) -> dict[str, Any]:
        raise cloudinary.exceptions.GeneralError("boom")

    monkeypatch.setattr("cloudinary.uploader.upload", failing_upload)

    with pytest.raises(MediaUnavailable) as exc_info:
        await CloudinaryMediaStore(_configured()).upload_profile_photo(JPEG)

    assert exc_info.value.status_code == 502


async def test_upload_without_credentials_fails_clearly() -> None:
    with pytest.raises(MediaNotConfigured):
        await CloudinaryMediaStore(_unconfigured()).upload_profile_photo(JPEG)


async def test_delete_calls_destroy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_destroy(public_id: str, **options: Any) -> dict[str, Any]:
        calls.append(public_id)
        return {"result": "ok"}

    monkeypatch.setattr("cloudinary.uploader.destroy", fake_destroy)

    await CloudinaryMediaStore(_configured()).delete_image("old/photo")

    assert calls == ["old/photo"]


async def test_delete_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_destroy(public_id: str, **options: Any) -> dict[str, Any]:
        raise cloudinary.exceptions.GeneralError("boom")

    monkeypatch.setattr("cloudinary.uploader.destroy", failing_destroy)

    await CloudinaryMediaStore(_configured()).delete_image("old/photo")
    await CloudinaryMediaStore(_unconfigured()).delete_image("old/photo")
