"""Reading documents: the list, one document's details, and the PDF itself."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeMediaStore
from app.resources.models import Resource, ResourceCategory
from app.resources.tests.helpers import PDF, RESOURCES, upload
from app.users.models import User


async def _add(
    db: AsyncSession,
    uploader: User,
    title: str,
    *,
    category: ResourceCategory = ResourceCategory.DOCUMENTS,
    featured: bool = False,
    age: timedelta = timedelta(0),
) -> Resource:
    """A document row straight in the database, `age` old."""
    resource = Resource(
        title=title,
        category=category,
        featured=featured,
        file_name=f"{title}.pdf",
        file_size=100,
        file_public_id=f"test/{uuid.uuid4()}",
        uploaded_by_id=uploader.id,
        created_at=datetime.now(UTC) - age,
    )
    db.add(resource)
    await db.flush()
    return resource


def _titles(body: dict[str, list[dict[str, str]]]) -> list[str]:
    return [item["title"] for item in body["items"]]


# --- The list ---


async def test_members_see_documents_newest_first(
    client: AsyncClient, db_session: AsyncSession, writer: User, member_headers: dict[str, str]
) -> None:
    await _add(db_session, writer, "Oldest", age=timedelta(days=30))
    await _add(db_session, writer, "Newest")
    await _add(db_session, writer, "Middle", age=timedelta(days=2))

    response = await client.get(RESOURCES, headers=member_headers)

    assert response.status_code == 200
    body = response.json()
    assert _titles(body) == ["Newest", "Middle", "Oldest"]
    assert (body["total"], body["page"], body["limit"]) == (3, 1, 20)
    assert body["items"][0]["uploaded_by"]["display_name"] == "Ganza"


async def test_the_list_is_paginated(
    client: AsyncClient, db_session: AsyncSession, writer: User, member_headers: dict[str, str]
) -> None:
    for days in range(3):
        await _add(db_session, writer, f"Doc {days}", age=timedelta(days=days))

    response = await client.get(RESOURCES, params={"page": 2, "limit": 2}, headers=member_headers)

    assert _titles(response.json()) == ["Doc 2"]
    assert response.json()["total"] == 3


async def test_the_list_narrows_to_one_category(
    client: AsyncClient, db_session: AsyncSession, writer: User, member_headers: dict[str, str]
) -> None:
    await _add(db_session, writer, "A guide", category=ResourceCategory.GUIDES)
    await _add(db_session, writer, "A document")

    response = await client.get(RESOURCES, params={"category": "guides"}, headers=member_headers)

    assert _titles(response.json()) == ["A guide"]
    assert response.json()["total"] == 1


@pytest.mark.parametrize(("featured", "expected"), [("true", ["Spotlight"]), ("false", ["Plain"])])
async def test_the_list_narrows_to_featured_or_not(
    client: AsyncClient,
    db_session: AsyncSession,
    writer: User,
    member_headers: dict[str, str],
    featured: str,
    expected: list[str],
) -> None:
    await _add(db_session, writer, "Spotlight", featured=True)
    await _add(db_session, writer, "Plain")

    response = await client.get(RESOURCES, params={"featured": featured}, headers=member_headers)

    assert _titles(response.json()) == expected


async def test_an_unknown_category_is_a_validation_error(
    client: AsyncClient, member_headers: dict[str, str]
) -> None:
    response = await client.get(RESOURCES, params={"category": "recipes"}, headers=member_headers)

    assert response.status_code == 422


async def test_reading_needs_a_login(client: AsyncClient) -> None:
    assert (await client.get(RESOURCES)).status_code == 401


# --- One document ---


async def test_members_read_one_documents_details(
    client: AsyncClient, db_session: AsyncSession, writer: User, member_headers: dict[str, str]
) -> None:
    resource = await _add(db_session, writer, "Charter")

    response = await client.get(f"{RESOURCES}/{resource.id}", headers=member_headers)

    assert response.status_code == 200
    assert response.json()["title"] == "Charter"
    assert set(response.json()) == {
        "id",
        "title",
        "description",
        "category",
        "featured",
        "file_name",
        "file_size",
        "uploaded_by",
        "created_at",
        "updated_at",
    }


async def test_an_unknown_document_is_not_found(
    client: AsyncClient, member_headers: dict[str, str]
) -> None:
    for path in (f"{RESOURCES}/{uuid.uuid7()}", f"{RESOURCES}/{uuid.uuid7()}/file"):
        response = await client.get(path, headers=member_headers)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "resource_not_found"


# --- The file ---


async def test_members_get_the_pdf_shown_in_the_browser(
    client: AsyncClient, writer_headers: dict[str, str], member_headers: dict[str, str]
) -> None:
    uploaded = await upload(client, writer_headers, file_name="charter.pdf")

    response = await client.get(f"{RESOURCES}/{uploaded.json()['id']}/file", headers=member_headers)

    assert response.status_code == 200
    assert response.content == PDF
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        "inline; filename=\"charter.pdf\"; filename*=UTF-8''charter.pdf"
    )
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


async def test_download_asks_the_browser_to_save_it_under_its_full_name(
    client: AsyncClient, writer_headers: dict[str, str], member_headers: dict[str, str]
) -> None:
    uploaded = await upload(client, writer_headers, file_name="Itegeko ry'umuryango é.pdf")

    response = await client.get(
        f"{RESOURCES}/{uploaded.json()['id']}/file",
        params={"download": "true"},
        headers=member_headers,
    )

    assert response.headers["content-disposition"] == (
        'attachment; filename="Itegeko ry\'umuryango _.pdf"; '
        "filename*=UTF-8''Itegeko%20ry%27umuryango%20%C3%A9.pdf"
    )


async def test_a_storage_failure_is_reported_before_any_byte(
    client: AsyncClient,
    fake_media: FakeMediaStore,
    writer_headers: dict[str, str],
    member_headers: dict[str, str],
) -> None:
    uploaded = await upload(client, writer_headers)
    fake_media.fail_downloads = True

    response = await client.get(f"{RESOURCES}/{uploaded.json()['id']}/file", headers=member_headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "media_unavailable"


async def test_the_file_needs_a_login(
    client: AsyncClient, db_session: AsyncSession, writer: User
) -> None:
    resource = await _add(db_session, writer, "Charter")

    assert (await client.get(f"{RESOURCES}/{resource.id}/file")).status_code == 401
