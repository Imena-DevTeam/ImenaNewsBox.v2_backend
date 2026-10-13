"""Application entry point: creates the FastAPI app and registers feature routers.

Run locally with `uv run fastapi dev src/app/main.py`. In production, uvicorn runs
`app.main:app` with `--proxy-headers`.
"""

from fastapi import FastAPI

app = FastAPI(title="Imena NewsBox API")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness check for UptimeRobot. Must stay cheap and never touch the database."""
    return {"status": "ok"}  
