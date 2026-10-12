"""Application entry point: creates the FastAPI app and registers feature routers."""

from fastapi import FastAPI
import uvicorn
from app.core.config import get_settings





app = FastAPI()


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness check for UptimeRobot. Must stay cheap and never touch the database."""
    return {"status": "ok"}




config = get_settings()

if __name__ == "__main__":
    uvicorn.run("src.app.main:app", host=config.app_host, port=config.app_port, reload=True, log_level=config.log_level)

    
