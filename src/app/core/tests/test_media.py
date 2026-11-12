import io
from typing import Any

import cloudinary.exceptions
import httpx
import pytest
from fastapi import UploadFile
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.core.media import (
    MAX_DOCUMENT_BYTES,
    MAX_IMAGE_BYTES,
    CloudinaryMediaStore,
    DocumentTooLarge,
    ImageTooLarge,
    MediaNotConfigured,
    MediaUnavailable,
    UnsupportedDocument,
    UnsupportedImage,
    check_document,
    check_image,
    detect_image_format,
    read_document,
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


# --- Documents ---

PDF = b"%PDF-1.7\n" + b"\x00" * 100


def test_a_pdf_is_recognised_by_its_content() -> None:
    check_document(PDF)
    # The header may come after a little junk, within the first 1024 bytes.
    check_document(b"\xef\xbb\xbf" + PDF)


@pytest.mark.parametrize("data", [b"<html>not a pdf</html>", JPEG, b"\x00" * 1024 + PDF, b""])
def test_anything_else_is_not_a_document(data: bytes) -> None:
    with pytest.raises(UnsupportedDocument) as exc_info:
        check_document(data)

    assert exc_info.value.code == "unsupported_document"


def test_documents_over_10_mb_are_refused() -> None:
    with pytest.raises(DocumentTooLarge) as exc_info:
        check_document(PDF + b"\x00" * MAX_DOCUMENT_BYTES)

    assert exc_info.value.status_code == 413


async def test_read_document_stops_one_byte_past_the_limit() -> None:
    upload = UploadFile(io.BytesIO(PDF + b"\x00" * (2 * MAX_DOCUMENT_BYTES)), filename="a.pdf")

    with pytest.raises(DocumentTooLarge):
        await read_document(upload)

    assert upload.file.tell() == MAX_DOCUMENT_BYTES + 1


async def test_documents_are_uploaded_as_private_raw_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def fake_upload(file: Any, **options: Any) -> dict[str, Any]:
        calls.append((file, options))
        return {"public_id": "imena-newsbox/test/family-resources/abc"}

    monkeypatch.setattr("cloudinary.uploader.upload", fake_upload)

    public_id = await CloudinaryMediaStore(_configured()).upload_document(PDF)

    assert public_id == "imena-newsbox/test/family-resources/abc"
    file, options = calls[0]
    assert file == ("document", PDF)
    assert options["resource_type"] == "raw"
    assert options["type"] == "authenticated"
    assert options["folder"] == "imena-newsbox/test/family-resources"
    assert options["overwrite"] is False
    assert options["api_secret"] == "shh"


async def test_a_document_upload_checks_the_pdf_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cloudinary.uploader.upload", pytest.fail)

    with pytest.raises(UnsupportedDocument):
        await CloudinaryMediaStore(_configured()).upload_document(b"not a pdf")


async def test_document_upload_errors_become_media_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_upload(file: Any, **options: Any) -> dict[str, Any]:
        raise cloudinary.exceptions.GeneralError("boom")

    monkeypatch.setattr("cloudinary.uploader.upload", failing_upload)

    with pytest.raises(MediaUnavailable):
        await CloudinaryMediaStore(_configured()).upload_document(PDF)


async def test_a_document_is_downloaded_through_a_signed_url() -> None:
    requested: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(200, content=PDF)

    store = CloudinaryMediaStore(_configured(), transport=httpx.MockTransport(handler))

    chunks = await store.open_document("imena-newsbox/test/family-resources/abc")
    data = b"".join([chunk async for chunk in chunks])

    assert data == PDF
    [url] = requested
    assert url.scheme == "https"
    assert url.host == "res.cloudinary.com"
    path = url.path
    assert path.startswith("/demo-cloud/raw/authenticated/s--")
    assert path.endswith("/imena-newsbox/test/family-resources/abc")
    # The signature changes with the secret, so nobody can make one without it.
    other = _configured().model_copy(update={"cloudinary_api_secret": SecretStr("other")})
    other_store = CloudinaryMediaStore(other, transport=httpx.MockTransport(handler))
    await (await other_store.open_document("imena-newsbox/test/family-resources/abc")).aclose()
    assert requested[1].path != path


@pytest.mark.parametrize("status", [401, 404, 500])
async def test_a_failed_download_becomes_media_unavailable(
    status: int, caplog: pytest.LogCaptureFixture
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    store = CloudinaryMediaStore(_configured(), transport=transport)

    with pytest.raises(MediaUnavailable):
        await store.open_document("some/document")

    # The signed URL would let anyone fetch the file, so it's never logged.
    assert "s--" not in caplog.text


async def test_a_network_error_becomes_media_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    store = CloudinaryMediaStore(_configured(), transport=httpx.MockTransport(handler))

    with pytest.raises(MediaUnavailable):
        await store.open_document("some/document")


async def test_downloads_without_credentials_fail_clearly() -> None:
    with pytest.raises(MediaNotConfigured):
        await CloudinaryMediaStore(_unconfigured()).open_document("some/document")


async def test_document_deletes_name_the_private_raw_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_destroy(public_id: str, **options: Any) -> dict[str, Any]:
        calls.append((public_id, options))
        return {"result": "ok"}

    monkeypatch.setattr("cloudinary.uploader.destroy", fake_destroy)

    await CloudinaryMediaStore(_configured()).delete_document("old/document")

    [(public_id, options)] = calls
    assert public_id == "old/document"
    assert (options["resource_type"], options["type"]) == ("raw", "authenticated")


async def test_document_deletes_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_destroy(public_id: str, **options: Any) -> dict[str, Any]:
        raise cloudinary.exceptions.GeneralError("boom")

    monkeypatch.setattr("cloudinary.uploader.destroy", failing_destroy)

    await CloudinaryMediaStore(_configured()).delete_document("old/document")
    await CloudinaryMediaStore(_unconfigured()).delete_document("old/document")
