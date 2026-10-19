from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.pagination import MAX_LIMIT, Page, PageParams, Pagination
from app.main import create_app

NAMES = [f"member-{n}" for n in range(1, 46)]


def _app_with_list_route() -> FastAPI:
    app = create_app()

    @app.get("/names", response_model=Page[str])
    async def list_names(params: Pagination) -> Page[str]:
        items = NAMES[params.offset : params.offset + params.limit]
        return Page.build(items, params=params, total=len(NAMES))

    return app


@pytest.fixture
async def http() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_app_with_list_route())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def test_offset_is_zero_based_from_the_page() -> None:
    assert PageParams().offset == 0
    assert PageParams(page=3, limit=10).offset == 20


async def test_defaults_to_the_first_page(http: AsyncClient) -> None:
    response = await http.get("/names")

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 1
    assert body["limit"] == 20
    assert body["total"] == 45
    assert body["items"] == NAMES[:20]


async def test_returns_the_requested_page(http: AsyncClient) -> None:
    response = await http.get("/names", params={"page": 3, "limit": 20})

    body = response.json()
    assert body["items"] == NAMES[40:]
    assert body["total"] == 45


@pytest.mark.parametrize(
    "params",
    [{"limit": MAX_LIMIT + 1}, {"limit": 0}, {"page": 0}, {"page": "first"}],
)
async def test_rejects_out_of_range_values(http: AsyncClient, params: dict[str, int | str]) -> None:
    response = await http.get("/names", params=params)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
