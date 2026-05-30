"""
Conversion funnel computation.

The funnel is session-based, not event-based. Each stage counts unique
visitor_ids (not raw events) to avoid inflating numbers from ZONE_DWELL
repeat emissions or re-entry within the same session.

Funnel stages:
  1. ENTRY       — unique visitor_ids with an ENTRY event today (non-staff)
  2. ZONE_VISIT  — subset who also had a ZONE_ENTER in any non-billing zone
  3. BILLING     — subset who entered the billing zone (BILLING_QUEUE_JOIN or ZONE_ENTER billing)
  4. PURCHASE    — subset whose session is marked is_converted=True

Drop-off % at stage N = (Stage N-1 - Stage N) / Stage N-1 × 100
"""
import logging
from datetime import date, datetime, time, timezone
from typing import Optional

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Event, VisitorSession
from app.schemas import FunnelResponse, FunnelStage

logger = logging.getLogger("api.funnel")

_FUNNEL_STAGE_DEFS = [
    ("ENTRY", "Store Entry"),
    ("ZONE_VISIT", "Product Zone Visit"),
    ("BILLING", "Billing Queue Entry"),
    ("PURCHASE", "Completed Purchase"),
]


async def get_store_funnel(
    store_id: str,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> FunnelResponse:
    if target_date is None:
        target_date = datetime.now(timezone.utc).date()

    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    # Stage 1 — unique non-staff visitors who entered the store
    entry_visitors_q = await db.execute(
        select(func.count(distinct(Event.visitor_id))).where(
            Event.store_id == store_id,
            Event.event_type == "ENTRY",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
        )
    )
    stage1_count: int = entry_visitors_q.scalar() or 0

    # Stage 2 — visitors who also visited at least one product zone
    # (ZONE_ENTER on any zone that is NOT 'BILLING' and NOT 'BILLING_QUEUE')
    zone_visitors_q = await db.execute(
        select(func.count(distinct(Event.visitor_id))).where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
            ~Event.zone_id.in_(("BILLING", "BILLING_QUEUE", "ENTRY_ZONE")),
        )
    )
    stage2_count: int = zone_visitors_q.scalar() or 0
    # Stage 2 can't exceed Stage 1
    stage2_count = min(stage2_count, stage1_count)

    # Stage 3 — visitors who entered the billing zone
    billing_visitors_q = await db.execute(
        select(func.count(distinct(Event.visitor_id))).where(
            Event.store_id == store_id,
            Event.event_type.in_(["BILLING_QUEUE_JOIN", "ZONE_ENTER"]),
            Event.is_staff.is_(False),
            Event.timestamp.between(day_start, day_end),
            Event.zone_id.in_(("BILLING", "BILLING_QUEUE")),
        )
    )
    stage3_count: int = billing_visitors_q.scalar() or 0
    stage3_count = min(stage3_count, stage2_count)

    # Stage 4 — converted sessions (visitor_id deduplicated)
    purchase_visitors_q = await db.execute(
        select(func.count(distinct(VisitorSession.visitor_id))).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.is_converted.is_(True),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    stage4_count: int = purchase_visitors_q.scalar() or 0
    stage4_count = min(stage4_count, stage3_count)

    raw_counts = [stage1_count, stage2_count, stage3_count, stage4_count]

    stages: list[FunnelStage] = []
    for i, (stage_key, label) in enumerate(_FUNNEL_STAGE_DEFS):
        count = raw_counts[i]
        if i == 0:
            drop_off_pct = 0.0
        else:
            prev = raw_counts[i - 1]
            if prev > 0:
                drop_off_pct = round((prev - count) / prev * 100, 2)
            else:
                drop_off_pct = 0.0
        stages.append(FunnelStage(stage=stage_key, label=label, count=count, drop_off_pct=drop_off_pct))

    data_confidence = stage1_count >= 20

    return FunnelResponse(
        store_id=store_id,
        date=target_date.isoformat(),
        stages=stages,
        data_confidence=data_confidence,
    )
