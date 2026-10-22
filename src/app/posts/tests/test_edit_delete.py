from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.fakes import FakeSanity
from app.posts import service
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
POST_ID = "010a6464-cff7-497d-a08a-fc0add31e85c"
POST_URL = f"/api/v1/posts/{POST_ID}"
NEW_ASSET_ID = "image-0123abcd-800x600-png"
NEW_ASSET = {
    "url": "https://cdn.sanity.io/images/p/production/0123abcd.png",
    "width": 800,
    "height": 600,
}
CURRENT: dict[str, Any] = {
    "id": POST_ID,
    "title": "A visit to our very own Ikuzwe",
    "slug": "visit-to-ikuzwe",
    "summary": "A visit",
    "category": "Enjoyment",
    "published_at": "2025-09-16T09:44:00.000Z",
    "image": {"url": "https://cdn.sanity.io/old.jpg", "width": 6048, "height": 6048},
    "author": {"id": "9819a068", "name": "Amata", "image_url": None},
    "body": [{"_key": "a", "_type": "block", "children": [{"_type": "span", "text": "Old"}]}],
    "updated_at": "2025-10-18T09:44:13Z",
}


def _sanity(
    *, post: dict[str, Any] | None = CURRENT, slug_taken: bool = False, asset: Any = NEW_ASSET
) -> Callable[[str, dict[str, Any]], Any]:
    def handler(groq: str, params: dict[str, Any]) -> Any:
        if groq.startswith("count("):  # does the post exist (delete)
            return 1 if post is not None else 0
        if '"post"' in groq:  # the write context (edit)
            return {"post": post, "slug_taken": slug_taken, "asset": asset}
        return {"total": 0, "items": []}

    return handler


@pytest.fixture
async def headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    # Another manager than the one who wrote it: any writer can edit any post.
    return await login_as(await make_user(role=Role.SOCIAL_MEDIA_MANAGER))


# --- Editing ---


async def test_a_writer_edits_any_post(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity()

    response = await client.patch(
        POST_URL, json={"title": "  A new title ", "category": "News"}, headers=headers
    )

    assert response.status_code == 200, response.text
    post = response.json()
    assert (post["title"], post["category"], post["summary"]) == ("A new title", "News", "A visit")
    # The author stays the original writer.
    assert post["author"]["id"] == "9819a068"
    assert fake_sanity.mutations == [
        [{"patch": {"id": POST_ID, "set": {"title": "A new title", "categories": "News"}}}]
    ]


async def test_every_field_can_change(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity()
    changes = {
        "slug": "ikuzwe-2026",
        "summary": "New summary",
        "published_at": "2026-01-01T00:00:00Z",
        "image_asset_id": NEW_ASSET_ID,
        "body": [{"_type": "block", "children": [{"_type": "span", "text": "New"}]}],
    }

    response = await client.patch(POST_URL, json=changes, headers=headers)

    post = response.json()
    assert post["slug"] == "ikuzwe-2026"
    assert post["image"] == {**NEW_ASSET, "hotspot": None, "crop": None}
    assert post["body"][0]["children"][0]["text"] == "New"
    [[mutation]] = fake_sanity.mutations
    patch = mutation["patch"]
    assert patch["set"]["slug"] == {"_type": "slug", "current": "ikuzwe-2026"}
    assert patch["set"]["publishedAt"] == "2026-01-01T00:00:00.000Z"
    assert patch["set"]["image"] == {
        "_type": "image",
        "asset": {"_type": "reference", "_ref": NEW_ASSET_ID},
    }


async def test_editing_clears_the_cache(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity()
    await client.get("/api/v1/posts", headers=headers)

    await client.patch(POST_URL, json={"title": "New"}, headers=headers)

    assert len(service._cache) == 0


async def test_an_empty_edit_changes_nothing(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity()

    response = await client.patch(POST_URL, json={}, headers=headers)

    assert response.json()["title"] == CURRENT["title"]
    assert fake_sanity.mutations == []


@pytest.mark.parametrize(
    ("changes", "handler", "status", "code"),
    [
        ({"title": "x"}, _sanity(post=None), 404, "post_not_found"),
        ({"slug": "taken"}, _sanity(slug_taken=True), 409, "slug_taken"),
        ({"image_asset_id": NEW_ASSET_ID}, _sanity(asset=None), 400, "unknown_image"),
    ],
    ids=["unknown-post", "slug-taken", "unknown-image"],
)
async def test_refused_edits_change_nothing(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    headers: dict[str, str],
    changes: dict[str, Any],
    handler: Callable[[str, dict[str, Any]], Any],
    status: int,
    code: str,
) -> None:
    fake_sanity.query_handler = handler

    response = await client.patch(POST_URL, json=changes, headers=headers)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert fake_sanity.mutations == []


@pytest.mark.parametrize(
    "changes",
    [{"title": None}, {"body": []}, {"author": "someone"}, {"slug": "Not A Slug"}],
    ids=["null", "empty-body", "author", "bad-slug"],
)
async def test_invalid_edits_are_rejected(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str], changes: dict[str, Any]
) -> None:
    response = await client.patch(POST_URL, json=changes, headers=headers)

    assert response.status_code == 422
    assert fake_sanity.queries == []


# --- Deleting ---


async def test_a_writer_deletes_any_post(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity()
    await client.get("/api/v1/posts", headers=headers)

    response = await client.delete(POST_URL, headers=headers)

    assert response.status_code == 204
    assert fake_sanity.mutations == [[{"delete": {"id": POST_ID}}]]
    assert len(service._cache) == 0


async def test_deleting_an_unknown_post(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _sanity(post=None)

    response = await client.delete(POST_URL, headers=headers)

    assert response.status_code == 404
    assert fake_sanity.mutations == []


# --- Access ---


@pytest.mark.parametrize("post_id", ["drafts.010a6464", "a b", "x" * 129])
async def test_malformed_post_ids_never_reach_sanity(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str], post_id: str
) -> None:
    url = f"/api/v1/posts/{post_id}"

    assert (await client.patch(url, json={"title": "x"}, headers=headers)).status_code == 422
    assert (await client.delete(url, headers=headers)).status_code == 422
    assert fake_sanity.queries == []


async def test_admins_edit_and_delete_too(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _sanity()
    headers = await login_as(await make_user(role=Role.ADMIN))

    assert (await client.patch(POST_URL, json={"title": "x"}, headers=headers)).status_code == 200
    assert (await client.delete(POST_URL, headers=headers)).status_code == 204


async def test_members_cannot_edit_or_delete(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    headers = await login_as(await make_user())

    assert (await client.patch(POST_URL, json={"title": "x"}, headers=headers)).status_code == 403
    assert (await client.delete(POST_URL, headers=headers)).status_code == 403
    assert (fake_sanity.queries, fake_sanity.mutations) == ([], [])


async def test_editing_and_deleting_need_a_login(client: AsyncClient) -> None:
    assert (await client.patch(POST_URL, json={"title": "x"})).status_code == 401
    assert (await client.delete(POST_URL)).status_code == 401
