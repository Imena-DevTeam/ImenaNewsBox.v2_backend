"""Files on Cloudinary: profile photos, and the family's PDF documents (`resources`). Only
this module uses the SDK.

- **Photos** are public images: their URL goes straight to the browser.
- **Documents** are private: uploaded as `authenticated` raw files, which Cloudinary only
  serves through a signed URL. The signed URL never leaves the server: the API downloads the
  file and streams it to a logged-in member (`open_document`).

The Cloudinary SDK is synchronous, so every call runs in a worker thread. Credentials are
passed per call instead of through the SDK's global config, so importing this module never
reads settings.
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final, Protocol

import cloudinary.exceptions
import cloudinary.uploader
import cloudinary.utils
import httpx
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, BadRequest

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES: Final = 5 * 1024 * 1024
# Photos larger than this (in either dimension) are scaled down by Cloudinary on upload.
MAX_IMAGE_SIDE: Final = 1024
# Cloudinary's free plan refuses raw files over 10 MB.
MAX_DOCUMENT_BYTES: Final = 10 * 1024 * 1024
# The PDF header must appear within the first 1024 bytes (most files start with it).
PDF_HEADER_WINDOW: Final = 1024
REQUEST_TIMEOUT_SECONDS: Final = 30
# Documents are private raw files, served only through a signed URL.
DOCUMENT_RESOURCE_TYPE: Final = "raw"
DOCUMENT_DELIVERY_TYPE: Final = "authenticated"


class ImageTooLarge(AppError):
    status_code = HTTPStatus.CONTENT_TOO_LARGE
    code = "image_too_large"
    default_message = f"Images can be at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB."


class UnsupportedImage(BadRequest):
    code = "unsupported_image"
    default_message = "Upload a JPEG, PNG, WebP or HEIC image."


class DocumentTooLarge(AppError):
    status_code = HTTPStatus.CONTENT_TOO_LARGE
    code = "document_too_large"
    default_message = f"Documents can be at most {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB."


class UnsupportedDocument(BadRequest):
    code = "unsupported_document"
    default_message = "Upload a PDF."


class MediaUnavailable(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    code = "media_unavailable"
    default_message = "The file couldn't be stored or fetched. Try again in a moment."


class MediaNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "media_not_configured"
    default_message = "File storage (Cloudinary) isn't set up on this server yet."


@dataclass(frozen=True, slots=True)
class UploadedImage:
    public_id: str
    url: str


class MediaStore(Protocol):
    async def upload_profile_photo(self, data: bytes) -> UploadedImage:
        """Upload checked image bytes (see `read_image`). Raises `MediaUnavailable`."""
        ...

    async def delete_image(self, public_id: str) -> None:
        """Delete an image. Never raises: meant for `BackgroundTasks`, where a lost delete
        only leaves an unused image behind."""
        ...

    async def upload_document(self, data: bytes) -> str:
        """Upload checked PDF bytes (see `read_document`) as a private file and return its
        public ID. Raises `MediaUnavailable`."""
        ...

    async def open_document(self, public_id: str) -> AsyncIterator[bytes]:
        """Start downloading a private document and return its bytes as they arrive. Raises
        `MediaUnavailable` before returning if it can't be fetched, so the caller can still
        answer with an error. Iterate it to the end, or `aclose()` it."""
        ...

    async def delete_document(self, public_id: str) -> None:
        """Delete a document. Never raises, like `delete_image`."""
        ...


def detect_image_format(data: bytes) -> str | None:
    """The image format from the file's first bytes, ignoring its name and content type."""
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[4:8] == b"ftyp" and data[8:12] in {b"heic", b"heix", b"mif1", b"msf1"}:
        return "heic"
    return None


