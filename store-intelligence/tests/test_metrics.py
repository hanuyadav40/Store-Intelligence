"""
Unit tests for business metrics and funnel computation.
"""
from datetime import datetime, timedelta
import pytest

from app.models.event_model import PersonEvent, EventType
from app.services.state_manager import StateManager
from analytics.metrics import compute_metrics
from analytics.funnel import compute_funnel


def _state_with_events(events):
    """Build a fresh StateManager, push events, return it."""
    sm = StateManager()
    for ev in events:
        sm.add_event(ev)
    return sm


def _entry(pid, ts, etype=EventType.ENTRY):
    return PersonEvent(
        person_id=pid,
        timestamp=ts,
        event_type=etype,
        confidence=0.9,
        camera_id="cam_01",
    )


def _exit(pid, ts):
    return PersonEvent(
        person_id=pid,
        timestamp=ts,
        event_type=EventType.EXIT,
        confidence=0.9,
        camera_id="cam_01",
    )


class TestStateManagerMetrics:
    def test_footfall_counts_entries(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([_entry(1, t0), _entry(2, t0)])
        snap = sm.get_metrics_snapshot()
        assert snap["footfall"] == 2

    def test_unique_visitors_no_double_count(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        # Same person_id twice → still 1 unique visitor
        sm = _state_with_events([_entry(1, t0), _entry(1, t0 + timedelta(minutes=1))])
        snap = sm.get_metrics_snapshot()
        assert snap["unique_visitors"] == 1

    def test_reentry_not_counted_as_new_visitor(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([
            _entry(1, t0),
            _entry(1, t0 + timedelta(minutes=10), EventType.REENTRY),
        ])
        snap = sm.get_metrics_snapshot()
        assert snap["unique_visitors"] == 1

    def test_occupancy_increments_on_entry(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([_entry(1, t0), _entry(2, t0)])
        snap = sm.get_metrics_snapshot()
        assert snap["current_occupancy"] == 2

    def test_occupancy_decrements_on_exit(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([_entry(1, t0), _exit(1, t0 + timedelta(minutes=5))])
        snap = sm.get_metrics_snapshot()
        assert snap["current_occupancy"] == 0

    def test_occupancy_never_negative(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([_exit(99, t0)])  # exit without prior entry
        snap = sm.get_metrics_snapshot()
        assert snap["current_occupancy"] >= 0

    def test_dwell_time_recorded_on_exit(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = StateManager()
        sm.add_event(_entry(1, t0))
        sm.record_exit_dwell(1, t0 + timedelta(minutes=10))
        snap = sm.get_metrics_snapshot()
        assert snap["avg_dwell_time"] == pytest.approx(600.0, rel=0.01)

    def test_conversion_rate_with_buyers(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = StateManager()
        for pid in range(1, 11):  # 10 unique visitors
            sm.add_event(_entry(pid, t0))
        sm.set_buyer_count(3)
        snap = sm.get_metrics_snapshot()
        assert snap["conversion_rate"] == pytest.approx(0.3, rel=0.01)

    def test_staff_event_adds_to_staff_set(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([
            PersonEvent(
                person_id=99,
                timestamp=t0,
                event_type=EventType.STAFF,
                confidence=0.95,
                camera_id="cam_01",
            )
        ])
        snap = sm.get_metrics_snapshot()
        assert snap["staff_count"] == 1

    def test_group_entry_counts_as_footfall(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = _state_with_events([
            _entry(1, t0, EventType.GROUP_ENTRY),
            _entry(2, t0, EventType.GROUP_ENTRY),
        ])
        snap = sm.get_metrics_snapshot()
        assert snap["footfall"] == 2


class TestFunnel:
    def test_funnel_conversion_below_100_percent(self):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        sm = StateManager()
        for pid in range(1, 6):
            sm.add_event(_entry(pid, t0))
        sm.set_buyer_count(2)

        import analytics.funnel as funnel_mod
        funnel_mod.state_manager = sm
        funnel = compute_funnel()
        assert funnel.conversion_rate <= 1.0
        assert funnel.converted == 2

    def test_funnel_zeros_when_no_visitors(self):
        sm = StateManager()
        import analytics.funnel as funnel_mod
        funnel_mod.state_manager = sm
        funnel = compute_funnel()
        assert funnel.entered == 0
        assert funnel.conversion_rate == 0.0
