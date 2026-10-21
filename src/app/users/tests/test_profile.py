from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.users.models import Role, User

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
ME = "/api/v1/users/me"


async def test_you_change_your_display_name_and_birthday(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(display_name="Old Name")
    headers = await login_as(user)

    response = await client.patch(
        ME, json={"display_name": "  New Name ", "birthday": "1993-06-30"}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["display_name"], body["birthday"]) == ("New Name", "1993-06-30")
    # Everyone else sees the change in the directory, without the year.
    public = await client.get(f"/api/v1/users/{user.id}", headers=headers)
    assert public.json()["display_name"] == "New Name"
    assert public.json()["birthday"] == {"month": 6, "day": 30}


async def test_only_the_fields_sent_change(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(display_name="Kept", birthday=date(1990, 1, 1))

    response = await client.patch(ME, json={"birthday": "1990-01-02"}, headers=await login_as(user))

    assert (response.json()["display_name"], response.json()["birthday"]) == (
        "Kept",
        "1990-01-02",
    )


async def test_your_birthday_can_be_cleared(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    user = await make_user(birthday=date(1990, 1, 1))

    response = await client.patch(ME, json={"birthday": None}, headers=await login_as(user))

    assert response.json()["birthday"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"family_name": "Someone Else"},
        {"email": "me@example.com"},
        {"role": "admin"},
        {"sub_family_id": 1},
        {"promotion_id": None},
    ],
    ids=["family-name", "email", "role", "sub-family", "promotion"],
)
async def test_admin_only_fields_are_refused(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, body: dict[str, Any]
) -> None:
    user = await make_user()

    response = await client.patch(ME, json=body, headers=await login_as(user))

    assert response.status_code == 422
    assert user.role is Role.MEMBER


@pytest.mark.parametrize(
    "body",
    [
        {"display_name": None},
        {"display_name": "   "},
        {"display_name": "x" * 101},
        {"birthday": (date.today() + timedelta(days=1)).isoformat()},
        {"birthday": "not-a-date"},
    ],
    ids=["null-name", "blank-name", "long-name", "future-birthday", "bad-date"],
)
async def test_invalid_changes_are_rejected(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs, body: dict[str, Any]
) -> None:
    response = await client.patch(ME, json=body, headers=await login_as(await make_user()))

    assert response.status_code == 422


async def test_changing_your_profile_needs_a_login(client: AsyncClient) -> None:
    assert (await client.patch(ME, json={"display_name": "Anyone"})).status_code == 401
