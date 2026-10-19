from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from app.core.exceptions import (
    AppError,
    BadRequest,
    Conflict,
    Forbidden,
    NotFound,
    TooManyRequests,
    Unauthorized,
)
from app.main import create_app


class WishTaken(Conflict):
    """A feature exception: subclasses a common one and only overrides the code and message."""

    code = "wish_already_sent"
    default_message = "You already wished this member a happy birthday this year."


class Body(BaseModel):
    family_name: str
    code: str


def _app_with_test_routes() -> FastAPI:
    app = create_app()

    @app.get("/raise/{name}")
    async def raise_error(name: str) -> None:
        errors: dict[str, AppError] = {
            "bad_request": BadRequest(),
            "unauthorized": Unauthorized(),
            "forbidden": Forbidden(),
            "not_found": NotFound("No member with that id."),
            "conflict": Conflict(),
            "too_many_requests": TooManyRequests(),
            "feature": WishTaken(),
        }
        raise errors[name]

    @app.get("/crash")
    async def crash() -> None:
        raise RuntimeError("secret detail that must not reach the client")

    @app.post("/validate")
    async def validate(body: Body) -> None:
        return None

    return app


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    # raise_app_exceptions=False: Starlette re-raises unhandled errors for the server to log,
    # and the test should see the 500 response instead.
    transport = ASGITransport(app=_app_with_test_routes(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.parametrize(
    ("name", "status", "code"),
    [
        ("bad_request", 400, "bad_request"),
        ("unauthorized", 401, "unauthorized"),
        ("forbidden", 403, "forbidden"),
        ("not_found", 404, "not_found"),
        ("conflict", 409, "conflict"),
        ("too_many_requests", 429, "too_many_requests"),
        ("feature", 409, "wish_already_sent"),
    ],
)
async def test_app_errors_map_to_status_and_code(
    http: AsyncClient, name: str, status: int, code: str
) -> None:
    response = await http.get(f"/raise/{name}")

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


async def test_message_can_be_overridden_per_raise(http: AsyncClient) -> None:
    response = await http.get("/raise/not_found")

    assert response.json()["error"]["message"] == "No member with that id."


async def test_unauthorized_sends_bearer_challenge(http: AsyncClient) -> None:
    response = await http.get("/raise/unauthorized")

    assert response.headers["www-authenticate"] == "Bearer"


async def test_unknown_route_uses_the_error_shape(http: AsyncClient) -> None:
    response = await http.get("/no-such-route")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_wrong_method_uses_the_error_shape(http: AsyncClient) -> None:
    response = await http.delete("/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


async def test_validation_errors_list_fields_without_echoing_input(http: AsyncClient) -> None:
    response = await http.post("/validate", json={"family_name": 42, "extra": "123456"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    fields = {detail["field"] for detail in error["details"]}
    assert fields == {"body.family_name", "body.code"}
    assert "123456" not in response.text
    assert "input" not in response.text


async def test_unexpected_errors_return_a_generic_500(http: AsyncClient) -> None:
    response = await http.get("/crash")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Something went wrong."}
    }
    assert "secret detail" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
