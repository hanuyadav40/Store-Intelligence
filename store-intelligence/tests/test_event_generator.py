"""
Unit tests for EventGenerator.
No YOLO / ByteTrack needed – we feed synthetic track arrays directly.
"""
from datetime import datetime, timedelta
import numpy as np
import pytest

from detector.event_generator import EventGenerator
from app.models.event_model import EventType
from app.utils.config import settings


def _make_tracks(centroids):
    """
    Given list of (cx, cy) tuples, return (xyxy, confs, track_ids).
    track_ids are sequential starting at 1.
    """
    n = len(centroids)
    xyxy = np.array(
        [[cx - 20, cy - 40, cx + 20, cy + 40] for cx, cy in centroids],
        dtype=np.float32,
    )
    confs = np.array([0.9] * n, dtype=np.float32)
    ids = np.arange(1, n + 1, dtype=np.int32)
    return xyxy, confs, ids


class TestEventGenerator:
    def setup_method(self):
        self.gen = EventGenerator()
        self.gen.set_frame_size(640, 480)
        self.line_y = int(480 * settings.ENTRY_LINE_Y_FRACTION)
        self.t0 = datetime(2026, 4, 10, 12, 0, 0)

    def _tick(self, centroids, dt_seconds=0):
        xyxy, confs, ids = _make_tracks(centroids)
        ts = self.t0 + timedelta(seconds=dt_seconds)
        return self.gen.process_tracks(xyxy, confs, ids, ts)

    # -------------------------------------------------------------------

    def test_no_events_on_first_frame(self):
        """First appearance of a track should not generate an event."""
        events = self._tick([(320, self.line_y - 10)], dt_seconds=0)
        assert len(events) == 0

    def test_entry_event_on_line_crossing_top_to_bottom(self):
        """Person above line then below → ENTRY."""
        self._tick([(320, self.line_y - 10)], dt_seconds=0)
        events = self._tick([(320, self.line_y + 10)], dt_seconds=1)
        assert any(e.event_type == EventType.ENTRY for e in events)

    def test_exit_event_on_line_crossing_bottom_to_top(self):
        """Person below line then above → EXIT."""
        self._tick([(320, self.line_y + 10)], dt_seconds=0)
        events = self._tick([(320, self.line_y - 10)], dt_seconds=1)
        assert any(e.event_type == EventType.EXIT for e in events)

    def test_reentry_within_session_window(self):
        """Person exits and re-enters within session window → REENTRY."""
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)

        # Appear outside
        xyxy, confs, ids = _make_tracks([(320, self.line_y - 10)])
        gen.process_tracks(xyxy, confs, ids, t0)

        # Cross in → ENTRY
        xyxy, confs, ids = _make_tracks([(320, self.line_y + 10)])
        gen.process_tracks(xyxy, confs, ids, t0 + timedelta(seconds=1))

        # Cross out → EXIT
        xyxy, confs, ids = _make_tracks([(320, self.line_y - 10)])
        gen.process_tracks(xyxy, confs, ids, t0 + timedelta(minutes=5))

        # Re-enter within 30 min → REENTRY
        xyxy, confs, ids = _make_tracks([(320, self.line_y + 10)])
        events = gen.process_tracks(
            xyxy, confs, ids, t0 + timedelta(minutes=10)
        )
        assert any(e.event_type == EventType.REENTRY for e in events)

    def test_group_entry_detection(self):
        """Two persons entering within GROUP_ENTRY_WINDOW_SECONDS → GROUP_ENTRY."""
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)

        # Person 1 appears outside
        xyxy = np.array([[280, self.line_y - 30, 320, self.line_y - 10]], dtype=np.float32)
        gen.process_tracks(xyxy, np.array([0.9]), np.array([1], dtype=np.int32), t0)

        # Person 1 crosses in → ENTRY
        xyxy = np.array([[280, self.line_y + 10, 320, self.line_y + 50]], dtype=np.float32)
        gen.process_tracks(xyxy, np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(seconds=1))

        # Person 2 appears outside
        xyxy = np.array([[320, self.line_y - 30, 360, self.line_y - 10]], dtype=np.float32)
        gen.process_tracks(xyxy, np.array([0.9]), np.array([2], dtype=np.int32), t0 + timedelta(seconds=1))

        # Person 2 crosses in quickly → GROUP_ENTRY
        xyxy = np.array([[320, self.line_y + 10, 360, self.line_y + 50]], dtype=np.float32)
        events = gen.process_tracks(
            xyxy, np.array([0.9]), np.array([2], dtype=np.int32),
            t0 + timedelta(seconds=1.5)
        )
        assert any(e.event_type == EventType.GROUP_ENTRY for e in events)

    def test_no_events_without_frame_size(self):
        """process_tracks before set_frame_size should return empty list."""
        gen = EventGenerator()  # no set_frame_size
        xyxy, confs, ids = _make_tracks([(320, 100)])
        events = gen.process_tracks(xyxy, confs, ids, datetime.utcnow())
        assert events == []

    def test_confidence_preserved_in_event(self):
        """Confidence value from detection should appear in the event."""
        self._tick([(320, self.line_y - 10)], dt_seconds=0)
        events = self._tick([(320, self.line_y + 10)], dt_seconds=1)
        entry_events = [e for e in events if e.event_type == EventType.ENTRY]
        assert len(entry_events) > 0
        assert 0.0 <= entry_events[0].confidence <= 1.0
