"""
Store analytics routes:
  GET /stores/{store_id}/metrics
  GET /stores/{store_id}/funnel
  GET /stores/{store_id}/heatmap
  GET /stores/{store_id}/anomalies
"""
import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.anomalies import get_store_anomalies
from app.database import get_db
from app.funnel import get_store_funnel
from app.heatmap import get_store_heatmap
from app.metrics import get_store_metrics
from app.schemas import (
    AnomalyResponse,
    FunnelResponse,
    HeatmapResponse,
    StoreMetrics,
)

router = APIRouter(prefix="/stores", tags=["stores"])
logger = logging.getLogger("api.routers.stores")


@router.get(
    "/{store_id}/metrics",
    response_model=StoreMetrics,
    summary="Real-time store metrics",
    description=(
        "Returns current metrics for the given store: unique visitor count, "
        "conversion rate, avg dwell per zone, current billing queue depth, and "
        "basket abandonment rate."
    ),
)
async def store_metrics(
    store_id: str,
    target_date: Optional[date] = Query(
        None,
        description="Date in ISO format (YYYY-MM-DD). Defaults to today (UTC).",
    ),
    db=Depends(get_db),
) -> StoreMetrics:
    return await get_store_metrics(store_id, db, target_date)


@router.get(
    "/{store_id}/funnel",
    response_model=FunnelResponse,
    summary="Shopper conversion funnel",
    description=(
        "4-stage funnel: Entry → Zone Visit → Billing → Purchase. "
        "Each stage includes absolute count and drop-off percentage."
    ),
)
async def store_funnel(
    store_id: str,
    target_date: Optional[date] = Query(
        None,
        description="Date in ISO format (YYYY-MM-DD). Defaults to today (UTC).",
    ),
    db=Depends(get_db),
) -> FunnelResponse:
    return await get_store_funnel(store_id, db, target_date)


@router.get(
    "/{store_id}/heatmap",
    response_model=HeatmapResponse,
    summary="Zone visit heatmap",
    description=(
        "Visit frequency per zone, normalised to 0–100 across all zones. "
        "Used to render a floor-map heatmap widget."
    ),
)
async def store_heatmap(
    store_id: str,
    target_date: Optional[date] = Query(
        None,
        description="Date in ISO format (YYYY-MM-DD). Defaults to today (UTC).",
    ),
    db=Depends(get_db),
) -> HeatmapResponse:
    return await get_store_heatmap(store_id, db, target_date)


@router.get(
    "/{store_id}/anomalies",
    response_model=AnomalyResponse,
    summary="Active store anomalies",
    description=(
        "Detects three anomaly types: BILLING_QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE. "
        "Each anomaly carries a severity (INFO / WARN / CRITICAL) and suggested_action."
    ),
)
async def store_anomalies(
    store_id: str,
    target_date: Optional[date] = Query(
        None,
        description="Date in ISO format (YYYY-MM-DD). Defaults to today (UTC).",
    ),
    db=Depends(get_db),
) -> AnomalyResponse:
    return await get_store_anomalies(store_id, db, target_date)
