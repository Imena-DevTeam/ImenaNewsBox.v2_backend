"""Shared helpers for the auth endpoint tests (fixtures live in `conftest.py`)."""

from collections.abc import Awaitable, Callable
from http.cookies import Morsel, SimpleCookie

from httpx import AsyncClient, Response

from app.auth.router import REFRESH_COOKIE

FRONTEND = "https://imena.example"
RequestCode = Callable[..., Awaitable[str]]
SignIn = Callable[..., Awaitable[str]]


async def post_with_cookie(
    client: AsyncClient, path: str, refresh_token: str | None, *, origin: str | None = FRONTEND
) -> Response:
    """POST the way the browser does on our site: the refresh cookie plus our Origin."""
    headers = {}
    if refresh_token is not None:
        headers["Cookie"] = f"{REFRESH_COOKIE}={refresh_token}"
    if origin is not None:
        headers["Origin"] = origin
    return await client.post(path, headers=headers)


def refresh_cookie(response: Response) -> Morsel[str] | None:
    """The refresh cookie set by a response, with its attributes, or None if it set none."""
    for header in response.headers.get_list("set-cookie"):
        cookie: SimpleCookie = SimpleCookie()
        cookie.load(header)
        if REFRESH_COOKIE in cookie:
            return cookie[REFRESH_COOKIE]
    return None
