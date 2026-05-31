"""
API integration tests using FastAPI TestClient (async).
"""
import pytest
import pytest_asyncio
from datetime import datetime

from app.models.event_model import PersonEvent, EventType
from app.services.state_manager import StateManager


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_returns_ok(self, async_client):
        resp = await async_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert "uptime_seconds" in body

    async def test_health_demo_mode_field_present(self, async_client):
        resp = await async_client.get("/health")
        body = resp.json()
        assert "demo_mode" in body


@pytest.mark.asyncio
class TestMetricsEndpoint:
    async def test_metrics_schema(self, async_client):
        resp = await async_client.get("/metrics")
        assert resp.status_code == 200
        body = resp.json()
        for field in ("footfall", "unique_visitors", "conversion_rate", "avg_dwell_time"):
            assert field in body, f"Missing field: {field}"

    async def test_metrics_initial_zeros(self, async_client):
        resp = await async_client.get("/metrics")
        body = resp.json()
        assert body["footfall"] == 0
        assert body["unique_visitors"] == 0

    async def test_metrics_reflect_added_events(self, async_client, fresh_state):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        for pid in range(1, 4):
            fresh_state.add_event(
                PersonEvent(
                    person_id=pid,
                    timestamp=t0,
                    event_type=EventType.ENTRY,
                    confidence=0.9,
                    camera_id="cam_01",
                )
            )
        resp = await async_client.get("/metrics")
        body = resp.json()
        assert body["footfall"] == 3
        assert body["unique_visitors"] == 3


@pytest.mark.asyncio
class TestFunnelEndpoint:
    async def test_funnel_schema(self, async_client):
        resp = await async_client.get("/funnel")
        assert resp.status_code == 200
        body = resp.json()
        for field in ("entered", "engaged", "converted"):
            assert field in body

    async def test_funnel_engaged_le_entered(self, async_client, fresh_state):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        for pid in range(1, 6):
            fresh_state.add_event(
                PersonEvent(
                    person_id=pid,
                    timestamp=t0,
                    event_type=EventType.ENTRY,
                    confidence=0.9,
                    camera_id="cam_01",
                )
            )
        resp = await async_client.get("/funnel")
        body = resp.json()
        assert body["engaged"] <= body["entered"]

    async def test_funnel_converted_le_entered(self, async_client, fresh_state):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        for pid in range(1, 6):
            fresh_state.add_event(
                PersonEvent(
                    person_id=pid,
                    timestamp=t0,
                    event_type=EventType.ENTRY,
                    confidence=0.9,
                    camera_id="cam_01",
                )
            )
        fresh_state.set_buyer_count(2)
        resp = await async_client.get("/funnel")
        body = resp.json()
        assert body["converted"] <= body["entered"]


@pytest.mark.asyncio
class TestEventsEndpoint:
    async def test_events_empty_initially(self, async_client):
        resp = await async_client.get("/events")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_events_returns_list(self, async_client, fresh_state):
        fresh_state.add_event(
            PersonEvent(
                person_id=1,
                timestamp=datetime(2026, 4, 10, 12, 0, 0),
                event_type=EventType.ENTRY,
                confidence=0.9,
                camera_id="cam_01",
            )
        )
        resp = await async_client.get("/events")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["event_type"] == "ENTRY"

    async def test_events_filter_by_type(self, async_client, fresh_state):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        fresh_state.add_event(
            PersonEvent(person_id=1, timestamp=t0, event_type=EventType.ENTRY,
                        confidence=0.9, camera_id="cam_01")
        )
        fresh_state.add_event(
            PersonEvent(person_id=1, timestamp=t0, event_type=EventType.EXIT,
                        confidence=0.9, camera_id="cam_01")
        )
        resp = await async_client.get("/events?event_type=EXIT")
        body = resp.json()
        assert all(e["event_type"] == "EXIT" for e in body)

    async def test_events_limit_parameter(self, async_client, fresh_state):
        t0 = datetime(2026, 4, 10, 12, 0, 0)
        for i in range(10):
            fresh_state.add_event(
                PersonEvent(person_id=i, timestamp=t0, event_type=EventType.ENTRY,
                            confidence=0.9, camera_id="cam_01")
            )
        resp = await async_client.get("/events?limit=3")
        assert len(resp.json()) == 3


@pytest.mark.asyncio
class TestAnomaliesEndpoint:
    async def test_anomalies_empty_initially(self, async_client):
        resp = await async_client.get("/anomalies")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    async def test_anomalies_schema(self, async_client, fresh_state):
        from app.models.metric_model import AnomalyAlert
        import uuid
        alert = AnomalyAlert(
            anomaly_id=str(uuid.uuid4()),
            anomaly_type="CROWD_FORMATION",
            description="Test alert",
            severity="HIGH",
            detected_at=datetime.utcnow(),
            value=15.0,
            threshold=10.0,
        )
        fresh_state.add_anomaly(alert)
        resp = await async_client.get("/anomalies")
        body = resp.json()
        assert len(body) == 1
        assert body[0]["anomaly_type"] == "CROWD_FORMATION"
