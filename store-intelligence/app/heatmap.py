"""
Heatmap computation.

Returns zone visit frequency + avg dwell, normalised 0–100 across all zones
for the store. The normalisation ensures a grid heatmap widget can render
directly without additional client-side math.

data_confidence is False when fewer than 20 sessions exist in the window
(per challenge spec) — the caller should show a low-confidence indicator.
"""
import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, VisitorSession
from app.schemas import HeatmapResponse, HeatmapZone

logger = logging.getLogger("api.heatmap")


async def get_store_heatmap(
    store_id: str,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> HeatmapResponse:
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    # Zone visit frequency and average dwell (from ZONE_ENTER + ZONE_DWELL events)
    freq_q = await db.execute(
        select(
            Event.zone_id,
            Event.sku_zone,
            func.count(Event.id).label("visit_count"),
            func.coalesce(func.avg(Event.dwell_ms), 0).label("avg_dwell"),
        )
        .where(
            Event.store_id == store_id,
            Event.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
            Event.zone_id.isnot(None),
        )
        .group_by(Event.zone_id, Event.sku_zone)
    )
    rows = freq_q.all()

    # Session count for data_confidence flag
    session_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    session_count: int = session_q.scalar() or 0
    data_confidence = session_count >= 20

    if not rows:
        return HeatmapResponse(
            store_id=store_id,
            date=target_date.isoformat(),
            zones=[],
            data_confidence=data_confidence,
        )

    # Normalise visit_count to 0–100
    max_visits = max(row.visit_count for row in rows) or 1

    zones = [
        HeatmapZone(
            zone_id=row.zone_id,
            sku_zone=row.sku_zone,
            visit_frequency=row.visit_count,
            avg_dwell_ms=round(float(row.avg_dwell), 1),
            normalised_score=round(row.visit_count / max_visits * 100, 1),
        )
        for row in rows
    ]

    # Sort descending by score for easy rendering
    zones.sort(key=lambda z: z.normalised_score, reverse=True)

    return HeatmapResponse(
        store_id=store_id,
        date=target_date.isoformat(),
        zones=zones,
        data_confidence=data_confidence,
    )
