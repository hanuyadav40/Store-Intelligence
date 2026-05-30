"""
Async SQLAlchemy database engine and session factory.

We use asyncpg for runtime (high throughput concurrent connections) and
psycopg2 for Alembic migrations (Alembic does not support async natively).
"""
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# Async engine — used by the FastAPI application at runtime
async_engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,          # Reconnect on stale connections
    pool_recycle=3600,           # Recycle connections every hour
    echo=settings.environment == "development",
)

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    expire_on_commit=False,      # Keep ORM objects accessible after commit
    class_=AsyncSession,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_db_connectivity() -> bool:
    """Returns True if the database is reachable."""
    from sqlalchemy import text

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
