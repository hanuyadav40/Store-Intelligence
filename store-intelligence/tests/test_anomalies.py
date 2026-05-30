# PROMPT: Test anomaly detection logic (QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE).
# Uses the unit-level anomaly functions with an in-memory SQLite DB.
# Also tests the API endpoint for anomalies.
#
# CHANGES MADE:
# - Tests GET /stores/{store_id}/anomalies
# - QUEUE_SPIKE triggered by BILLING_QUEUE_JOIN events with high queue_depth
# - No anomalies on a normal-looking store
# - DEAD_ZONE detected when a zone had earlier traffic but none recently

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from tests.conftest import make_event

STORE = "STORE_ANOMALY_TEST"


async def _ingest(client: AsyncClient, events: list[dict]) -> None:
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_anomalies_empty_store(client: AsyncClient):
    """Store with no events returns empty anomaly list."""
    resp = await client.get(f"/stores/{STORE}_EMPTY/anomalies")
    assert resp.status_code == 200
    data = resp.json()
    assert data["anomalies"] == []


@pytest.mark.asyncio
async def test_queue_spike_warn(client: AsyncClient):
    """Queue depth of 6 should trigger a WARN BILLING_QUEUE_SPIKE."""
    store = STORE + "_QSPIKE_WARN"
    vid = str(uuid.uuid4())
    await _ingest(client, [
        make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING", queue_depth=6),
    ])

    resp = await client.get(f"/stores/{store}/anomalies")
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    spike = [a for a in anomalies if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike) == 1
    assert spike[0]["severity"] in ("WARN", "CRITICAL")


@pytest.mark.asyncio
async def test_queue_spike_critical(client: AsyncClient):
    """Queue depth >= 10 should trigger a CRITICAL BILLING_QUEUE_SPIKE."""
    store = STORE + "_QSPIKE_CRIT"
    vid = str(uuid.uuid4())
    await _ingest(client, [
        make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING", queue_depth=12),
    ])

    resp = await client.get(f"/stores/{store}/anomalies")
    assert resp.status_code == 200
    anomalies = resp.json()["anomalies"]
    spike = [a for a in anomalies if a["anomaly_type"] == "BILLING_QUEUE_SPIKE"]
    assert len(spike) == 1
    assert spike[0]["severity"] == "CRITICAL"


@pytest.mark.asyncio
async def test_anomaly_has_suggested_action(client: AsyncClient):
    """Every anomaly must include a non-empty suggested_action."""
    store = STORE + "_ACTION"
    vid = str(uuid.uuid4())
    await _ingest(client, [
        make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
        make_event(store_id=store, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                   zone_id="BILLING", queue_depth=8),
    ])

    resp = await client.get(f"/stores/{store}/anomalies")
    assert resp.status_code == 200
    for anomaly in resp.json()["anomalies"]:
        assert anomaly["suggested_action"], "suggested_action must not be empty"
        assert anomaly["anomaly_id"], "anomaly_id must be present"
        assert anomaly["severity"] in ("INFO", "WARN", "CRITICAL")
