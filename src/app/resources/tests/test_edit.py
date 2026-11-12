"""Changing, replacing and deleting documents: any social media manager or admin may."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeMediaStore
from app.resources.models import Resource
from app.resources.tests.helpers import OTHER_PDF, PDF, RESOURCES, LoginAs, MakeUser, upload
from app.users.models import Role


@pytest.fixture
async def other_writer_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    """Another social media manager: not the one who uploaded."""
    return await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))


@pytest.fixture
async def resource_id(client: AsyncClient, writer_headers: dict[str, str]) -> str:
    response = await upload(client, writer_headers, description="First version")
    assert response.status_code == 201
    resource_id: str = response.json()["id"]
    return resource_id


async def _public_id(db: AsyncSession, resource_id: str) -> str:
    resource = await db.get(Resource, uuid.UUID(resource_id), populate_existing=True)
    assert resource is not None
    return resource.file_public_id


# --- Changing details ---


async def test_any_writer_changes_any_documents_details(
    client: AsyncClient, resource_id: str, other_writer_headers: dict[str, str]
) -> None:
    response = await client.patch(
        f"{RESOURCES}/{resource_id}",
        json={"title": "Charter (2026)", "category": "family_resources", "featured": True},
        headers=other_writer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["title"], body["category"], body["featured"]) == (
        "Charter (2026)",
        "family_resources",
        True,
    )
    # Left out, so unchanged; and the uploader is still the uploader.
    assert body["description"] == "First version"
    assert body["uploaded_by"]["display_name"] == "Ganza"


async def test_admins_change_details_too(
    client: AsyncClient, resource_id: str, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user(role=Role.ADMIN))

    response = await client.patch(
        f"{RESOURCES}/{resource_id}", json={"featured": True}, headers=headers
    )

    assert response.json()["featured"] is True


@pytest.mark.parametrize("cleared", [None, "  "])
async def test_the_description_can_be_cleared(
    client: AsyncClient, resource_id: str, writer_headers: dict[str, str], cleared: str | None
) -> None:
    response = await client.patch(
        f"{RESOURCES}/{resource_id}", json={"description": cleared}, headers=writer_headers
    )

    assert response.json()["description"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"title": None},
        {"title": ""},
        {"category": None},
        {"category": "recipes"},
        {"featured": None},
        {"file_name": "renamed.pdf"},
    ],
)
async def test_invalid_changes_are_rejected(
    client: AsyncClient, resource_id: str, writer_headers: dict[str, str], changes: dict[str, Any]
) -> None:
    response = await client.patch(
        f"{RESOURCES}/{resource_id}", json=changes, headers=writer_headers
    )

    assert response.status_code == 422


# --- Replacing the file ---


async def test_a_writer_replaces_the_pdf(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_media: FakeMediaStore,
    resource_id: str,
    other_writer_headers: dict[str, str],
    member_headers: dict[str, str],
) -> None:
    old = await _public_id(db_session, resource_id)

    response = await client.put(
        f"{RESOURCES}/{resource_id}/file",
        files={"file": ("charter v2.pdf", OTHER_PDF, "application/pdf")},
        headers=other_writer_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["file_name"], body["file_size"]) == ("charter v2.pdf", len(OTHER_PDF))
    assert body["title"] == "Family charter"
    # The old file is deleted afterwards; the new one is what members get.
    assert fake_media.deleted_documents == [old]
    file = await client.get(f"{RESOURCES}/{resource_id}/file", headers=member_headers)
    assert file.content == OTHER_PDF


async def test_a_bad_replacement_keeps_the_old_pdf(
    client: AsyncClient,
    fake_media: FakeMediaStore,
    resource_id: str,
    writer_headers: dict[str, str],
    member_headers: dict[str, str],
) -> None:
    not_a_pdf = await client.put(
        f"{RESOURCES}/{resource_id}/file",
        files={"file": ("x.pdf", b"<html></html>", "application/pdf")},
        headers=writer_headers,
    )
    fake_media.fail_uploads = True
    storage_down = await client.put(
        f"{RESOURCES}/{resource_id}/file",
        files={"file": ("x.pdf", OTHER_PDF, "application/pdf")},
        headers=writer_headers,
    )

    assert not_a_pdf.status_code == 400
    assert storage_down.status_code == 502
    assert fake_media.deleted_documents == []
    file = await client.get(f"{RESOURCES}/{resource_id}/file", headers=member_headers)
    assert file.content == PDF


# --- Deleting ---


async def test_a_writer_deletes_a_document_and_its_file(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_media: FakeMediaStore,
    resource_id: str,
    other_writer_headers: dict[str, str],
) -> None:
    public_id = await _public_id(db_session, resource_id)

    response = await client.delete(f"{RESOURCES}/{resource_id}", headers=other_writer_headers)

    assert response.status_code == 204
    assert fake_media.deleted_documents == [public_id]
    gone = await client.get(f"{RESOURCES}/{resource_id}", headers=other_writer_headers)
    assert gone.status_code == 404


# --- Who may ---


async def test_members_cannot_change_replace_or_delete(
    client: AsyncClient,
    fake_media: FakeMediaStore,
    resource_id: str,
    member_headers: dict[str, str],
) -> None:
    path = f"{RESOURCES}/{resource_id}"
    responses = [
        await client.patch(path, json={"title": "Mine now"}, headers=member_headers),
        await client.put(
            f"{path}/file",
            files={"file": ("x.pdf", OTHER_PDF, "application/pdf")},
            headers=member_headers,
        ),
        await client.delete(path, headers=member_headers),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "insufficient_role"
    assert fake_media.deleted_documents == []
    assert (await client.get(path, headers=member_headers)).json()["title"] == "Family charter"


async def test_changes_need_a_login(client: AsyncClient, resource_id: str) -> None:
    path = f"{RESOURCES}/{resource_id}"

    assert (await client.patch(path, json={"title": "x"})).status_code == 401
    assert (await client.delete(path)).status_code == 401


async def test_an_unknown_document_is_not_found(
    client: AsyncClient, fake_media: FakeMediaStore, writer_headers: dict[str, str]
) -> None:
    path = f"{RESOURCES}/{uuid.uuid7()}"
    responses = [
        await client.patch(path, json={"title": "x"}, headers=writer_headers),
        await client.put(
            f"{path}/file",
            files={"file": ("x.pdf", OTHER_PDF, "application/pdf")},
            headers=writer_headers,
        ),
        await client.delete(path, headers=writer_headers),
    ]

    for response in responses:
        assert response.status_code == 404
    # The replacement was never stored for a document that doesn't exist.
    assert list(fake_media.documents.values()).count(OTHER_PDF) == 0
