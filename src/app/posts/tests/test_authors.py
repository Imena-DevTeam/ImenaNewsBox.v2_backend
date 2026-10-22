import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.fakes import FakeSanity
from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
AUTHORS = "/api/v1/posts/authors"
AMATA = {"id": "9819a068-324d-4179-ab0d-7e5de3ef8521", "name": "Amata", "image_url": None}
GANZA = {"id": "03369014-6b33-4cca-ab88-59ac2feceef0", "name": "Ganza", "image_url": None}


def _authors(*authors: dict[str, Any]) -> Callable[[str, dict[str, Any]], Any]:
    """A fake Sanity holding these authors: answers the list and the "does it exist" count."""

    def handler(groq: str, params: dict[str, Any]) -> Any:
        if groq.startswith("count("):
            return sum(author["id"] == params["id"] for author in authors)
        return list(authors)

    return handler


def _link_url(user: User) -> str:
    return f"/api/v1/users/{user.id}/sanity-author"


async def _stored_author(db: AsyncSession, user_id: uuid.UUID) -> str | None:
    return await db.scalar(
        select(User.sanity_author_id)
        .where(User.id == user_id)
        .execution_options(populate_existing=True)
    )


@pytest.fixture
async def admin_headers(make_user: MakeUser, login_as: LoginAs) -> dict[str, str]:
    return await login_as(await make_user(role=Role.ADMIN))


# --- Listing authors ---


async def test_admins_list_authors_with_who_they_are_linked_to(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    fake_sanity.query_handler = _authors(AMATA, GANZA)
    amata = await make_user(sanity_author_id=AMATA["id"])

    response = await client.get(AUTHORS, headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == [
        {**AMATA, "user_id": str(amata.id)},
        {**GANZA, "user_id": None},
    ]


async def test_no_authors_yet(
    client: AsyncClient, fake_sanity: FakeSanity, admin_headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = lambda groq, params: None

    response = await client.get(AUTHORS, headers=admin_headers)

    assert response.json() == []


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_list_authors(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    response = await client.get(AUTHORS, headers=await login_as(await make_user(role=role)))

    assert response.status_code == 403
    assert fake_sanity.queries == []


# --- Linking ---


async def test_admins_link_a_user_to_an_author(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    user = await make_user(role=Role.SOCIAL_MEDIA_MANAGER)

    response = await client.put(
        _link_url(user), json={"author_id": AMATA["id"]}, headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["sanity_author_id"] == AMATA["id"]
    assert await _stored_author(db_session, user.id) == AMATA["id"]
    [(_, params)] = fake_sanity.queries
    assert params == {"id": AMATA["id"]}


async def test_null_unlinks_without_asking_sanity(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    user = await make_user(sanity_author_id=AMATA["id"])

    response = await client.put(_link_url(user), json={"author_id": None}, headers=admin_headers)

    assert response.json()["sanity_author_id"] is None
    assert await _stored_author(db_session, user.id) is None
    assert fake_sanity.queries == []


async def test_an_unknown_author_is_refused(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    user = await make_user()

    response = await client.put(
        _link_url(user), json={"author_id": GANZA["id"]}, headers=admin_headers
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unknown_author"
    assert await _stored_author(db_session, user.id) is None


async def test_an_author_links_to_one_user_only(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    await make_user(sanity_author_id=AMATA["id"])
    other = await make_user()
    other_id = other.id

    response = await client.put(
        _link_url(other), json={"author_id": AMATA["id"]}, headers=admin_headers
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "author_already_linked"
    assert await _stored_author(db_session, other_id) is None


async def test_linking_the_same_author_again_changes_nothing(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    user = await make_user(sanity_author_id=AMATA["id"])

    response = await client.put(
        _link_url(user), json={"author_id": AMATA["id"]}, headers=admin_headers
    )

    assert response.status_code == 200


async def test_deactivated_users_can_be_linked(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    gone = await make_user(deactivated_at=datetime.now(UTC))

    response = await client.put(
        _link_url(gone), json={"author_id": AMATA["id"]}, headers=admin_headers
    )

    assert response.status_code == 200


@pytest.mark.parametrize(
    "author_id",
    ["drafts.9819a068", "../users", "a b", "", "x" * 129, 42],
    ids=["draft", "path", "space", "empty", "too-long", "number"],
)
async def test_malformed_author_ids_are_rejected_before_sanity(
    client: AsyncClient,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
    author_id: object,
) -> None:
    user = await make_user()

    response = await client.put(
        _link_url(user), json={"author_id": author_id}, headers=admin_headers
    )

    assert response.status_code == 422
    assert fake_sanity.queries == []


async def test_the_body_needs_the_field(
    client: AsyncClient, make_user: MakeUser, admin_headers: dict[str, str]
) -> None:
    user = await make_user()

    response = await client.put(_link_url(user), json={}, headers=admin_headers)

    assert response.status_code == 422


async def test_an_unknown_user_is_not_found(
    client: AsyncClient, fake_sanity: FakeSanity, admin_headers: dict[str, str]
) -> None:
    fake_sanity.query_handler = _authors(AMATA)

    response = await client.put(
        f"/api/v1/users/{uuid.uuid7()}/sanity-author",
        json={"author_id": AMATA["id"]},
        headers=admin_headers,
    )

    assert response.status_code == 404


@pytest.mark.parametrize("role", [Role.MEMBER, Role.SOCIAL_MEDIA_MANAGER])
async def test_only_admins_link_authors_even_for_themselves(
    client: AsyncClient, fake_sanity: FakeSanity, make_user: MakeUser, login_as: LoginAs, role: Role
) -> None:
    fake_sanity.query_handler = _authors(AMATA)
    user = await make_user(role=role)

    response = await client.put(
        _link_url(user), json={"author_id": AMATA["id"]}, headers=await login_as(user)
    )

    assert response.status_code == 403
    assert fake_sanity.queries == []


async def test_sanity_down_links_nothing(
    client: AsyncClient,
    db_session: AsyncSession,
    fake_sanity: FakeSanity,
    make_user: MakeUser,
    admin_headers: dict[str, str],
) -> None:
    fake_sanity.unavailable = True
    user = await make_user()

    response = await client.put(
        _link_url(user), json={"author_id": AMATA["id"]}, headers=admin_headers
    )

    assert response.status_code == 502
    assert await _stored_author(db_session, user.id) is None


async def test_authors_need_a_login(client: AsyncClient, make_user: MakeUser) -> None:
    user = await make_user()

    assert (await client.get(AUTHORS)).status_code == 401
    assert (await client.put(_link_url(user), json={"author_id": None})).status_code == 401
