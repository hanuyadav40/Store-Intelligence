"""
Edge-case tests:
  - Re-entry detection
  - Staff identification
  - Group entry classification
  - Occlusion (track loss and reappearance)
  - Occupancy never goes negative
  - Session window boundary
"""
from datetime import datetime, timedelta
import numpy as np
import pytest

from detector.event_generator import EventGenerator
from app.models.event_model import EventType
from app.services.state_manager import StateManager
from app.utils.config import settings


LINE_Y_ABOVE = int(480 * settings.ENTRY_LINE_Y_FRACTION) - 10
LINE_Y_BELOW = int(480 * settings.ENTRY_LINE_Y_FRACTION) + 10


def _xyxy(cx, cy):
    return np.array([[cx - 15, cy - 30, cx + 15, cy + 30]], dtype=np.float32)


class TestReentryEdgeCases:
    def test_reentry_just_inside_session_window(self):
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)

        # Appear outside
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0)
        # Enter
        gen.process_tracks(_xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(seconds=5))
        # Exit
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(minutes=5))
        # Re-enter just before session window expires
        t_reentry = t0 + timedelta(minutes=settings.SESSION_WINDOW_MINUTES - 1)
        events = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32), t_reentry
        )
        types = [e.event_type for e in events]
        assert EventType.REENTRY in types

    def test_new_entry_after_session_window_expired(self):
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)

        # Appear and cross in
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0)
        gen.process_tracks(_xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(seconds=5))
        # Exit
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(minutes=5))
        # Re-enter AFTER session window (window is measured from exit at t0+5min)
        t_after = t0 + timedelta(minutes=5 + settings.SESSION_WINDOW_MINUTES + 1)
        events = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32), t_after
        )
        types = [e.event_type for e in events]
        # Should be ENTRY (new session), not REENTRY
        assert EventType.ENTRY in types
        assert EventType.REENTRY not in types


class TestStaffEdgeCases:
    def test_staff_event_generated_after_long_dwell(self):
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 8, 0, 0)

        # Enter
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([5], dtype=np.int32), t0)
        gen.process_tracks(_xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([5], dtype=np.int32), t0 + timedelta(seconds=1))

        # Fast-forward past staff dwell threshold
        t_staff = t0 + timedelta(hours=settings.STAFF_DWELL_THRESHOLD_HOURS + 0.1)
        events = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([5], dtype=np.int32), t_staff
        )
        assert any(e.event_type == EventType.STAFF for e in events)

    def test_staff_not_triggered_twice_for_same_id(self):
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 8, 0, 0)

        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([5], dtype=np.int32), t0)
        gen.process_tracks(_xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([5], dtype=np.int32), t0 + timedelta(seconds=1))

        threshold_time = timedelta(hours=settings.STAFF_DWELL_THRESHOLD_HOURS + 0.1)
        e1 = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([5], dtype=np.int32),
            t0 + threshold_time
        )
        # Second tick slightly later
        e2 = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([5], dtype=np.int32),
            t0 + threshold_time + timedelta(seconds=10)
        )
        staff_events_total = sum(
            1 for e in (e1 + e2) if e.event_type == EventType.STAFF
        )
        assert staff_events_total == 1  # only once per track


class TestGroupEntryEdgeCases:
    def test_solo_entry_not_group(self):
        """A single person entering should produce ENTRY, not GROUP_ENTRY."""
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)

        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0)
        events = gen.process_tracks(
            _xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32),
            t0 + timedelta(seconds=1)
        )
        types = [e.event_type for e in events]
        # No GROUP_ENTRY for a solo person
        assert EventType.GROUP_ENTRY not in types

    def test_group_entry_only_within_window(self):
        """Persons entering more than GROUP_ENTRY_WINDOW_SECONDS apart → separate ENTRY events."""
        gen = EventGenerator()
        gen.set_frame_size(640, 480)
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        gap = settings.GROUP_ENTRY_WINDOW_SECONDS + 5  # well outside window

        # Person 1 appears and enters
        gen.process_tracks(_xyxy(320, LINE_Y_ABOVE), np.array([0.9]), np.array([1], dtype=np.int32), t0)
        gen.process_tracks(_xyxy(320, LINE_Y_BELOW), np.array([0.9]), np.array([1], dtype=np.int32), t0 + timedelta(seconds=1))

        # Person 2 appears and enters well after the group window
        gen.process_tracks(_xyxy(380, LINE_Y_ABOVE), np.array([0.9]), np.array([2], dtype=np.int32), t0 + timedelta(seconds=gap))
        events2 = gen.process_tracks(
            _xyxy(380, LINE_Y_BELOW), np.array([0.9]), np.array([2], dtype=np.int32),
            t0 + timedelta(seconds=gap + 1)
        )
        types = [e.event_type for e in events2]
        # Should be ENTRY, not GROUP_ENTRY
        assert EventType.ENTRY in types
        assert EventType.GROUP_ENTRY not in types


class TestOcclusionHandling:
    def test_track_id_reuse_after_lost_track(self):
        """
        If a track is lost and a new detection gets the same ID,
        StateManager should treat it as a separate entry.
        """
        sm = StateManager()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        from app.models.event_model import PersonEvent

        sm.add_event(PersonEvent(person_id=42, timestamp=t0, event_type=EventType.ENTRY,
                                  confidence=0.9, camera_id="cam_01"))
        sm.record_exit_dwell(42, t0 + timedelta(minutes=5))
        sm.add_event(PersonEvent(person_id=42, timestamp=t0 + timedelta(minutes=10),
                                  event_type=EventType.ENTRY, confidence=0.9, camera_id="cam_01"))

        snap = sm.get_metrics_snapshot()
        assert snap["footfall"] == 2   # two ENTRY events recorded

    def test_occupancy_not_negative_on_orphan_exit(self):
        """Exit without preceding entry must not make occupancy negative."""
        sm = StateManager()
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        from app.models.event_model import PersonEvent

        sm.add_event(PersonEvent(person_id=99, timestamp=t0, event_type=EventType.EXIT,
                                  confidence=0.8, camera_id="cam_01"))
        snap = sm.get_metrics_snapshot()
        assert snap["current_occupancy"] >= 0
