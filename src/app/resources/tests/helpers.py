"""Shared helpers for the resources tests."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Final

from httpx import AsyncClient, Response

from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
RESOURCES: Final = "/api/v1/resources"
PDF: Final = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
OTHER_PDF: Final = b"%PDF-1.4\nanother document\n%%EOF\n"


async def upload(
    client: AsyncClient,
    headers: dict[str, str],
    *,
    data: bytes = PDF,
    file_name: str = "charter.pdf",
    form: Mapping[str, str] | None = None,
    **fields: str,
) -> Response:
    """POST a document the way the frontend does: multipart, with the PDF in `file`. Form
    fields come from `fields` (or `form`, for names that aren't valid keywords here)."""
    form = {"title": "Family charter", "category": "documents"} | dict(form or {}) | fields
    files = {"file": (file_name, data, "application/pdf")}
    return await client.post(RESOURCES, data=form, files=files, headers=headers)
