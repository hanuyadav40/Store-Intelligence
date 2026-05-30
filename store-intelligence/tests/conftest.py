# PROMPT: Generate async test fixtures for the store-intelligence FastAPI service.
# Uses an in-memory SQLite database for isolation (no external Postgres required).
# All fixtures are async and session-scoped where appropriate.
#
# CHANGES MADE:
# - Uses aiosqlite for async SQLite in-memory DB (avoids needing a running Postgres)
# - Overrides get_db dependency to use test session
# - Provides a pre-seeded test client via httpx.AsyncClient

import asyncio
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_db
from app.main import app
from app.models import Base

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncGenerator[AsyncSession, None]:
    TestSession = async_sessionmaker(db_engine, expire_on_commit=False)
    async with TestSession() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_engine) -> AsyncGenerator[AsyncClient, None]:
    TestSession = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        async with TestSession() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac

    app.dependency_overrides.clear()


def make_event(
    store_id: str = "STORE_TEST_001",
    camera_id: str = "CAM_TEST_001",
    visitor_id: str | None = None,
    event_type: str = "ENTRY",
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    queue_depth: int | None = None,
    confidence: float = 0.91,
) -> dict:
    metadata: dict = {}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id or str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": metadata,
    }
