"""
Customer journey funnel analysis.

Funnel stages:
  Entered  →  people who crossed the entry line (unique visitors)
  Engaged  →  stayed ≥ ENGAGEMENT_DWELL_SECONDS (default 2 min)
  Converted →  made a purchase (linked to sales CSV buyer count)
"""
from __future__ import annotations

from app.services.state_manager import state_manager
from app.models.metric_model import FunnelResponse
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


def compute_funnel() -> FunnelResponse:
    """Return funnel stages derived from current state."""
    snap = state_manager.get_funnel_snapshot()
    return FunnelResponse(
        entered=snap["entered"],
        engaged=snap["engaged"],
        converted=snap["converted"],
        engagement_rate=snap["engagement_rate"],
        conversion_rate=snap["conversion_rate"],
    )
