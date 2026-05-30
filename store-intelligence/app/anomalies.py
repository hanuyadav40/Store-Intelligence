"""
Anomaly detection engine.

Detects three classes of operational anomalies:

1. BILLING_QUEUE_SPIKE
   Current queue depth exceeds warn/critical thresholds.
   Threshold: WARN ≥ 5, CRITICAL ≥ 10 (configurable).

2. CONVERSION_DROP
   Today's rolling conversion rate is significantly below the 7-day average.
   Uses simple percentage drop rather than statistical z-score to keep it
   interpretable and explainable to ops teams.

3. DEAD_ZONE
   A product zone that normally receives traffic has had zero events for
   ≥ 30 minutes during store hours.

4. STALE_FEED (surfaced via /health but also exposed here)
   A store's event feed has been silent for > 10 minutes.

All anomalies include:
  - severity: INFO / WARN / CRITICAL
  - suggested_action: human-readable guidance for the ops team
"""
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Event, VisitorSession
from app.schemas import Anomaly, AnomalyResponse

logger = logging.getLogger("api.anomalies")
settings = get_settings()


async def get_store_anomalies(
    store_id: str,
    db: AsyncSession,
    target_date: Optional[date] = None,
) -> AnomalyResponse:
    now = datetime.now(timezone.utc)
    if target_date is None:
        target_date = now.date()

    anomalies: list[Anomaly] = []

    anomalies.extend(await _check_queue_spike(store_id, db, now))
    anomalies.extend(await _check_conversion_drop(store_id, db, target_date, now))
    anomalies.extend(await _check_dead_zones(store_id, db, now))

    return AnomalyResponse(
        store_id=store_id,
        anomalies=anomalies,
        as_of=now.isoformat(),
    )


# ---------------------------------------------------------------------------
# Queue spike
# ---------------------------------------------------------------------------
async def _check_queue_spike(
    store_id: str, db: AsyncSession, now: datetime
) -> list[Anomaly]:
    window_cutoff = now - timedelta(minutes=10)

    q = await db.execute(
        select(func.max(Event.queue_depth)).where(
            Event.store_id == store_id,
            Event.event_type == "BILLING_QUEUE_JOIN",
            Event.timestamp >= window_cutoff,
        )
    )
    current_depth: int = q.scalar() or 0

    if current_depth < settings.queue_spike_warn_threshold:
        return []

    if current_depth >= settings.queue_spike_critical_threshold:
        severity = "CRITICAL"
        action = (
            "Open additional billing counters immediately. "
            "Alert floor manager to redirect customers."
        )
    else:
        severity = "WARN"
        action = (
            "Monitor billing queue. Consider opening a second counter "
            "if depth increases further."
        )

    return [
        Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="BILLING_QUEUE_SPIKE",
            severity=severity,
            description=f"Billing queue depth is {current_depth} (threshold: {settings.queue_spike_warn_threshold})",
            suggested_action=action,
            detected_at=now.isoformat(),
            metadata={"current_queue_depth": current_depth},
        )
    ]


