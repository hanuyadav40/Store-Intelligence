"""
Health check logic.

Checks:
  - Database connectivity
  - Redis connectivity
  - Per-store feed staleness (last event timestamp)

A store feed is STALE if the last ingested event is > STALE_FEED_THRESHOLD_MINUTES old.
This is what an on-call engineer checks first when alerted.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Event
from app.schemas import HealthResponse, StoreHealthStatus

logger = logging.getLogger("api.health")
settings = get_settings()

# Module-level start time for uptime tracking
_START_TIME = time.monotonic()
_VERSION = "1.0.0"


async def get_health(db: AsyncSession) -> HealthResponse:
    now = datetime.now(timezone.utc)

    # Database check
    db_ok = await _check_database(db)

    # Redis check
    redis_ok = await _check_redis()

    # Per-store feed status
    store_statuses = await _get_store_statuses(db, now)

    overall_status = "healthy"
    if not db_ok:
        overall_status = "unhealthy"
    elif not redis_ok or any(s.status != "OK" for s in store_statuses):
        overall_status = "degraded"

    return HealthResponse(
        status=overall_status,
        version=_VERSION,
        environment=settings.environment,
        database="ok" if db_ok else "error",
        redis="ok" if redis_ok else "error",
        stores=store_statuses,
        uptime_seconds=round(time.monotonic() - _START_TIME, 1),
        as_of=now.isoformat(),
    )


async def _check_database(db: AsyncSession) -> bool:
    try:
        await db.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("Database health check failed", extra={"error": str(exc)})
        return False


async def _check_redis() -> bool:
    from app.config import get_settings

    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.redis_url, socket_connect_timeout=2)
        await r.ping()
        await r.aclose()
        return True
    except Exception as exc:
        logger.warning("Redis health check failed", extra={"error": str(exc)})
        return False


async def _get_store_statuses(
    db: AsyncSession, now: datetime
) -> list[StoreHealthStatus]:
    """Return last event timestamp for each store and flag stale feeds."""
    stale_cutoff = now - timedelta(minutes=settings.stale_feed_threshold_minutes)

    # Get last event per store
    q = await db.execute(
        select(Event.store_id, func.max(Event.created_at).label("last_event_at"))
        .group_by(Event.store_id)
    )
    rows = q.all()

    statuses: list[StoreHealthStatus] = []
    for row in rows:
        last_at: datetime = row.last_event_at
        if last_at.tzinfo is None:
            last_at = last_at.replace(tzinfo=timezone.utc)

        lag = (now - last_at).total_seconds()

        if last_at < stale_cutoff:
            feed_status = "STALE_FEED"
        else:
            feed_status = "OK"

        statuses.append(
            StoreHealthStatus(
                store_id=row.store_id,
                last_event_at=last_at.isoformat(),
                lag_seconds=round(lag, 1),
                status=feed_status,
            )
        )

    return statuses
