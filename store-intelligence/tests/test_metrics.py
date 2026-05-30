# PROMPT: Test store metrics endpoint. Validates unique visitor counting,
# staff exclusion, conversion rate, and queue depth calculations.
# Seeds database via the /events/ingest API endpoint.
#
# CHANGES MADE:
# - Tests GET /stores/{store_id}/metrics
# - Staff visitors are excluded from unique_visitors and conversion_rate
# - Zero-traffic stores return safe default values (0s, not errors)

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import make_event

STORE = "STORE_METRICS_TEST"


async def _ingest(client: AsyncClient, events: list[dict]) -> None:
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_metrics_zero_traffic(client: AsyncClient):
    """Store with no events returns safe zeros, not an error."""
    resp = await client.get(f"/stores/{STORE}_EMPTY/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["unique_visitors"] == 0
    assert data["conversion_rate"] == 0.0
    assert data["current_queue_depth"] == 0


@pytest.mark.asyncio
async def test_metrics_staff_excluded(client: AsyncClient):
    """Staff visitor_ids must not be counted in unique_visitors."""
    store = STORE + "_STAFF"
    staff_id = str(uuid.uuid4())
    visitor_id = str(uuid.uuid4())

    await _ingest(client, [
        make_event(store_id=store, visitor_id=staff_id, event_type="ENTRY", is_staff=True),
        make_event(store_id=store, visitor_id=visitor_id, event_type="ENTRY", is_staff=False),
    ])

    resp = await client.get(f"/stores/{store}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    # Only 1 non-staff visitor
    assert data["unique_visitors"] == 1


@pytest.mark.asyncio
async def test_metrics_queue_depth(client: AsyncClient):
    """current_queue_depth should reflect the max billing queue_depth in last 10 min."""
    store = STORE + "_QUEUE"
    vid = str(uuid.uuid4())

    await _ingest(client, [
        make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING", queue_depth=7),
    ])

    resp = await client.get(f"/stores/{store}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current_queue_depth"] == 7


@pytest.mark.asyncio
async def test_metrics_dwell_per_zone(client: AsyncClient):
    """avg_dwell_per_zone should return correct zone stats."""
    store = STORE + "_DWELL"
    vid = str(uuid.uuid4())

    await _ingest(client, [
        make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store, visitor_id=vid, event_type="ZONE_ENTER", zone_id="SKINCARE"),
        make_event(store_id=store, visitor_id=vid, event_type="ZONE_DWELL",
                   zone_id="SKINCARE", dwell_ms=30000),
    ])

    resp = await client.get(f"/stores/{store}/metrics")
    assert resp.status_code == 200
    data = resp.json()
    zones = {z["zone_id"]: z for z in data["avg_dwell_per_zone"]}
    assert "SKINCARE" in zones
    assert zones["SKINCARE"]["avg_dwell_ms"] > 0
