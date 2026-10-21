from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User
from app.wishes.models import Wish
from app.wishes.service import today
from app.wishes.tests.helpers import (
    WISHES,
    LoginAs,
    MakeUser,
    birthday_in,
    year_of_birthday_in,
)

UPCOMING = f"{WISHES}/upcoming-birthdays"


def _members(body: list[dict[str, object]]) -> list[str]:
    return [item["member"]["id"] for item in body]  # type: ignore[index]


async def _only(client: AsyncClient, headers: dict[str, str], *users: User) -> list[str]:
    """The upcoming list, keeping only these users (other tests' rows are rolled back, but
    the shared database may hold users from elsewhere)."""
    response = await client.get(UPCOMING, headers=headers)
    assert response.status_code == 200, response.text
    wanted = {str(user.id) for user in users}
    return [member for member in _members(response.json()) if member in wanted]


async def test_members_with_a_birthday_in_the_next_30_days_soonest_first(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    in_ten = await make_user(birthday=birthday_in(10))
    today_ = await make_user(birthday=birthday_in(0))
    in_thirty = await make_user(birthday=birthday_in(30))
    too_far = await make_user(birthday=birthday_in(31))
    yesterday = await make_user(birthday=birthday_in(-1))
    no_birthday = await make_user(birthday=None)
    headers = await login_as(await make_user())

    members = await _only(
        client, headers, in_ten, today_, in_thirty, too_far, yesterday, no_birthday
    )

    assert members == [str(today_.id), str(in_ten.id), str(in_thirty.id)]


async def test_each_entry_has_the_date_without_the_birth_year(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    member = await make_user(birthday=birthday_in(5))

    response = await client.get(UPCOMING, headers=await login_as(await make_user()))

    [entry] = [item for item in response.json() if item["member"]["id"] == str(member.id)]
    assert entry["falls_on"] == (today() + timedelta(days=5)).isoformat()
    assert entry["days_until"] == 5
    assert entry["wished"] is False
    assert entry["member"]["birthday"] == {
        "month": member.birthday.month,  # type: ignore[union-attr]
        "day": member.birthday.day,  # type: ignore[union-attr]
    }
    assert "1992" not in response.text


async def test_it_shows_who_you_already_wished(
    client: AsyncClient, db_session: AsyncSession, make_user: MakeUser, login_as: LoginAs
) -> None:
    me = await make_user()
    wished, not_yet, last_year = [await make_user(birthday=birthday_in(3)) for _ in range(3)]
    year = year_of_birthday_in(3)
    db_session.add_all(
        [
            Wish(sender_id=me.id, recipient_id=wished.id, year=year, message="Hi"),
            Wish(sender_id=me.id, recipient_id=last_year.id, year=year - 1, message="Hi"),
            # Someone else's wish doesn't count as yours.
            Wish(sender_id=wished.id, recipient_id=not_yet.id, year=year, message="Hi"),
        ]
    )
    await db_session.flush()

    response = await client.get(UPCOMING, headers=await login_as(me))

    flags = {item["member"]["id"]: item["wished"] for item in response.json()}
    assert flags[str(wished.id)] is True
    assert flags[str(not_yet.id)] is False
    assert flags[str(last_year.id)] is False


async def test_deactivated_members_are_left_out(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    gone = await make_user(birthday=birthday_in(2), deactivated_at=datetime.now(UTC))

    assert await _only(client, await login_as(await make_user()), gone) == []


async def test_your_own_birthday_is_included(
    client: AsyncClient, make_user: MakeUser, login_as: LoginAs
) -> None:
    me = await make_user(birthday=birthday_in(4))

    assert await _only(client, await login_as(me), me) == [str(me.id)]


async def test_upcoming_birthdays_need_a_login(client: AsyncClient) -> None:
    assert (await client.get(UPCOMING)).status_code == 401
