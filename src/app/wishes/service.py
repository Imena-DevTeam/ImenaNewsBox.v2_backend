"""Business logic and data access."""

import calendar
from datetime import UTC, date, datetime
from typing import Final

# Wishes are open, and "upcoming birthdays" look ahead, from the birthday itself to this many
# days before it.
WISH_WINDOW_DAYS: Final = 30


# --- Birthday dates ---


def today() -> date:
    """Today's date in UTC, the day every birthday rule uses."""
    return datetime.now(UTC).date()


def birthday_in(birthday: date, year: int) -> date:
    """The date the birthday falls on in `year`. 29 February is kept on 28 February in years
    without one."""
    if (birthday.month, birthday.day) == (2, 29) and not calendar.isleap(year):
        return date(year, 2, 28)
    return birthday.replace(year=year)


def next_birthday(birthday: date, on: date) -> date:
    """The birthday's next date from `on`: `on` itself when it's the day."""
    this_year = birthday_in(birthday, on.year)
    return this_year if this_year >= on else birthday_in(birthday, on.year + 1)


def days_until_birthday(birthday: date, on: date) -> int:
    return (next_birthday(birthday, on) - on).days


def is_wishable(birthday: date | None, on: date) -> bool:
    """Whether the birthday is today or within the next `WISH_WINDOW_DAYS` days."""
    return birthday is not None and days_until_birthday(birthday, on) <= WISH_WINDOW_DAYS
