import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeSanity
from app.posts import service
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
POSTS = "/api/v1/posts"
ASSET_ID = "image-cff88f7d8e03653e754ee6a403328beeeb5ab6bb-6048x6048-jpg"
ASSET = {
    "url": "https://cdn.sanity.io/images/p/production/cff88f.jpg",
    "width": 6048,
    "height": 6048,
}
BODY = [{"_type": "block", "children": [{"_type": "span", "text": "We went to Ikuzwe."}]}]
NEW_POST: dict[str, Any] = {
    "title": "A visit to our very own Ikuzwe",
    "summary": "A visit",
    "category": "Enjoyment",
    "image_asset_id": ASSET_ID,
    "body": BODY,
}


def _context(
    *, slug_taken: bool = False, asset: dict[str, Any] | None = ASSET, author: Any = None
) -> Callable[[str, dict[str, Any]], Any]:
    def handler(groq: str, params: dict[str, Any]) -> Any:
        if '"slug_taken"' in groq:
            return {"slug_taken": slug_taken, "asset": asset, "author": author}
        return {"total": 0, "items": []}

    return handler


def _created(fake_sanity: FakeSanity) -> dict[str, Any]:
    [batch] = fake_sanity.mutations
    [document] = [mutation["create"] for mutation in batch if "create" in mutation]
    assert isinstance(document, dict)
    return document


