"""Profile photos on Cloudinary: check, upload and delete. Only this module uses the SDK.

The Cloudinary SDK is synchronous, so every call runs in a worker thread. Credentials are
passed per call instead of through the SDK's global config, so importing this module never
reads settings.
"""

import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Final, Protocol

import cloudinary.exceptions
import cloudinary.uploader
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError, BadRequest

logger = logging.getLogger(__name__)

MAX_IMAGE_BYTES: Final = 5 * 1024 * 1024
# Photos larger than this (in either dimension) are scaled down by Cloudinary on upload.
MAX_IMAGE_SIDE: Final = 1024
REQUEST_TIMEOUT_SECONDS: Final = 30


class ImageTooLarge(AppError):
    status_code = HTTPStatus.CONTENT_TOO_LARGE
    code = "image_too_large"
    default_message = f"Images can be at most {MAX_IMAGE_BYTES // (1024 * 1024)} MB."


class UnsupportedImage(BadRequest):
    code = "unsupported_image"
    default_message = "Upload a JPEG, PNG, WebP or HEIC image."


class MediaUnavailable(AppError):
    status_code = HTTPStatus.BAD_GATEWAY
    code = "media_unavailable"
    default_message = "The photo couldn't be uploaded. Try again in a moment."


class MediaNotConfigured(AppError):
    status_code = HTTPStatus.SERVICE_UNAVAILABLE
    code = "media_not_configured"
    default_message = "Photo uploads aren't set up on this server yet."


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


class CloudinaryMediaStore:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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


def get_media_store() -> MediaStore:
    """FastAPI dependency. Tests override it with a fake."""
    return CloudinaryMediaStore(get_settings())
