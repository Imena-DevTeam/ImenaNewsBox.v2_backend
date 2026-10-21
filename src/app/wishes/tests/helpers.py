"""Shared helpers for the wishes tests."""

from collections.abc import Awaitable, Callable
from datetime import date, timedelta

from app.users.models import User
from app.wishes.service import today

MakeUser = Callable[..., Awaitable[User]]
LoginAs = Callable[[User], Awaitable[dict[str, str]]]
WISHES = "/api/v1/wishes"


def birthday_in(days: int) -> date:
    """A birthday (born 1992, a leap year, so any day exists) that comes `days` from today."""
    return (today() + timedelta(days=days)).replace(year=1992)


def year_of_birthday_in(days: int) -> int:
    """The year of the birthday a wish made today for `birthday_in(days)` is for."""
    return (today() + timedelta(days=days)).year
