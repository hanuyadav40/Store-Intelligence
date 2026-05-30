"""
FastAPI application entry point.

Startup sequence:
  1. configure_logging() — JSON logs before anything else
  2. load_pos_data()     — idempotent CSV load into DB (runs every startup)
  3. Routes mounted     — /events, /stores, /health, /dashboard/stream

SSE endpoint /dashboard/stream/{store_id} emits a "tick" every 15 seconds
with the latest store metrics so the live dashboard can update without polling.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sse_starlette.sse import EventSourceResponse

from app.config import get_settings
from app.database import AsyncSessionLocal, async_engine as engine
from app.ingestion import load_pos_data
from app.logging_config import configure_logging
from app.middleware import RequestLoggingMiddleware
from app.models import Base
from app.routers import events, health, stores

configure_logging()
logger = logging.getLogger("api.main")
settings = get_settings()

# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting store-intelligence API", extra={"env": settings.environment})

    # Ensure tables exist (Alembic is preferred, but this is a safe fallback)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    # Load POS transaction data (idempotent — skips already-loaded rows)
    async with AsyncSessionLocal() as session:
        await load_pos_data(settings.pos_data_path, session)

    logger.info("Startup complete — API is ready")
    yield
    logger.info("Shutting down store-intelligence API")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Store Intelligence API",
    description=(
        "Retail analytics API for the Purplle/Apex store intelligence system. "
        "Ingests structured events from CCTV detection pipeline and exposes "
        "metrics, funnel, heatmap, anomaly, and health endpoints."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS — permits dashboard (same origin in Docker) and local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(events.router)
app.include_router(stores.router)
app.include_router(health.router)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------
@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# SSE live dashboard stream
# ---------------------------------------------------------------------------
async def _metrics_stream(store_id: str) -> AsyncGenerator[dict, None]:
    """
    Yields a JSON payload every 15 seconds with the latest store metrics.
    Clients reconnect automatically on disconnect (SSE spec).
    """
    from app.metrics import get_store_metrics

    while True:
        try:
            async with AsyncSessionLocal() as db:
                metrics = await get_store_metrics(store_id, db)
                payload = metrics.model_dump()
                payload["_event"] = "metrics"
                yield {"data": json.dumps(payload)}
        except Exception as exc:
            logger.warning(
                "SSE metrics error",
                extra={"store_id": store_id, "error": str(exc)},
            )
            yield {
                "data": json.dumps(
                    {
                        "_event": "error",
                        "store_id": store_id,
                        "message": "metrics unavailable",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                )
            }

        await asyncio.sleep(15)


@app.get(
    "/dashboard/stream/{store_id}",
    summary="Live metrics stream (SSE)",
    description="Server-Sent Events stream. Emits a metrics tick every 15 s.",
    tags=["dashboard"],
)
async def dashboard_stream(store_id: str, request: Request):
    return EventSourceResponse(_metrics_stream(store_id))
