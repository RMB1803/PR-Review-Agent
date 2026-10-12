import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.webhook import router as webhook_router
from app.config import get_settings
from app.database.connection import close_db


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown."""
    logger.info("PR Review Agent starting up.")

    yield

    logger.info("PR Review Agent shutting down.")
    await close_db()


app = FastAPI(
    title="PR-Review-Agent",
    description="Automated AI Pull Request Code Review Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Check application health."""
    settings = get_settings()

    return JSONResponse(
        content={
            "status": "healthy",
            "app_env": settings.app_env,
            "version": "1.0.0",
        }
    )