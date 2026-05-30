"""
Lightweight dashboard service.

Serves the static HTML/JS dashboard and proxies the API metrics endpoints
so the browser only talks to one origin (avoids CORS issues in Docker).

Endpoints:
  GET /                           → HTML dashboard (index.html)
  GET /metrics/{store_id}         → proxied from API /stores/{store_id}/metrics
  GET /funnel/{store_id}          → proxied from API /stores/{store_id}/funnel
  GET /heatmap/{store_id}         → proxied from API /stores/{store_id}/heatmap
  GET /anomalies/{store_id}       → proxied from API /stores/{store_id}/anomalies
  GET /stream/{store_id}          → SSE pass-through from API /dashboard/stream/{store_id}
"""
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("dashboard")

API_BASE_URL = os.getenv("API_BASE_URL", "http://api:8000")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Store Intelligence Dashboard", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard_home():
    index = STATIC_DIR / "index.html"
    return HTMLResponse(content=index.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# API proxy helpers
# ---------------------------------------------------------------------------
async def _proxy_get(path: str) -> JSONResponse:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{API_BASE_URL}{path}")
    return JSONResponse(content=resp.json(), status_code=resp.status_code)


@app.get("/metrics/{store_id}")
async def proxy_metrics(store_id: str):
    return await _proxy_get(f"/stores/{store_id}/metrics")


@app.get("/funnel/{store_id}")
async def proxy_funnel(store_id: str):
    return await _proxy_get(f"/stores/{store_id}/funnel")


@app.get("/heatmap/{store_id}")
async def proxy_heatmap(store_id: str):
    return await _proxy_get(f"/stores/{store_id}/heatmap")


@app.get("/anomalies/{store_id}")
async def proxy_anomalies(store_id: str):
    return await _proxy_get(f"/stores/{store_id}/anomalies")


# ---------------------------------------------------------------------------
# SSE pass-through
# ---------------------------------------------------------------------------
@app.get("/stream/{store_id}")
async def stream_proxy(store_id: str):
    """
    Pass-through SSE stream from the API.
    The dashboard's JavaScript connects to /stream/{store_id} and receives
    real-time metrics ticks via Server-Sent Events.
    """
    async def _event_generator():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", f"{API_BASE_URL}/dashboard/stream/{store_id}"
            ) as response:
                async for chunk in response.aiter_text():
                    yield chunk

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
