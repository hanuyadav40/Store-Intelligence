"""
FastAPI route handlers.

Endpoints:
  GET  /health      – liveness probe
  GET  /metrics     – store business metrics
  GET  /funnel      – customer journey funnel
  GET  /events      – event stream (last N events)
  GET  /anomalies   – detected anomalies
  POST /process     – trigger processing of an uploaded video (optional)
"""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional

from fastapi import APIRouter, Query, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from app.models.event_model import PersonEvent
from app.models.metric_model import (
    MetricsResponse,
    FunnelResponse,
    AnomalyAlert,
    HealthResponse,
)
from app.services.state_manager import state_manager
from app.utils.config import settings
from analytics.metrics import compute_metrics
from analytics.funnel import compute_funnel
from analytics.anomalies import run_all_checks

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=settings.APP_VERSION,
        uptime_seconds=round(state_manager.uptime_seconds, 2),
        demo_mode=settings.DEMO_MODE,
        events_processed=state_manager.total_events,
    )


@router.get("/metrics", response_model=MetricsResponse, tags=["analytics"])
async def get_metrics() -> MetricsResponse:
    """Return current store business metrics."""
    return compute_metrics()


@router.get("/funnel", response_model=FunnelResponse, tags=["analytics"])
async def get_funnel() -> FunnelResponse:
    """Return customer journey funnel (entered → engaged → converted)."""
    return compute_funnel()


@router.get("/events", response_model=List[PersonEvent], tags=["events"])
async def get_events(
    limit: int = Query(default=100, ge=1, le=1000, description="Max events to return"),
    event_type: Optional[str] = Query(default=None, description="Filter by event type"),
) -> List[PersonEvent]:
    """Return the most recent events, newest last."""
    events = state_manager.get_events(limit=limit)
    if event_type:
        upper = event_type.upper()
        events = [e for e in events if e.event_type.value == upper]
    return events


@router.get("/anomalies", response_model=List[AnomalyAlert], tags=["analytics"])
async def get_anomalies(
    run_check: bool = Query(default=False, description="Run live anomaly check first"),
) -> List[AnomalyAlert]:
    """Return detected anomalies. Optionally trigger a fresh check."""
    if run_check:
        run_all_checks()
    return state_manager.get_anomalies()


@router.post("/process", tags=["pipeline"])
async def process_video(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="Video file to process"),
) -> JSONResponse:
    """
    Accept a video upload and process it asynchronously.
    The file is saved to a temp path and processed via VideoFileProcessor.
    """
    allowed_ext = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    _, ext = os.path.splitext(file.filename or "")
    if ext.lower() not in allowed_ext:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext}'. Allowed: {allowed_ext}",
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    from app.services.video_processor import VideoFileProcessor

    proc = VideoFileProcessor(tmp_path)
    background_tasks.add_task(_run_and_cleanup, proc, tmp_path)

    return JSONResponse(
        status_code=202,
        content={"message": "Processing started", "filename": file.filename},
    )


async def _run_and_cleanup(proc, path: str) -> None:
    try:
        proc.start()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
