"""Shared helpers for the auth endpoint tests (fixtures live in `conftest.py`)."""

from collections.abc import Awaitable, Callable
from http.cookies import Morsel, SimpleCookie

from httpx import Response

from app.auth.router import REFRESH_COOKIE

RequestCode = Callable[..., Awaitable[str]]


def refresh_cookie(response: Response) -> Morsel[str] | None:
    """The refresh cookie set by a response, with its attributes, or None if it set none."""
    for header in response.headers.get_list("set-cookie"):
        cookie: SimpleCookie = SimpleCookie()
        cookie.load(header)
        if REFRESH_COOKIE in cookie:
            return cookie[REFRESH_COOKIE]
    return None
