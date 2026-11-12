"""`POST /resources`: sharing a PDF."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeMediaStore
from app.core.media import MAX_DOCUMENT_BYTES
from app.resources.models import Resource
from app.resources.tests.helpers import PDF, RESOURCES, LoginAs, MakeUser, upload
from app.users.models import Role, User


async def _count(db: AsyncSession) -> int:
    return await db.scalar(select(func.count()).select_from(Resource)) or 0


async def test_a_social_media_manager_shares_a_pdf(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_media: FakeMediaStore,
    writer: User,
    writer_headers: dict[str, str],
) -> None:
    response = await upload(
        client,
        writer_headers,
        title="  Family charter ",
        description="Our rules,\r\nsince 1996.",
        category="guides",
        featured="true",
    )

    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Family charter"
    assert body["description"] == "Our rules,\nsince 1996."
    assert body["category"] == "guides"
    assert body["featured"] is True
    assert body["file_name"] == "charter.pdf"
    assert body["file_size"] == len(PDF)
    assert body["uploaded_by"] == {"id": str(writer.id), "display_name": "Ganza"}
    assert datetime.now(UTC) - datetime.fromisoformat(body["created_at"]) < timedelta(minutes=1)
    # The storage ID stays on the server.
    assert "file_public_id" not in body
    stored = await db_session.get(Resource, body["id"])
    assert stored is not None
    assert fake_media.documents[stored.file_public_id] == PDF


async def test_admins_share_documents_too(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user(role=Role.ADMIN))

    assert (await upload(client, headers)).status_code == 201


async def test_description_and_featured_are_optional(
    client: AsyncClient, writer_headers: dict[str, str]
) -> None:
    response = await upload(client, writer_headers, description="   ")

    assert (response.json()["description"], response.json()["featured"]) == (None, False)


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        ("Itegeko nshinga.PDF", "Itegeko nshinga.pdf"),
        ("C:\\Users\\me\\Desktop\\audit 2024.pdf", "audit 2024.pdf"),
        ("../../etc/passwd", "passwd.pdf"),
        ("minutes: 2024|2025?.pdf", "minutes_ 2024_2025_.pdf"),
        ("\u202eevil\u202dfdp.pdf", "evilfdp.pdf"),
        ("...", "document.pdf"),
        ("x" * 300 + ".pdf", "x" * 251 + ".pdf"),
    ],
)
async def test_the_file_name_is_cleaned_up(
    client: AsyncClient, writer_headers: dict[str, str], file_name: str, expected: str
) -> None:
    response = await upload(client, writer_headers, file_name=file_name)

    assert response.json()["file_name"] == expected


async def test_only_pdfs_are_accepted(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_media: FakeMediaStore,
    writer_headers: dict[str, str],
) -> None:
    response = await upload(client, writer_headers, data=b"MZ\x90\x00", file_name="looks.pdf")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_document"
    assert fake_media.documents == {}
    assert await _count(db_session) == 0


async def test_documents_over_10_mb_are_refused(
    client: AsyncClient, fake_media: FakeMediaStore, writer_headers: dict[str, str]
) -> None:
    response = await upload(client, writer_headers, data=PDF + b"\x00" * MAX_DOCUMENT_BYTES)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_large"
    assert fake_media.documents == {}


async def test_a_storage_failure_saves_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_media: FakeMediaStore,
    writer_headers: dict[str, str],
) -> None:
    fake_media.fail_uploads = True

    response = await upload(client, writer_headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "media_unavailable"
    assert await _count(db_session) == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"category": "recipes"},
        {"description": "x" * 2001},
        {"description": "bell\x07"},
        {"featured": "maybe"},
        {"uploaded_by_id": "someone"},
    ],
)
async def test_invalid_details_are_rejected(
    client: AsyncClient,
    fake_media: FakeMediaStore,
    writer_headers: dict[str, str],
    fields: dict[str, str],
) -> None:
    response = await upload(client, writer_headers, form=fields)

    assert response.status_code == 422
    assert fake_media.documents == {}


async def test_the_file_is_required(client: AsyncClient, writer_headers: dict[str, str]) -> None:
    response = await client.post(
        RESOURCES, data={"title": "No file", "category": "documents"}, headers=writer_headers
    )

    assert response.status_code == 422


async def test_members_cannot_share_documents(
    client: AsyncClient, fake_media: FakeMediaStore, member_headers: dict[str, str]
) -> None:
    response = await upload(client, member_headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_role"
    assert fake_media.documents == {}


async def test_sharing_needs_a_login(client: AsyncClient, fake_media: FakeMediaStore) -> None:
    assert (await upload(client, {})).status_code == 401
    assert fake_media.documents == {}
