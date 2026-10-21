from datetime import date

import pytest

from app.wishes.service import birthday_in, days_until_birthday, is_wishable, next_birthday

LEAP_DAY = date(1992, 2, 29)


@pytest.mark.parametrize(
    ("birthday", "on", "expected"),
    [
        (date(1990, 6, 30), date(2026, 6, 30), date(2026, 6, 30)),
        (date(1990, 6, 30), date(2026, 6, 1), date(2026, 6, 30)),
        (date(1990, 6, 30), date(2026, 7, 1), date(2027, 6, 30)),
        # Across New Year.
        (date(1990, 1, 5), date(2026, 12, 20), date(2027, 1, 5)),
        (date(1990, 12, 31), date(2026, 12, 31), date(2026, 12, 31)),
        # 29 February: on 28 February in years without one.
        (LEAP_DAY, date(2027, 2, 1), date(2027, 2, 28)),
        (LEAP_DAY, date(2027, 2, 28), date(2027, 2, 28)),
        (LEAP_DAY, date(2027, 3, 1), date(2028, 2, 29)),
        (LEAP_DAY, date(2028, 2, 28), date(2028, 2, 29)),
    ],
)
def test_next_birthday(birthday: date, on: date, expected: date) -> None:
    assert next_birthday(birthday, on) == expected


def test_a_leap_day_birthday_in_a_leap_year() -> None:
    assert birthday_in(LEAP_DAY, 2028) == date(2028, 2, 29)


@pytest.mark.parametrize(
    ("on", "days", "wishable"),
    [
        (date(2026, 6, 30), 0, True),
        (date(2026, 5, 31), 30, True),
        (date(2026, 5, 30), 31, False),
        # The day after: a whole year away again.
        (date(2026, 7, 1), 364, False),
    ],
)
def test_the_30_day_window(on: date, days: int, wishable: bool) -> None:
    birthday = date(1990, 6, 30)

    assert days_until_birthday(birthday, on) == days
    assert is_wishable(birthday, on) is wishable


def test_the_window_crosses_new_year() -> None:
    assert days_until_birthday(date(1990, 1, 10), date(2026, 12, 15)) == 26
    assert is_wishable(date(1990, 1, 10), date(2026, 12, 15))
