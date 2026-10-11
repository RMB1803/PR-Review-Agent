# app/main.py
"""
FastAPI application entry point.
"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.webhook import router as webhook_router
from app.config import get_settings
from app.database.connection import init_db, init_tiger_schema

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: database initialization and cleanup."""
    logger.info("Application starting up...")
    try:
        await init_db()
        logger.info("Database tables initialized.")
    except Exception as exc:
        logger.warning("PostgreSQL init_db skipped/failed: %s", exc)

    try:
        await init_tiger_schema()
        logger.info("Tiger Cloud schema initialized.")
    except Exception as exc:
        logger.warning("Tiger Cloud schema init skipped/failed: %s", exc)

    yield

    logger.info("Application shutting down...")


app = FastAPI(
    title="PR-Review-Agent",
    description="Automated AI Pull Request Code Review Agent",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(webhook_router)


@app.get("/health", tags=["system"])
async def health_check():
    """Health check endpoint."""
    cfg = get_settings()
    return JSONResponse(
        content={
            "status": "healthy",
            "app_env": cfg.app_env,
            "version": "1.0.0",
        }
    )
