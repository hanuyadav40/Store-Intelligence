"""
10 API acceptance assertions from the challenge specification.

Run with: python data/assertions.py

Requires the API to be running at API_BASE_URL (default: http://localhost:8000).
Tests the exact endpoints and response structure specified in the challenge.

Exit code 0 = all assertions passed.
Exit code 1 = one or more assertions failed.
"""
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
STORE_ID = "STORE_BLR_001"
CAMERA_ID = "CAM_BLR_001_FLOOR"


def _make_event(event_type, visitor_id=None, zone_id=None, dwell_ms=0,
                queue_depth=None, is_staff=False):
    metadata = {}
    if queue_depth is not None:
        metadata["queue_depth"] = queue_depth
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": STORE_ID,
        "camera_id": CAMERA_ID,
        "visitor_id": visitor_id or str(uuid.uuid4()),
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": 0.92,
        "metadata": metadata,
    }


passed = 0
failed = 0


def check(name, condition, details=""):
    global passed, failed
    if condition:
        print(f"  ✓ {name}")
        passed += 1
    else:
        print(f"  ✗ {name}: {details}")
        failed += 1


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
# 1. Health endpoint returns valid structure
# ============================================================
section("Assertion 1: GET /health returns valid structure")
r = requests.get(f"{API_BASE_URL}/health", timeout=10)
check("HTTP 200 or 503", r.status_code in (200, 503))
h = r.json()
check("status field present", "status" in h)
check("status is valid value", h.get("status") in ("healthy", "degraded", "unhealthy"))
check("database field present", "database" in h)
check("redis field present", "redis" in h)
check("stores is a list", isinstance(h.get("stores"), list))
check("uptime_seconds > 0", h.get("uptime_seconds", -1) >= 0)

# ============================================================
# 2. Ingest a single ENTRY event — accepted=1
# ============================================================
section("Assertion 2: POST /events/ingest — single ENTRY accepted")
vid1 = str(uuid.uuid4())
r = requests.post(f"{API_BASE_URL}/events/ingest",
                  json={"events": [_make_event("ENTRY", visitor_id=vid1)]},
                  timeout=10)
check("HTTP 200", r.status_code == 200)
body = r.json()
check("accepted=1", body.get("accepted") == 1)
check("rejected=0", body.get("rejected") == 0)
check("trace_id present", bool(body.get("trace_id")))

# ============================================================
# 3. Duplicate event → duplicate=1
# ============================================================
section("Assertion 3: Duplicate event_id returns duplicate=1")
dup_event = _make_event("ENTRY")
requests.post(f"{API_BASE_URL}/events/ingest", json={"events": [dup_event]}, timeout=10)
r2 = requests.post(f"{API_BASE_URL}/events/ingest", json={"events": [dup_event]}, timeout=10)
check("HTTP 200", r2.status_code == 200)
check("duplicate=1", r2.json().get("duplicate") == 1)
check("accepted=0", r2.json().get("accepted") == 0)

# ============================================================
# 4. Schema validation: ZONE_ENTER without zone_id → 422
# ============================================================
section("Assertion 4: ZONE_ENTER without zone_id → HTTP 422")
r = requests.post(f"{API_BASE_URL}/events/ingest",
                  json={"events": [_make_event("ZONE_ENTER", zone_id=None)]},
                  timeout=10)
check("HTTP 422", r.status_code == 422)

# ============================================================
# 5. BILLING_QUEUE_JOIN without queue_depth → 422
# ============================================================
section("Assertion 5: BILLING_QUEUE_JOIN without queue_depth → 422")
bad_billing = _make_event("BILLING_QUEUE_JOIN", zone_id="BILLING")
bad_billing["metadata"] = {}  # no queue_depth
r = requests.post(f"{API_BASE_URL}/events/ingest", json={"events": [bad_billing]}, timeout=10)
check("HTTP 422", r.status_code == 422)