async def _stored_author(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await db.scalar(
        select(User.sanity_author_id)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )


@pytest.fixture
async def writer(make_user: MakeUser) -> User:
    return await make_user(role=Role.SOCIAL_MEDIA_MANAGER, display_name="Amata Uwase")


# --- Creating ---


async def test_a_writer_publishes_a_post(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context()

    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    assert response.status_code == 201, response.text
    post = response.json()
    assert post["slug"] == "a-visit-to-our-very-own-ikuzwe"
    assert post["image"] == {**ASSET, "hotspot": None, "crop": None}
    assert post["author"] == {
        "id": f"author-{writer.id}",
        "name": "Amata Uwase",
        "image_url": None,
    }
    assert post["body"][0]["children"][0]["text"] == "We went to Ikuzwe."

    document = _created(fake_sanity)
    assert document["_id"] == post["id"]
    assert document["_type"] == "post"
    assert document["slug"] == {"_type": "slug", "current": "a-visit-to-our-very-own-ikuzwe"}
    assert document["categories"] == "Enjoyment"
    assert document["author"] == {"_type": "reference", "_ref": f"author-{writer.id}"}
    assert document["image"] == {
        "_type": "image",
        "asset": {"_type": "reference", "_ref": ASSET_ID},
    }
    assert document["body"][0]["_key"]
    assert document["body"][0]["style"] == "normal"


async def test_the_first_post_creates_and_links_an_author(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    writer: User,
    login_as: LoginAs,
) -> None:
    fake_sanity.query_handler = _context()
    writer_id = writer.id

    await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    author_id = f"author-{writer_id}"
    [batch] = fake_sanity.mutations
    # One transaction: the author first, so the post's reference always resolves.
    assert batch[0] == {
        "createIfNotExists": {"_id": author_id, "_type": "author", "name": "Amata Uwase"}
    }
    assert batch[1] == {"patch": {"id": author_id, "set": {"name": "Amata Uwase"}}}
    assert "create" in batch[2]
    assert await _stored_author(db_session, writer_id) == author_id


async def test_a_linked_writer_keeps_their_author_and_its_name_follows_theirs(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    amata = {"id": "9819a068", "name": "Amata", "image_url": "https://cdn.sanity.io/a.jpg"}
    fake_sanity.query_handler = _context(author=amata)
    writer = await make_user(
        role=Role.SOCIAL_MEDIA_MANAGER, display_name="Amata Uwase", sanity_author_id="9819a068"
    )

    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    assert response.json()["author"] == {
        "id": "9819a068",
        "name": "Amata Uwase",
        "image_url": "https://cdn.sanity.io/a.jpg",
    }
    [batch] = fake_sanity.mutations
    assert batch[1] == {"patch": {"id": "9819a068", "set": {"name": "Amata Uwase"}}}
    assert _created(fake_sanity)["author"]["_ref"] == "9819a068"


async def test_admins_write_posts_too(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context()

    response = await client.post(
        POSTS, json=NEW_POST, headers=await login_as(await make_user(role=Role.ADMIN))
    )

    assert response.status_code == 201


async def test_the_slug_and_publish_date_can_be_chosen(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context()
    body = {**NEW_POST, "slug": "ikuzwe", "published_at": "2026-10-22T12:30:00.123456+02:00"}

    response = await client.post(POSTS, json=body, headers=await login_as(writer))

    assert response.json()["slug"] == "ikuzwe"
    # Stored the way the Studio stores dates: UTC with milliseconds.
    assert _created(fake_sanity)["publishedAt"] == "2026-10-22T10:30:00.123Z"


async def test_the_publish_date_defaults_to_now(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context()
    before = datetime.now(UTC)

    await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    published = datetime.fromisoformat(_created(fake_sanity)["publishedAt"])
    assert before.replace(microsecond=0) <= published <= datetime.now(UTC)


async def test_creating_a_post_clears_the_cache(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context()
    headers = await login_as(writer)
    await client.get(POSTS, headers=headers)
    await client.get(POSTS, headers=headers)
    assert len(fake_sanity.queries) == 1

    await client.post(POSTS, json=NEW_POST, headers=headers)
    await client.get(POSTS, headers=headers)

    # The list, the write's checks, then the list again from Sanity.
    assert len(fake_sanity.queries) == 3


# --- Refused ---


async def test_a_taken_slug_is_refused(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context(slug_taken=True)

    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slug_taken"
    assert fake_sanity.mutations == []


async def test_an_unknown_image_is_refused(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context(asset=None)

    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_image"
    assert fake_sanity.mutations == []


@pytest.mark.parametrize("missing", ["title", "summary", "category", "image_asset_id", "body"])
async def test_every_field_is_required(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    writer: User,
    login_as: LoginAs,
    missing: str,
) -> None:
    body = {key: value for key, value in NEW_POST.items() if key != missing}

    response = await client.post(POSTS, json=body, headers=await login_as(writer))

    assert response.status_code == 422
    assert fake_sanity.queries == []


async def test_a_javascript_link_is_refused_without_echoing_it(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    body = {
        **NEW_POST,
        "body": [
            {
                "_type": "block",
                "markDefs": [{"_type": "link", "_key": "l1", "href": "javascript:steal()"}],
                "children": [{"_type": "span", "text": "click", "marks": ["l1"]}],
            }
        ],
    }

    response = await client.post(POSTS, json=body, headers=await login_as(writer))

    assert response.status_code == 422
    assert "steal()" not in response.text


async def test_members_cannot_write_posts(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(await make_user()))

    assert response.status_code == 403
    assert (fake_sanity.queries, fake_sanity.mutations) == ([], [])


async def test_sanity_down_writes_and_links_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    writer: User,
    login_as: LoginAs,
) -> None:
    fake_sanity.unavailable = True
    writer_id = writer.id

    response = await client.post(POSTS, json=NEW_POST, headers=await login_as(writer))

    assert response.status_code == 502
    assert await _stored_author(db_session, writer_id) is None


async def test_a_failed_write_keeps_the_cache(
    client: AsyncClient, fake_sanity: FakeSanity, writer: User, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _context(slug_taken=True)
    headers = await login_as(writer)
    await client.get(POSTS, headers=headers)

    await client.post(POSTS, json=NEW_POST, headers=headers)

    assert ("list", 0, 20, None) in service._cache


async def test_writing_needs_a_login(client: AsyncClient) -> None:
    assert (await client.post(POSTS, json=NEW_POST)).status_code == 401
