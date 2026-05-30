"""
GET /health — service health endpoint.
Returns 200 when healthy, 503 when unhealthy.
Degraded (e.g. Redis down, stale feed) also returns 200 with status=degraded
so the service is still reachable but ops are alerted via the payload.
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.database import get_db
from app.health import get_health
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger("api.routers.health")


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service health check",
    description=(
        "Returns status of database, Redis, and per-store event feed freshness. "
        "HTTP 503 when the database is unreachable. HTTP 200 for all other states."
    ),
)
async def health_check(db=Depends(get_db)) -> JSONResponse:
    result = await get_health(db)

    status_code = 503 if result.status == "unhealthy" else 200
    return JSONResponse(content=result.model_dump(), status_code=status_code)
