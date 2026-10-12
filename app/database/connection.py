from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


# Base class for all SQLAlchemy models.
class Base(DeclarativeBase):
    pass


# Database engine and connection pool.
settings = get_settings()

database_url = settings.tiger_database_url.replace(
    "postgres://", "postgresql+asyncpg://", 1
).replace("sslmode=require", "ssl=require")


engine = create_async_engine(
    database_url,
    pool_pre_ping=True,
    echo=False,
)

# Creates a new database session when called.
SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# FastAPI dependency: provides a session for each request.
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


# Used by background workers and other application code.
def get_session_factory() -> async_sessionmaker:
    return SessionLocal


# Close the connection pool when the application shuts down.
async def close_db() -> None:
    await engine.dispose()