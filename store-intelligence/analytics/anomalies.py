"""
Anomaly detection engine.

Detects three anomaly classes:
  1. FOOTFALL_SPIKE      – current hour's footfall > 2× rolling average
  2. UNUSUAL_DWELL       – single visitor dwell > DWELL_TIME_ANOMALY_MINUTES
  3. CROWD_FORMATION     – current occupancy > CROWD_THRESHOLD
"""
from __future__ import annotations

import uuid
from datetime import datetime
from statistics import mean
from typing import List, Optional

from app.models.metric_model import AnomalyAlert
from app.services.state_manager import state_manager
from app.utils.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


def _make_alert(
    anomaly_type: str,
    description: str,
    severity: str,
    value: float,
    threshold: float,
) -> AnomalyAlert:
    return AnomalyAlert(
        anomaly_id=str(uuid.uuid4()),
        anomaly_type=anomaly_type,
        description=description,
        severity=severity,
        detected_at=datetime.utcnow(),
        value=value,
        threshold=threshold,
    )


def check_footfall_spike() -> Optional[AnomalyAlert]:
    """
    Compare the most-recently-completed hour's footfall against the
    rolling average of all preceding hours.
    """
    history = state_manager.get_hourly_footfall()
    if len(history) < 2:
        return None

    completed = history[:-1]  # exclude the still-in-progress bucket
    counts = [h["count"] for h in completed]
    if not counts:
        return None

    avg = mean(counts)
    latest = counts[-1]
    threshold = avg * settings.FOOTFALL_SPIKE_THRESHOLD

    if avg > 0 and latest > threshold:
        alert = _make_alert(
            anomaly_type="FOOTFALL_SPIKE",
            description=(
                f"Footfall in last completed hour ({latest}) is "
                f"{latest / avg:.1f}× above rolling average ({avg:.1f})"
            ),
            severity="HIGH" if latest > threshold * 1.5 else "MEDIUM",
            value=float(latest),
            threshold=threshold,
        )
        logger.warning("anomaly_footfall_spike", value=latest, avg=avg)
        return alert
    return None


def check_dwell_anomaly(person_id: int, dwell_seconds: float) -> Optional[AnomalyAlert]:
    """Called when a visitor exits with an unusually long dwell time."""
    threshold = settings.DWELL_TIME_ANOMALY_MINUTES * 60.0
    if dwell_seconds > threshold:
        alert = _make_alert(
            anomaly_type="UNUSUAL_DWELL",
            description=(
                f"Person {person_id} dwelled for "
                f"{dwell_seconds / 60:.1f} min "
                f"(threshold: {settings.DWELL_TIME_ANOMALY_MINUTES} min)"
            ),
            severity="HIGH" if dwell_seconds > threshold * 2 else "LOW",
            value=dwell_seconds,
            threshold=threshold,
        )
        logger.warning(
            "anomaly_unusual_dwell", person_id=person_id, dwell_seconds=dwell_seconds
        )
        return alert
    return None


def check_crowd_formation() -> Optional[AnomalyAlert]:
    """Check if current occupancy exceeds the crowd threshold."""
    snap = state_manager.get_metrics_snapshot()
    occupancy = snap["current_occupancy"]
    threshold = settings.CROWD_THRESHOLD

    if occupancy >= threshold:
        alert = _make_alert(
            anomaly_type="CROWD_FORMATION",
            description=(
                f"Current occupancy ({occupancy}) exceeds crowd threshold ({threshold})"
            ),
            severity="HIGH" if occupancy > threshold * 1.5 else "MEDIUM",
            value=float(occupancy),
            threshold=float(threshold),
        )
        logger.warning("anomaly_crowd_formation", occupancy=occupancy)
        return alert
    return None


def run_all_checks() -> List[AnomalyAlert]:
    """Run all anomaly checks and persist new alerts to StateManager."""
    alerts: List[AnomalyAlert] = []

    for check_fn in (check_footfall_spike, check_crowd_formation):
        alert = check_fn()
        if alert:
            alerts.append(alert)
            state_manager.add_anomaly(alert)

    return alerts
