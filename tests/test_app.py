"""App-level wiring: middleware, CORS, routes and settings checks."""

from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI, Response
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.main import create_app

FRONTEND = "http://frontend.test"


def _settings(**overrides: object) -> Settings:
    return get_settings().model_copy(update={"cors_origins": [FRONTEND], **overrides})


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    async with await _client(create_app(_settings())) as client:
        yield client


async def test_every_response_is_private_and_not_stored(http: AsyncClient) -> None:
    for path in ("/health", "/api/v1/users/does-not-exist", "/openapi.json"):
        response = await http.get(path)
        assert response.headers["cache-control"] == "private, no-store", path


async def test_no_store_replaces_a_cache_header_set_by_a_route() -> None:
    app = create_app(_settings())

    @app.get("/sets-public")
    async def sets_public(response: Response) -> dict[str, str]:
        response.headers["Cache-Control"] = "public, s-maxage=600"
        return {}

    async with await _client(app) as client:
        response = await client.get("/sets-public")

    assert response.headers.get_list("cache-control") == ["private, no-store"]


async def test_cors_allows_the_configured_origin_with_credentials(http: AsyncClient) -> None:
    response = await http.options(
        "/api/v1/auth/refresh",
        headers={"Origin": FRONTEND, "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    assert response.headers["access-control-allow-credentials"] == "true"


async def test_cors_rejects_other_origins(http: AsyncClient) -> None:
    response = await http.options(
        "/api/v1/auth/refresh",
        headers={"Origin": "http://evil.test", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


async def test_feature_routers_are_mounted_under_api_v1(http: AsyncClient) -> None:
    schema = (await http.get("/openapi.json")).json()

    assert schema["info"]["title"] == "Imena NewsBox API"
    # The routers are still empty; this checks the prefix once routes exist.
    assert all(path == "/health" or path.startswith("/api/v1/") for path in schema["paths"])


async def test_production_hides_the_openapi_schema_and_docs() -> None:
    app = create_app(_settings(environment="production"))

    async with await _client(app) as client:
        for path in ("/openapi.json", "/docs", "/redoc"):
            assert (await client.get(path)).status_code == 404, path
        assert (await client.get("/health")).status_code == 200


def test_settings_reject_a_wildcard_cors_origin() -> None:
    with pytest.raises(ValidationError, match="never '\\*'"):
        Settings(cors_origins="http://a.test,*")  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["access_token_secret", "refresh_token_secret", "code_secret"])
def test_production_requires_long_secrets(name: str) -> None:
    with pytest.raises(ValidationError, match=f"{name} must be at least 32 characters"):
        Settings(environment="production", **{name: "too-short"})  # type: ignore[arg-type]
