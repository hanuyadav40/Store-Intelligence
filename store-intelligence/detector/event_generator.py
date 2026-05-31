"""
Event generator: translates per-frame track data into structured business events.

Logic:
  - Virtual horizontal entry line at ENTRY_LINE_Y_FRACTION of frame height
  - Centroid crosses line top→bottom  →  ENTRY  (or REENTRY / GROUP_ENTRY)
  - Centroid crosses line bottom→top  →  EXIT
  - Person present > STAFF_DWELL_THRESHOLD_HOURS  →  STAFF
  - N persons cross within GROUP_ENTRY_WINDOW_SECONDS  →  GROUP_ENTRY
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Set, Tuple
import numpy as np

from app.models.event_model import EventType, PersonEvent
from app.utils.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


class EventGenerator:
    """
    Stateful; one instance per camera/video stream.
    Call set_frame_size() before the first process_tracks() call.
    """

    def __init__(self, camera_id: Optional[str] = None) -> None:
        self._session_window = timedelta(minutes=settings.SESSION_WINDOW_MINUTES)
        self._staff_threshold = timedelta(hours=settings.STAFF_DWELL_THRESHOLD_HOURS)
        self._group_window = timedelta(seconds=settings.GROUP_ENTRY_WINDOW_SECONDS)
        self._group_min = settings.GROUP_ENTRY_MIN_SIZE
        self._camera_id = camera_id or settings.CAMERA_ID

        # frame geometry
        self._entry_line_y: Optional[int] = None

        # per-track state  (track_id → value)
        self._prev_y: Dict[int, float] = {}
        self._person_side: Dict[int, str] = {}  # "outside" | "inside"
        self._entry_times: Dict[int, datetime] = {}
        self._exit_times: Dict[int, datetime] = {}

        # staff
        self._staff_ids: Set[int] = set()

        # group-entry window: deque of (timestamp, track_id) for recent entries
        self._recent_entries: Deque[Tuple[datetime, int]] = deque()

        # debounce: suppress repeated ENTRY/EXIT within this window (seconds)
        self._debounce_seconds: float = 3.0
        self._last_event_ts: Dict[int, datetime] = {}  # track_id → last event time

        # external callback to push dwell on EXIT
        self._on_exit_dwell = None  # callable(person_id, exit_time)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_frame_size(self, width: int, height: int) -> None:
        self._entry_line_y = int(height * settings.ENTRY_LINE_Y_FRACTION)
        logger.debug(
            "entry_line_set",
            y=self._entry_line_y,
            height=height,
            fraction=settings.ENTRY_LINE_Y_FRACTION,
        )

    def set_exit_dwell_callback(self, callback) -> None:
        self._on_exit_dwell = callback

    def process_tracks(
        self,
        xyxy: np.ndarray,      # (M, 4)
        confidences: np.ndarray,  # (M,)
        track_ids: np.ndarray,    # (M,)
        frame_timestamp: datetime,
    ) -> List[PersonEvent]:
        """
        Process one frame worth of tracked detections.
        Returns a list of events generated this frame.
        """
        if self._entry_line_y is None:
            logger.warning("entry_line_not_set_skip_frame")
            return []

        events: List[PersonEvent] = []
        active_ids = set(int(tid) for tid in track_ids)

        for i in range(len(track_ids)):
            tid = int(track_ids[i])
            conf = float(confidences[i])
            bbox = xyxy[i]

            cy = float((bbox[1] + bbox[3]) / 2.0)
            prev_cy = self._prev_y.get(tid, cy)
            self._prev_y[tid] = cy

            # Initialise side on first appearance
            if tid not in self._person_side:
                if cy < self._entry_line_y:
                    self._person_side[tid] = "outside"
                else:
                    # Person already inside store when video starts — count as ENTRY
                    self._person_side[tid] = "inside"
                    self._entry_times[tid] = frame_timestamp
                    self._last_event_ts[tid] = frame_timestamp
                    new_events = self._handle_entry(tid, frame_timestamp, conf)
                    events.extend(new_events)

            side = self._person_side[tid]

            # ----- Entry: outside → inside -----
            if side == "outside" and cy >= self._entry_line_y:
                self._person_side[tid] = "inside"
                if not self._is_debounced(tid, frame_timestamp):
                    self._last_event_ts[tid] = frame_timestamp
                    new_events = self._handle_entry(tid, frame_timestamp, conf)
                    events.extend(new_events)

            # ----- Exit: inside → outside -----
            elif side == "inside" and cy < self._entry_line_y:
                self._person_side[tid] = "outside"
                if not self._is_debounced(tid, frame_timestamp):
                    self._last_event_ts[tid] = frame_timestamp
                    exit_evt = self._handle_exit(tid, frame_timestamp, conf)
                    if exit_evt:
                        events.append(exit_evt)

            # ----- Long-dwell → Staff detection -----
            if (
                tid in self._entry_times
                and tid not in self._staff_ids
                and (frame_timestamp - self._entry_times[tid]) >= self._staff_threshold
            ):
                self._staff_ids.add(tid)
                events.append(
                    self._make_event(tid, EventType.STAFF, frame_timestamp, conf)
                )

        return events

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _handle_entry(
        self, tid: int, ts: datetime, conf: float
    ) -> List[PersonEvent]:
        events: List[PersonEvent] = []

        # --- Check re-entry ---
        last_exit = self._exit_times.get(tid)
        if last_exit and (ts - last_exit) < self._session_window:
            event_type = EventType.REENTRY
        else:
            event_type = EventType.ENTRY

        # --- Check group entry ---
        self._purge_old_group_entries(ts)
        self._recent_entries.append((ts, tid))
        same_window = [
            t for t, _id in self._recent_entries
            if (ts - t) <= self._group_window and _id != tid
        ]
        if len(same_window) >= self._group_min - 1:
            # There are already (min_size-1) other entries in the window
            event_type = EventType.GROUP_ENTRY

        self._entry_times[tid] = ts
        events.append(self._make_event(tid, event_type, ts, conf))
        return events

    def _handle_exit(
        self, tid: int, ts: datetime, conf: float
    ) -> Optional[PersonEvent]:
        self._exit_times[tid] = ts
        if self._on_exit_dwell:
            self._on_exit_dwell(tid, ts)
        return self._make_event(tid, EventType.EXIT, ts, conf)

    def _is_debounced(self, tid: int, now: datetime) -> bool:
        """Return True if this track fired an event too recently."""
        last = self._last_event_ts.get(tid)
        if last is None:
            return False
        return (now - last).total_seconds() < self._debounce_seconds

    def _purge_old_group_entries(self, now: datetime) -> None:
        while self._recent_entries:
            oldest_ts, _ = self._recent_entries[0]
            if (now - oldest_ts) > self._group_window:
                self._recent_entries.popleft()
            else:
                break

    def _make_event(
        self, tid: int, event_type: EventType, ts: datetime, conf: float
    ) -> PersonEvent:
        return PersonEvent(
            person_id=tid,
            timestamp=ts,
            event_type=event_type,
            confidence=conf,
            camera_id=self._camera_id,
        )
