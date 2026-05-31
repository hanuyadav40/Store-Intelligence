"""
Business metrics calculator.
Reads from StateManager and produces derived metrics.
All heavy computation happens here, keeping StateManager as a pure data store.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List

from app.services.state_manager import state_manager
from app.models.metric_model import MetricsResponse
from app.utils.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


def compute_metrics() -> MetricsResponse:
    """Return a fully populated MetricsResponse from current state."""
    snap = state_manager.get_metrics_snapshot()
    return MetricsResponse(
        footfall=snap["footfall"],
        unique_visitors=snap["unique_visitors"],
        conversion_rate=snap["conversion_rate"],
        avg_dwell_time=snap["avg_dwell_time"],
        current_occupancy=snap["current_occupancy"],
        total_exits=snap["total_exits"],
        revisit_rate=snap["revisit_rate"],
        staff_count=snap["staff_count"],
        timestamp=datetime.utcnow(),
    )


def load_buyers_from_csv(csv_path: str) -> int:
    """
    Parse sales CSV and count unique buyers for the Brigade_Bangalore store.
    Returns the unique buyer count, which is pushed into StateManager.
    """
    try:
        import pandas as pd  # type: ignore

        df = pd.read_csv(csv_path, dtype=str)
        # Normalise column names
        df.columns = [c.strip() for c in df.columns]

        # Filter to sales (not returns) for this store
        if "invoice_type" in df.columns:
            df = df[df["invoice_type"].str.strip().str.lower() == "sales"]

        buyer_col = None
        for col in ("customer_number", "phone", "mobile"):
            if col in df.columns:
                buyer_col = col
                break

        if buyer_col is None:
            logger.warning("csv_no_customer_column", path=csv_path)
            return 0

        count = int(df[buyer_col].dropna().nunique())
        logger.info("buyers_loaded_from_csv", count=count, path=csv_path)
        return count
    except FileNotFoundError:
        logger.debug("sales_csv_not_found", path=csv_path)
        return 0
    except Exception as exc:
        logger.error("csv_parse_error", error=str(exc))
        return 0