# ---------------------------------------------------------------------------
# Conversion drop
# ---------------------------------------------------------------------------
async def _check_conversion_drop(
    store_id: str, db: AsyncSession, target_date: date, now: datetime
) -> list[Anomaly]:
    day_start = datetime.combine(target_date, time.min).replace(tzinfo=timezone.utc)
    day_end = datetime.combine(target_date, time.max).replace(tzinfo=timezone.utc)

    # Today's conversion rate
    today_sessions_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    today_sessions: int = today_sessions_q.scalar() or 0
    if today_sessions < 10:
        # Insufficient data for reliable anomaly detection
        return []

    today_converted_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.is_converted.is_(True),
            VisitorSession.entry_time.between(day_start, day_end),
        )
    )
    today_converted: int = today_converted_q.scalar() or 0
    today_rate = today_converted / today_sessions if today_sessions > 0 else 0.0

    # 7-day historical average (excluding today)
    hist_start = day_start - timedelta(days=7)
    hist_end = day_start  # exclusive

    hist_sessions_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.entry_time.between(hist_start, hist_end),
        )
    )
    hist_sessions: int = hist_sessions_q.scalar() or 0
    if hist_sessions < 20:
        # Not enough historical data for baseline
        return []

    hist_converted_q = await db.execute(
        select(func.count(VisitorSession.id)).where(
            VisitorSession.store_id == store_id,
            VisitorSession.is_staff.is_(False),
            VisitorSession.is_converted.is_(True),
            VisitorSession.entry_time.between(hist_start, hist_end),
        )
    )
    hist_converted: int = hist_converted_q.scalar() or 0
    baseline_rate = hist_converted / hist_sessions if hist_sessions > 0 else 0.0

    if baseline_rate == 0.0:
        return []

    drop_pct = (baseline_rate - today_rate) / baseline_rate

    if drop_pct < settings.conversion_drop_warn_pct:
        return []

    if drop_pct >= settings.conversion_drop_critical_pct:
        severity = "CRITICAL"
        action = (
            "Conversion is critically low. Trigger immediate floor-walk. "
            "Check if billing is operational and staff are available to assist."
        )
    else:
        severity = "WARN"
        action = (
            "Conversion rate is below 7-day average. Review zone engagement via heatmap. "
            "Consider activating promotions for high-dwell low-conversion zones."
        )

    return [
        Anomaly(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CONVERSION_DROP",
            severity=severity,
            description=(
                f"Today's conversion rate {today_rate:.1%} is {drop_pct:.1%} below "
                f"7-day baseline of {baseline_rate:.1%}"
            ),
            suggested_action=action,
            detected_at=now.isoformat(),
            metadata={
                "today_rate": round(today_rate, 4),
                "baseline_rate": round(baseline_rate, 4),
                "drop_pct": round(drop_pct, 4),
            },
        )
    ]


# ---------------------------------------------------------------------------
# Dead zone detection
# ---------------------------------------------------------------------------
async def _check_dead_zones(
    store_id: str, db: AsyncSession, now: datetime
) -> list[Anomaly]:
    """
    A zone is 'dead' if it has had zero ZONE_ENTER events in the past
    DEAD_ZONE_THRESHOLD_MINUTES minutes, but did have activity earlier today.
    """
    threshold = now - timedelta(minutes=settings.dead_zone_threshold_minutes)
    day_start = datetime.combine(now.date(), time.min).replace(tzinfo=timezone.utc)

    # Zones that had activity today
    active_today_q = await db.execute(
        select(Event.zone_id)
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.is_staff.is_(False),
            Event.timestamp >= day_start,
            Event.zone_id.isnot(None),
            Event.zone_id.notin_(("BILLING", "BILLING_QUEUE", "ENTRY_ZONE")),
        )
        .distinct()
    )
    active_zones = {row.zone_id for row in active_today_q}

    if not active_zones:
        return []

    # Zones with recent activity (within dead zone window)
    recent_q = await db.execute(
        select(Event.zone_id)
        .where(
            Event.store_id == store_id,
            Event.event_type == "ZONE_ENTER",
            Event.is_staff.is_(False),
            Event.timestamp >= threshold,
            Event.zone_id.isnot(None),
        )
        .distinct()
    )
    recently_active = {row.zone_id for row in recent_q}

    dead_zones = active_zones - recently_active
    anomalies: list[Anomaly] = []

    for zone_id in sorted(dead_zones):
        anomalies.append(
            Anomaly(
                anomaly_id=str(uuid.uuid4()),
                anomaly_type="DEAD_ZONE",
                severity="INFO",
                description=(
                    f"Zone '{zone_id}' has had no customer visits for "
                    f"over {settings.dead_zone_threshold_minutes} minutes "
                    f"despite earlier activity today."
                ),
                suggested_action=(
                    f"Check if zone '{zone_id}' is accessible. Verify camera coverage. "
                    "Consider moving a floor associate to re-engage customers."
                ),
                detected_at=now.isoformat(),
                metadata={
                    "zone_id": zone_id,
                    "dead_since_minutes": settings.dead_zone_threshold_minutes,
                },
            )
        )

    return anomalies
