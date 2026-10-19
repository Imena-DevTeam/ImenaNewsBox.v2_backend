"""Application entry point: builds the FastAPI app, its middleware and its routes.

Run locally with `uv run fastapi dev src/app/main.py`. In production, uvicorn runs
`app.main:app` with `--proxy-headers`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth.router import router as auth_router
from app.core.config import Settings, get_settings
from app.core.database.session import dispose_engine
from app.core.exceptions import NO_STORE, register_exception_handlers
from app.posts.router import router as posts_router
from app.users.router import router as users_router
from app.wishes.router import router as wishes_router


class NoStoreMiddleware:
    """Sends `Cache-Control: private, no-store` on every response.

    Browsers reach the API through Vercel's proxy, which must never cache member data. Setting
    it on every response (not only authenticated ones) means no route can forget it, and it
    replaces any value a route sets itself.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = NO_STORE
            await send(message)

        await self.app(scope, receive, send_with_header)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    is_production = settings.environment == "production"

    app = FastAPI(
        title="Imena NewsBox API",
        lifespan=lifespan,
        # There is no public API, so production doesn't publish its schema.
        openapi_url=None if is_production else "/openapi.json",
    )

    register_exception_handlers(app)

    # Added last, so it runs first: CORS answers preflight requests before anything else.
    app.add_middleware(NoStoreMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    api = APIRouter(prefix="/api/v1")
    for router in (auth_router, users_router, wishes_router, posts_router):
        api.include_router(router)
    app.include_router(api)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        """Liveness check for UptimeRobot. Must stay cheap and never touch the database."""
        return {"status": "ok"}

    return app


app = create_app()
