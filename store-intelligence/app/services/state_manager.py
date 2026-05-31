"""
Thread-safe in-memory state manager.
Stores events, person tracking state, session windows, and aggregated metrics.
"""
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Deque

from app.models.event_model import PersonEvent, EventType
from app.models.metric_model import AnomalyAlert
from app.utils.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


class StateManager:
    """Central thread-safe store for all runtime state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

        # ---- event store ----
        self._events: List[PersonEvent] = []
        self._max_events: int = 20_000

        # ---- anomaly store ----
        self._anomalies: List[AnomalyAlert] = []
        self._max_anomalies: int = 500

        # ---- per-person tracking ----
        # person_id -> {"entry_time": datetime, "last_seen": datetime}
        self._active_persons: Dict[int, Dict] = {}
        # person_id -> last exit time (for re-entry detection)
        self._exit_times: Dict[int, datetime] = {}
        # dwell records: list of dwell_seconds for completed visits
        self._dwell_records: List[float] = []

        # ---- session / unique-visitor tracking ----
        # session key = str(person_id)  →  {first_entry, last_entry, visits}
        self._sessions: Dict[str, Dict] = {}

        # ---- staff ----
        self._staff_ids: Set[int] = set()

        # ---- aggregate counters ----
        self._total_entries: int = 0
        self._total_exits: int = 0
        self._unique_visitors: int = 0
        self._returning_visitors: int = 0   # had > 1 entry session
        self._engaged_count: int = 0        # dwell > ENGAGEMENT threshold
        self._current_occupancy: int = 0

        # ---- buyers (from sales CSV or external push) ----
        self._buyer_count: int = 0

        # ---- hourly footfall buckets for spike detection ----
        # deque of (bucket_hour: datetime, count: int)
        self._hourly_footfall: Deque = deque(maxlen=24)
        self._current_hour_bucket: Optional[datetime] = None
        self._current_hour_count: int = 0

        # ---- startup timestamp ----
        self.started_at: datetime = datetime.utcnow()

    # ------------------------------------------------------------------
    # Public write interface
    # ------------------------------------------------------------------

    def add_event(self, event: PersonEvent) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

            self._route_event(event)
            self._update_hourly_bucket(event.timestamp)

        logger.info(
            "event_recorded",
            event_type=event.event_type,
            person_id=event.person_id,
            timestamp=event.timestamp.isoformat(),
        )

    def record_exit_dwell(self, person_id: int, exit_time: datetime) -> None:
        """Called by event_generator when an EXIT is confirmed."""
        with self._lock:
            entry_info = self._active_persons.pop(person_id, None)
            self._exit_times[person_id] = exit_time
            if entry_info and "entry_time" in entry_info:
                dwell = (exit_time - entry_info["entry_time"]).total_seconds()
                if dwell > 0:
                    self._dwell_records.append(dwell)
                    if dwell >= settings.ENGAGEMENT_DWELL_SECONDS:
                        self._engaged_count += 1

    def set_buyer_count(self, count: int) -> None:
        with self._lock:
            self._buyer_count = count

    def add_anomaly(self, anomaly: AnomalyAlert) -> None:
        with self._lock:
            self._anomalies.append(anomaly)
            if len(self._anomalies) > self._max_anomalies:
                self._anomalies = self._anomalies[-self._max_anomalies:]

    # ------------------------------------------------------------------
    # Public read interface
    # ------------------------------------------------------------------

    def get_events(self, limit: int = 500) -> List[PersonEvent]:
        with self._lock:
            return list(self._events[-limit:])

    def get_anomalies(self) -> List[AnomalyAlert]:
        with self._lock:
            return list(self._anomalies)

    def get_metrics_snapshot(self) -> Dict:
        with self._lock:
            avg_dwell = (
                sum(self._dwell_records) / len(self._dwell_records)
                if self._dwell_records
                else 0.0
            )
            conversion = (
                self._buyer_count / self._unique_visitors
                if self._unique_visitors > 0
                else 0.0
            )
            revisit = (
                self._returning_visitors / self._unique_visitors
                if self._unique_visitors > 0
                else 0.0
            )
            return {
                "footfall": self._total_entries,
                "unique_visitors": self._unique_visitors,
                "conversion_rate": round(min(conversion, 1.0), 4),
                "avg_dwell_time": round(avg_dwell, 2),
                "current_occupancy": self._current_occupancy,
                "total_exits": self._total_exits,
                "revisit_rate": round(min(revisit, 1.0), 4),
                "staff_count": len(self._staff_ids),
                "buyer_count": self._buyer_count,
                "engaged_count": self._engaged_count,
            }

    def get_funnel_snapshot(self) -> Dict:
        with self._lock:
            entered = self._unique_visitors
            engaged = self._engaged_count
            converted = self._buyer_count
            return {
                "entered": entered,
                "engaged": engaged,
                "converted": converted,
                "engagement_rate": round(min(engaged / entered, 1.0), 4) if entered > 0 else 0.0,
                "conversion_rate": round(min(converted / entered, 1.0), 4) if entered > 0 else 0.0,
            }

    def get_hourly_footfall(self) -> List[Dict]:
        with self._lock:
            result = [
                {"hour": h.isoformat(), "count": c}
                for h, c in self._hourly_footfall
            ]
            if self._current_hour_bucket:
                result.append(
                    {
                        "hour": self._current_hour_bucket.isoformat(),
                        "count": self._current_hour_count,
                    }
                )
            return result

    @property
    def total_events(self) -> int:
        with self._lock:
            return len(self._events)

    @property
    def uptime_seconds(self) -> float:
        return (datetime.utcnow() - self.started_at).total_seconds()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _route_event(self, event: PersonEvent) -> None:
        """Update aggregated state based on event type. Must be called under lock."""
        pid = event.person_id
        ts = event.timestamp

        if event.event_type in (EventType.ENTRY, EventType.GROUP_ENTRY):
            self._total_entries += 1
            self._current_occupancy = max(0, self._current_occupancy + 1)
            self._active_persons[pid] = {"entry_time": ts, "last_seen": ts}
            self._register_session(pid, ts, is_reentry=False)

        elif event.event_type == EventType.REENTRY:
            self._total_entries += 1
            self._current_occupancy = max(0, self._current_occupancy + 1)
            self._active_persons[pid] = {"entry_time": ts, "last_seen": ts}
            self._register_session(pid, ts, is_reentry=True)

        elif event.event_type == EventType.EXIT:
            self._total_exits += 1
            self._current_occupancy = max(0, self._current_occupancy - 1)
            # dwell is handled via record_exit_dwell

        elif event.event_type == EventType.STAFF:
            self._staff_ids.add(pid)

    def _register_session(
        self, person_id: int, timestamp: datetime, is_reentry: bool
    ) -> None:
        key = str(person_id)
        if key not in self._sessions:
            self._sessions[key] = {
                "first_entry": timestamp,
                "last_entry": timestamp,
                "visits": 1,
            }
            self._unique_visitors += 1
        else:
            session = self._sessions[key]
            last = session["last_entry"]
            gap = (timestamp - last).total_seconds() / 60.0
            if gap >= settings.SESSION_WINDOW_MINUTES:
                # New session for returning visitor
                session["visits"] += 1
                session["last_entry"] = timestamp
                if session["visits"] == 2:
                    self._returning_visitors += 1

    def _update_hourly_bucket(self, ts: datetime) -> None:
        bucket = ts.replace(minute=0, second=0, microsecond=0)
        if self._current_hour_bucket is None:
            self._current_hour_bucket = bucket
            self._current_hour_count = 1
        elif bucket == self._current_hour_bucket:
            self._current_hour_count += 1
        else:
            # Roll over
            self._hourly_footfall.append(
                (self._current_hour_bucket, self._current_hour_count)
            )
            self._current_hour_bucket = bucket
            self._current_hour_count = 1


# Singleton instance
state_manager = StateManager()
