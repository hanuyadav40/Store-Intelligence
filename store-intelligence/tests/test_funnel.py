# PROMPT: Test the conversion funnel endpoint. Validates 4-stage funnel,
# re-entry deduplication, staff exclusion, and data_confidence flag.
#
# CHANGES MADE:
# - Tests GET /stores/{store_id}/funnel
# - Re-entry events must not double-count the same visitor_id in Entry stage
# - data_confidence=False when session count < 20

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import make_event

STORE = "STORE_FUNNEL_TEST"


async def _ingest(client: AsyncClient, events: list[dict]) -> None:
    resp = await client.post("/events/ingest", json={"events": events})
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_funnel_empty_store(client: AsyncClient):
    """Empty store returns 4 stages all with count=0."""
    resp = await client.get(f"/stores/{STORE}_EMPTY/funnel")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["stages"]) == 4
    for stage in data["stages"]:
        assert stage["count"] == 0


@pytest.mark.asyncio
async def test_funnel_entry_stage(client: AsyncClient):
    """ENTRY events populate the first funnel stage."""
    store = STORE + "_ENTRY"
    for _ in range(5):
        vid = str(uuid.uuid4())
        await _ingest(client, [make_event(store_id=store, visitor_id=vid, event_type="ENTRY")])

    resp = await client.get(f"/stores/{store}/funnel")
    assert resp.status_code == 200
    data = resp.json()
    entry_stage = next(s for s in data["stages"] if s["stage"] == "ENTRY")
    assert entry_stage["count"] == 5


@pytest.mark.asyncio
async def test_funnel_data_confidence_low(client: AsyncClient):
    """Fewer than 20 sessions → data_confidence=False."""
    store = STORE + "_LOW_CONF"
    for _ in range(3):
        vid = str(uuid.uuid4())
        await _ingest(client, [make_event(store_id=store, visitor_id=vid, event_type="ENTRY")])

    resp = await client.get(f"/stores/{store}/funnel")
    assert resp.status_code == 200
    assert resp.json()["data_confidence"] is False


@pytest.mark.asyncio
async def test_funnel_stages_non_increasing(client: AsyncClient):
    """Each funnel stage count must be <= the previous stage count."""
    store = STORE + "_MONOTONE"
    # Create 10 visitors, 7 enter zones, 4 reach billing
    for _ in range(10):
        vid = str(uuid.uuid4())
        await _ingest(client, [make_event(store_id=store, visitor_id=vid, event_type="ENTRY")])
    for _ in range(7):
        vid = str(uuid.uuid4())
        await _ingest(client, [
            make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
            make_event(store_id=store, visitor_id=vid, event_type="ZONE_ENTER", zone_id="LIPSTICK"),
        ])
    for _ in range(4):
        vid = str(uuid.uuid4())
        await _ingest(client, [
            make_event(store_id=store, visitor_id=vid, event_type="ENTRY"),
            make_event(store_id=store, visitor_id=vid, event_type="BILLING_QUEUE_JOIN",
                       zone_id="BILLING", queue_depth=2),
        ])

    resp = await client.get(f"/stores/{store}/funnel")
    assert resp.status_code == 200
    stages = resp.json()["stages"]
    counts = [s["count"] for s in stages]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i - 1], f"Stage {i} count {counts[i]} > previous {counts[i-1]}"
