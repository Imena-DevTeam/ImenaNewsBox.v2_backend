from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from app.core.fakes import FakeSanity
from app.users.models import User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
POSTS = "/api/v1/posts"

# What our GROQ projection returns for a post written in Studio.
IKUZWE: dict[str, Any] = {
    "id": "010a6464-cff7-497d-a08a-fc0add31e85c",
    "title": "A visit to our very own Ikuzwe",
    "slug": "visit-to-ikuzwe",
    "summary": "A visit to our very own Ikuzwe",
    "category": "Enjoyment",
    "published_at": "2025-09-16T09:44:00.000Z",
    "image": {
        "url": "https://cdn.sanity.io/images/p/production/abc-6048x6048.jpg",
        "width": 6048,
        "height": 6048,
        "hotspot": None,
        "crop": None,
    },
    "author": {"id": "9819a068", "name": "Amata", "image_url": None},
}
BODY = [
    {
        "_key": "1504bf121698",
        "_type": "block",
        "style": "normal",
        "markDefs": [],
        "children": [{"_key": "c1", "_type": "span", "marks": [], "text": "We went."}],
    }
]


def _serve(posts: list[dict[str, Any]]) -> Callable[[str, dict[str, Any]], Any]:
    """A fake Sanity holding these posts: answers our list and slug queries."""

    def handler(groq: str, params: dict[str, Any]) -> Any:
        if "slug.current == $slug" in groq:
            found = [post for post in posts if post["slug"] == params["slug"]]
            if not found:
                return None
            return {**found[0], "body": BODY, "updated_at": "2025-10-18T09:44:13Z"}
        return {"total": len(posts), "items": posts}

    return handler


@pytest.fixture
async def headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user())


# --- The list ---


async def test_members_read_the_list(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])

    response = await client.get(POSTS, headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"], body["limit"]) == (1, 1, 20)
    [post] = body["items"]
    assert post["title"] == IKUZWE["title"]
    assert post["category"] == "Enjoyment"
    assert post["author"] == {"id": "9819a068", "name": "Amata", "image_url": None}
    assert post["published_at"] == "2025-09-16T09:44:00Z"
    assert "body" not in post


async def test_the_page_and_category_reach_the_query(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([])

    query = {"page": "3", "limit": "5", "category": "Enjoyment"}
    await client.get(POSTS, params=query, headers=headers)

    [(groq, sent)] = fake_sanity.queries
    assert "[10...15]" in groq
    assert sent == {"category": "Enjoyment"}
    # Drafts are left out by the client's "published" perspective, not by the query.
    assert "order(publishedAt desc" in groq


async def test_posts_missing_fields_still_show(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    bare = {"id": "bare", "slug": "bare", "title": None, "image": None, "author": None}
    fake_sanity.query_handler = _serve([bare])

    response = await client.get(POSTS, headers=headers)

    assert response.status_code == 200
    [post] = response.json()["items"]
    assert (post["id"], post["title"], post["image"], post["summary"]) == ("bare", None, None, None)


async def test_an_empty_blog(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = lambda groq, params: None

    response = await client.get(POSTS, headers=headers)

    assert response.json()["items"] == []
    assert response.json()["total"] == 0


# --- One post ---


async def test_members_read_a_post_by_its_slug(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])

    response = await client.get(f"{POSTS}/visit-to-ikuzwe", headers=headers)

    assert response.status_code == 200
    post = response.json()
    assert post["body"] == BODY
    assert post["updated_at"] == "2025-10-18T09:44:13Z"
    [(_, params)] = fake_sanity.queries
    assert params == {"slug": "visit-to-ikuzwe"}


async def test_an_unknown_slug_is_not_found(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])

    response = await client.get(f"{POSTS}/no-such-post", headers=headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "post_not_found"


async def test_a_huge_slug_is_rejected_before_sanity(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    response = await client.get(f"{POSTS}/{'a' * 201}", headers=headers)

    assert response.status_code == 422
    assert fake_sanity.queries == []


# --- The cache ---


async def test_reads_are_cached_for_everyone(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    login_as: LoginAs,
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])

    for _ in range(2):
        headers = await login_as(await make_user())
        assert (await client.get(POSTS, headers=headers)).status_code == 200
        assert (await client.get(f"{POSTS}/visit-to-ikuzwe", headers=headers)).status_code == 200

    # One query for the list and one for the post, whoever asks.
    assert len(fake_sanity.queries) == 2


async def test_each_page_is_cached_separately(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])

    await client.get(POSTS, params={"page": 1}, headers=headers)
    await client.get(POSTS, params={"page": 2}, headers=headers)
    await client.get(POSTS, params={"category": "Enjoyment"}, headers=headers)

    assert len(fake_sanity.queries) == 3


async def test_not_found_is_not_cached(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    posts: list[dict[str, Any]] = []
    fake_sanity.query_handler = _serve(posts)
    assert (await client.get(f"{POSTS}/visit-to-ikuzwe", headers=headers)).status_code == 404

    posts.append(IKUZWE)

    assert (await client.get(f"{POSTS}/visit-to-ikuzwe", headers=headers)).status_code == 200


# --- Access and failures ---


async def test_posts_need_a_login_before_the_cache(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])
    # Fill the cache with a logged-in request first.
    await client.get(POSTS, headers=headers)

    assert (await client.get(POSTS)).status_code == 401
    assert (await client.get(f"{POSTS}/visit-to-ikuzwe")).status_code == 401


async def test_deactivated_members_cannot_read(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])
    user = await make_user()
    headers = await login_as(user)
    user.deactivated_at = datetime.now(UTC)

    assert (await client.get(POSTS, headers=headers)).status_code == 401


async def test_sanity_down_is_a_502_and_not_cached(
    client: AsyncClient, fake_sanity: FakeSanity, headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _serve([IKUZWE])
    fake_sanity.unavailable = True

    response = await client.get(POSTS, headers=headers)

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "cms_unavailable"
    fake_sanity.unavailable = False
    assert (await client.get(POSTS, headers=headers)).status_code == 200