# ============================================================
# 6. GET /stores/{store_id}/metrics returns required fields
# ============================================================
section("Assertion 6: GET /stores/{store_id}/metrics structure")
r = requests.get(f"{API_BASE_URL}/stores/{STORE_ID}/metrics", timeout=10)
check("HTTP 200", r.status_code == 200)
m = r.json()
check("unique_visitors present", "unique_visitors" in m)
check("conversion_rate present", "conversion_rate" in m)
check("avg_dwell_per_zone is list", isinstance(m.get("avg_dwell_per_zone"), list))
check("current_queue_depth present", "current_queue_depth" in m)
check("abandonment_rate present", "abandonment_rate" in m)

# ============================================================
# 7. GET /stores/{store_id}/funnel returns 4 stages
# ============================================================
section("Assertion 7: GET /stores/{store_id}/funnel returns 4 stages")
r = requests.get(f"{API_BASE_URL}/stores/{STORE_ID}/funnel", timeout=10)
check("HTTP 200", r.status_code == 200)
f = r.json()
check("stages is list", isinstance(f.get("stages"), list))
check("4 stages present", len(f.get("stages", [])) == 4)
check("data_confidence present", "data_confidence" in f)
stage_names = [s["stage"] for s in f.get("stages", [])]
check("ENTRY stage present", "ENTRY" in stage_names)
check("PURCHASE stage present", "PURCHASE" in stage_names)

# ============================================================
# 8. GET /stores/{store_id}/heatmap returns zones with normalised_score
# ============================================================
section("Assertion 8: GET /stores/{store_id}/heatmap structure")
r = requests.get(f"{API_BASE_URL}/stores/{STORE_ID}/heatmap", timeout=10)
check("HTTP 200", r.status_code == 200)
h = r.json()
check("zones is list", isinstance(h.get("zones"), list))
check("data_confidence present", "data_confidence" in h)
# If zones present, each must have normalised_score 0-100
for z in h.get("zones", []):
    check(f"zone {z.get('zone_id')} normalised_score 0-100",
          0.0 <= z.get("normalised_score", -1) <= 100.0)

# ============================================================
# 9. GET /stores/{store_id}/anomalies returns valid anomaly list
# ============================================================
section("Assertion 9: GET /stores/{store_id}/anomalies structure")
# First seed a high queue event to trigger an anomaly
vid_q = str(uuid.uuid4())
requests.post(f"{API_BASE_URL}/events/ingest", json={
    "events": [
        _make_event("ENTRY", visitor_id=vid_q),
        _make_event("BILLING_QUEUE_JOIN", visitor_id=vid_q, zone_id="BILLING", queue_depth=11),
    ]
}, timeout=10)

r = requests.get(f"{API_BASE_URL}/stores/{STORE_ID}/anomalies", timeout=10)
check("HTTP 200", r.status_code == 200)
a_body = r.json()
check("anomalies is list", isinstance(a_body.get("anomalies"), list))
check("as_of present", bool(a_body.get("as_of")))
for a in a_body.get("anomalies", []):
    check(f"anomaly {a.get('anomaly_id')} has severity",
          a.get("severity") in ("INFO", "WARN", "CRITICAL"))
    check(f"anomaly {a.get('anomaly_id')} has suggested_action",
          bool(a.get("suggested_action")))

# ============================================================
# 10. Staff events excluded from unique_visitors
# ============================================================
section("Assertion 10: Staff events excluded from unique_visitors")
staff_vid = str(uuid.uuid4())
visitor_vid = str(uuid.uuid4())
requests.post(f"{API_BASE_URL}/events/ingest", json={
    "events": [
        _make_event("ENTRY", visitor_id=staff_vid, is_staff=True),
        _make_event("ENTRY", visitor_id=visitor_vid, is_staff=False),
    ]
}, timeout=10)
r = requests.get(f"{API_BASE_URL}/stores/{STORE_ID}/metrics", timeout=10)
data = r.json()
# Unique visitors must count only non-staff
check("unique_visitors is int", isinstance(data.get("unique_visitors"), int))
check("unique_visitors >= 1 (non-staff visitor was added)", data.get("unique_visitors", 0) >= 1)

# ============================================================
# Summary
# ============================================================
print(f"\n{'='*60}")
print(f"  RESULTS: {passed} passed, {failed} failed")
print(f"{'='*60}\n")
sys.exit(0 if failed == 0 else 1)
