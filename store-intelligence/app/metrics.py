"""
Real-time store metrics computation.

All queries run against the live events and visitor_sessions tables —
nothing is pre-aggregated or cached. Metrics are always fresh.

Design: We exclude is_staff=True from all customer-facing metrics.
Zero-traffic periods return zeros rather than null to simplify dashboard rendering.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, POSTransaction, VisitorSession
from app.schemas import StoreMetrics, ZoneDwellStats

logger = logging.getLogger("api.metrics")


async def get_store_metrics(
    store_id: str,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> StoreMetrics:
    """
    Compute all real-time metrics for a store on a given date (defaults to today UTC).
    """
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # Unique customer visitors (ENTRY events, non-staff, distinct visitor_id)
    # ------------------------------------------------------------------
    uv_q = await db.execute(
        select(func.count(func.distinct(Event.visitor_id))).where(
            Event.store_id == store_id,
            Event.event_type == "ENTRY",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
        )
    )
    unique_visitors: int = uv_q.scalar() or 0

    # ------------------------------------------------------------------
    # Conversion rate — sessions marked is_converted=True / total non-staff sessions
    # ------------------------------------------------------------------
    total_sessions_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    total_sessions: int = total_sessions_q.scalar() or 0

    converted_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.is_converted.is_(True),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    converted: int = converted_q.scalar() or 0
    conversion_rate = round(converted / total_sessions, 4) if total_sessions > 0 else 0.0

    # ------------------------------------------------------------------
    # Average dwell per zone (from ZONE_DWELL events, non-staff)
    # ------------------------------------------------------------------
    dwell_q = await db.execute(
        select(
            Event.zone_id,
            func.avg(Event.dwell_ms).label("avg_dwell"),
            func.count(Event.id).label("visit_count"),
        )
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_DWELL",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
            Event.zone_id.isnot(None),
        )
        .group_by(Event.zone_id)
    )
    avg_dwell_per_zone = [
        ZoneDwellStats(
            zone_id=row.zone_id,
            avg_dwell_ms=round(float(row.avg_dwell), 1),
            visit_count=row.visit_count,
        )
        for row in dwell_q
    ]

    # ------------------------------------------------------------------
    # Current queue depth — max queue_depth in the last 10 minutes
    # ------------------------------------------------------------------
    queue_cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
    queue_q = await db.execute(
        select(func.max(Event.queue_depth)).where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.timestamp >= queue_cutoff,
        )
    )
    current_queue_depth: int = queue_q.scalar() or 0

    # ------------------------------------------------------------------
    # Abandonment rate — BILLING_QUEUE_ABANDON / BILLING_QUEUE_JOIN
    # ------------------------------------------------------------------
    join_q = await db.execute(
        select(func.count()).where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
        )
    )
    join_count: int = join_q.scalar() or 0

    abandon_q = await db.execute(
        select(func.count()).where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_ABANDON",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
        )
    )
    abandon_count: int = abandon_q.scalar() or 0
    abandonment_rate = round(abandon_count / join_count, 4) if join_count > 0 else 0.0

    # ------------------------------------------------------------------
    # Total POS transactions today
    # ------------------------------------------------------------------
    pos_q = await db.execute(
        select(func.count(POSTransaction.id)).where(
            POSTransaction.store_id == store_id,
            POSTransaction.timestamp.between(day_start, day_end),
        )
    )
    total_transactions: int = pos_q.scalar() or 0

    return StoreMetrics(
        store_id=store_id,
        date=target_date.isoformat(),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone=avg_dwell_per_zone,
        current_queue_depth=current_queue_depth,
        abandonment_rate=abandonment_rate,
        total_transactions=total_transactions,
        as_of=datetime.now(timezone.utc).isoformat(),
    )
