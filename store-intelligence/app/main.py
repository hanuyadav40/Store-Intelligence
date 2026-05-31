"""
FastAPI application entry point.

Startup sequence:
  1. Configure structured logging
  2. Load Prometheus metrics
  3. Load buyer count from sales CSV
  4. Start background video / demo processor
  5. Serve API
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.services.state_manager import state_manager
from app.services.video_processor import orchestrator
from app.utils.config import settings
from app.utils.logging_config import get_logger, setup_logging
from analytics.metrics import load_buyers_from_csv

setup_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

if settings.ENABLE_PROMETHEUS:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server

    REQUEST_COUNT = Counter(
        "store_api_requests_total",
        "Total API requests",
        ["method", "endpoint", "status"],
    )
    REQUEST_LATENCY = Histogram(
        "store_api_request_latency_seconds",
        "API request latency",
        ["endpoint"],
    )
    FOOTFALL_GAUGE = Gauge("store_footfall_total", "Total footfall")
    OCCUPANCY_GAUGE = Gauge("store_current_occupancy", "Current in-store occupancy")


# ---------------------------------------------------------------------------
# Lifespan (replaces on_event startup/shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ---- startup ----
    logger.info("app_starting", version=settings.APP_VERSION)

    # Load sales data for conversion rate
    if settings.SALES_CSV_PATH:
        buyers = load_buyers_from_csv(settings.SALES_CSV_PATH)
        if buyers > 0:
            state_manager.set_buyer_count(buyers)

    # Start video / demo processor
    orchestrator.start()

    if settings.ENABLE_PROMETHEUS:
        try:
            start_http_server(settings.PROMETHEUS_PORT)
            logger.info("prometheus_started", port=settings.PROMETHEUS_PORT)
        except OSError:
            logger.warning("prometheus_port_in_use", port=settings.PROMETHEUS_PORT)

    logger.info("app_ready", host=settings.HOST, port=settings.PORT)
    yield

    # ---- shutdown ----
    orchestrator.stop()
    logger.info("app_shutdown")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Store Intelligence API – person detection, tracking & metrics",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- Global exception handler ----
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.error(
            "unhandled_exception",
            path=str(request.url),
            error=str(exc),
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error", "error": str(exc)},
        )

    # ---- Prometheus instrumentation middleware ----
    if settings.ENABLE_PROMETHEUS:

        @app.middleware("http")
        async def prometheus_middleware(request: Request, call_next):
            start = time.perf_counter()
            response = await call_next(request)
            elapsed = time.perf_counter() - start
            endpoint = request.url.path

            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=endpoint,
                status=response.status_code,
            ).inc()
            REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)

            # Keep gauges fresh
            snap = state_manager.get_metrics_snapshot()
            FOOTFALL_GAUGE.set(snap["footfall"])
            OCCUPANCY_GAUGE.set(snap["current_occupancy"])

            return response

    app.include_router(router)
    return app


app = create_app()