def check_image(data: bytes) -> str:
    """Raise unless `data` is a supported image within the size limit. Returns its format."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageTooLarge()
    image_format = detect_image_format(data)
    if image_format is None:
        raise UnsupportedImage()
    return image_format


async def read_image(upload: UploadFile) -> bytes:
    """Read and check an uploaded image, reading at most one byte past the limit."""
    data = await upload.read(MAX_IMAGE_BYTES + 1)
    check_image(data)
    return data


def check_document(data: bytes) -> None:
    """Raise unless `data` is a PDF within the size limit, judged by its content (never its
    name or content type)."""
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentTooLarge()
    if b"%PDF-" not in data[:PDF_HEADER_WINDOW]:
        raise UnsupportedDocument()


async def read_document(upload: UploadFile) -> bytes:
    """Read and check an uploaded PDF, reading at most one byte past the limit."""
    data = await upload.read(MAX_DOCUMENT_BYTES + 1)
    check_document(data)
    return data


async def _stream(client: httpx.AsyncClient, response: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()
        await client.aclose()


class CloudinaryMediaStore:
    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        # Tests pass an `httpx.MockTransport` for downloads; production uses the network.
        self._transport = transport

    def _options(self) -> dict[str, Any]:
        settings = self._settings
        secret = settings.cloudinary_api_secret.get_secret_value()
        if not (settings.cloudinary_cloud_name and settings.cloudinary_api_key and secret):
            raise MediaNotConfigured()
        return {
            "cloud_name": settings.cloudinary_cloud_name,
            "api_key": settings.cloudinary_api_key,
            "api_secret": secret,
            "timeout": REQUEST_TIMEOUT_SECONDS,
        }

    async def upload_profile_photo(self, data: bytes) -> UploadedImage:
        check_image(data)
        options = self._options()
        try:
            # A (name, bytes) tuple: the SDK treats a plain string as a local path or URL.
            result = await run_in_threadpool(
                cloudinary.uploader.upload,
                ("photo", data),
                folder=f"imena-newsbox/{self._settings.environment}/profile-photos",
                resource_type="image",
                allowed_formats=["jpg", "png", "webp", "heic"],
                unique_filename=True,
                overwrite=False,
                # Stored as a JPEG of at most 1024px a side: HEIC can't be shown in most
                # browsers, and smaller files stay well inside the free plan.
                format="jpg",
                transformation=[
                    {"width": MAX_IMAGE_SIDE, "height": MAX_IMAGE_SIDE, "crop": "limit"},
                    {"quality": "auto"},
                ],
                **options,
            )
        except cloudinary.exceptions.Error as exc:
            logger.warning("Cloudinary upload failed: %s", type(exc).__name__)
            raise MediaUnavailable() from exc
        return UploadedImage(public_id=result["public_id"], url=result["secure_url"])

    async def delete_image(self, public_id: str) -> None:
        try:
            result = await run_in_threadpool(
                cloudinary.uploader.destroy,
                public_id,
                resource_type="image",
                invalidate=True,
                **self._options(),
            )
        except (cloudinary.exceptions.Error, MediaNotConfigured) as exc:
            logger.warning("Cloudinary delete of %s failed: %s", public_id, type(exc).__name__)
            return
        if result.get("result") not in {"ok", "not found"}:
            logger.warning("Cloudinary delete of %s returned %r", public_id, result.get("result"))

    async def upload_document(self, data: bytes) -> str:
        check_document(data)
        options = self._options()
        try:
            result = await run_in_threadpool(
                cloudinary.uploader.upload,
                ("document", data),
                folder=f"imena-newsbox/{self._settings.environment}/family-resources",
                resource_type=DOCUMENT_RESOURCE_TYPE,
                type=DOCUMENT_DELIVERY_TYPE,
                unique_filename=True,
                overwrite=False,
                **options,
            )
        except cloudinary.exceptions.Error as exc:
            logger.warning("Cloudinary document upload failed: %s", type(exc).__name__)
            raise MediaUnavailable() from exc
        public_id: str = result["public_id"]
        return public_id

    def _signed_document_url(self, public_id: str) -> str:
        options = self._options()
        options.pop("timeout")
        url, _ = cloudinary.utils.cloudinary_url(
            public_id,
            resource_type=DOCUMENT_RESOURCE_TYPE,
            type=DOCUMENT_DELIVERY_TYPE,
            sign_url=True,
            secure=True,
            **options,
        )
        return str(url)

    async def open_document(self, public_id: str) -> AsyncIterator[bytes]:
        url = self._signed_document_url(public_id)
        client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS, transport=self._transport)
        try:
            response = await client.send(client.build_request("GET", url), stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.warning("Cloudinary document download failed: %s", type(exc).__name__)
            raise MediaUnavailable() from exc
        if response.status_code != HTTPStatus.OK:
            await response.aclose()
            await client.aclose()
            # Never the URL: it's signed.
            logger.warning(
                "Cloudinary document download of %s returned %d", public_id, response.status_code
            )
            raise MediaUnavailable()
        return _stream(client, response)

    async def delete_document(self, public_id: str) -> None:
        try:
            result = await run_in_threadpool(
                cloudinary.uploader.destroy,
                public_id,
                resource_type=DOCUMENT_RESOURCE_TYPE,
                type=DOCUMENT_DELIVERY_TYPE,
                invalidate=True,
                **self._options(),
            )
        except (cloudinary.exceptions.Error, MediaNotConfigured) as exc:
            logger.warning(
                "Cloudinary document delete of %s failed: %s", public_id, type(exc).__name__
            )
            return
        if result.get("result") not in {"ok", "not found"}:
            logger.warning(
                "Cloudinary document delete of %s returned %r", public_id, result.get("result")
            )


def get_media_store() -> MediaStore:
    """FastAPI dependency. Tests override it with a fake."""
    return CloudinaryMediaStore(get_settings())
