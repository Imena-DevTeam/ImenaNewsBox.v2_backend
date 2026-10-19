from collections.abc import AsyncIterator

import pytest
from fastapi import Depends
from httpx import ASGITransport, AsyncClient

from app.auth.dependencies import require_allowed_origin
from app.core.config import get_settings
from app.main import create_app

FRONTEND = "https://imena.example"


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    app = create_app()

    @app.post("/cookie-route", dependencies=[Depends(require_allowed_origin)])
    async def cookie_route() -> dict[str, str]:
        return {"status": "ok"}

    settings = get_settings().model_copy(
        update={"cors_origins": [f"{FRONTEND}/", "http://localhost:3000"]}
    )
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("origin", [FRONTEND, "http://localhost:3000"])
async def test_allowed_origins_pass(http: AsyncClient, origin: str) -> None:
    response = await http.post("/cookie-route", headers={"Origin": origin})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": f"{FRONTEND}.evil.example"},
        {"Origin": FRONTEND.replace("https", "http")},
    ],
    ids=["missing", "other-site", "null", "lookalike", "wrong-scheme"],
)
async def test_other_origins_are_refused(http: AsyncClient, headers: dict[str, str]) -> None:
    response = await http.post("/cookie-route", headers=headers)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "origin_not_allowed"
