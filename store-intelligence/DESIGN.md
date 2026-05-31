# DESIGN.md — Store Intelligence System Architecture

## 1. System Overview

The Store Intelligence system is an end-to-end pipeline that ingests raw CCTV
footage (or runs in a synthetic-data demo mode) and produces real-time business
metrics accessible via a REST API.

```
┌──────────────────────────────────────────────────────────┐
│                     Docker Container                      │
│                                                          │
│  ┌─────────────┐     ┌─────────────┐   ┌─────────────┐  │
│  │  Video /    │────▶│  Detection  │──▶│   Tracking  │  │
│  │  Demo Src   │     │  YOLOv8n    │   │  ByteTrack  │  │
│  └─────────────┘     └─────────────┘   └──────┬──────┘  │
│                                               │          │
│                                    ┌──────────▼──────┐  │
│                                    │ Event Generator │  │
│                                    │ (virtual line + │  │
│                                    │  business rules)│  │
│                                    └──────────┬──────┘  │
│                                               │          │
│                              ┌────────────────▼───────┐ │
│                              │      State Manager      │ │
│                              │  (thread-safe, in-mem)  │ │
│                              └───────────┬─────────────┘ │
│                                          │               │
│            ┌─────────────────────────────▼─────────────┐ │
│            │            Analytics Engine               │ │
│            │   metrics.py  funnel.py  anomalies.py     │ │
│            └─────────────────────────────┬─────────────┘ │
│                                          │               │
│                            ┌─────────────▼───────────┐  │
│                            │    FastAPI REST Layer    │  │
│                            │  /health /metrics        │  │
│                            │  /funnel /events         │  │
│                            │  /anomalies /process     │  │
│                            └──────────────────────────┘  │
│                                                          │
│   Prometheus :8001          Structured JSON Logs         │
└──────────────────────────────────────────────────────────┘
```

---

## 2. Detection Pipeline

### 2.1 Frame Capture
- OpenCV `VideoCapture` reads from a local video file (MP4/AVI/MOV/MKV).
- Every 2nd frame is processed (halves CPU load with minimal accuracy loss at
  typical 25 fps store footage).
- When no video is found in `data/`, the `DemoProcessor` generates realistic
  synthetic events instead, anchored to actual sales timing from the CSV.

### 2.2 Person Detection — YOLOv8n
- Class filter: only `class=0` (person) is requested.
- Confidence threshold: configurable (default 0.40).
- Returns `(xyxy, confidence, class_id)` arrays as NumPy.

### 2.3 Multi-Object Tracking — ByteTrack (via supervision)
- Converts YOLO detections into a `supervision.Detections` object.
- `ByteTrack.update_with_detections()` returns detections with stable
  `tracker_id` values across frames.
- Lost tracks are buffered for `LOST_TRACK_BUFFER` frames before removal,
  handling short occlusion events.

### 2.4 Virtual Entry Line
```
Frame (0,0) ─────────────────────────────────────────► x
     │   Camera FOV — outside store (parking / street)
     │
   ══════════════ Entry line  y = H × 0.40 ══════════════
     │
     │   Inside store (shelves, checkout)
     ▼ y
```
- Centroid crosses line top→bottom: **ENTRY / REENTRY / GROUP_ENTRY**
- Centroid crosses line bottom→top: **EXIT**
- Line position is configurable via `ENTRY_LINE_Y_FRACTION`.

---

## 3. Business Event Rules

| Event | Trigger |
|-------|---------|
| `ENTRY` | Centroid crosses line inward; no prior exit or exit > session window ago |
| `EXIT` | Centroid crosses line outward |
| `REENTRY` | Inward crossing; last exit < `SESSION_WINDOW_MINUTES` (30 min) ago |
| `STAFF` | Track has been continuously inside > `STAFF_DWELL_THRESHOLD_HOURS` (4 h) |
| `GROUP_ENTRY` | Inward crossing; ≥ `GROUP_ENTRY_MIN_SIZE` (2) others crossed inward within `GROUP_ENTRY_WINDOW_SECONDS` (2 s) |

---

## 4. Business Metrics

| Metric | Formula |
|--------|---------|
| Footfall | Count of all ENTRY + REENTRY + GROUP_ENTRY events |
| Unique Visitors | Distinct `person_id` values (session-based; 30 min window) |
| Conversion Rate | `buyer_count / unique_visitors` (buyers from sales CSV) |
| Avg Dwell Time | Mean of `exit_time − entry_time` for completed visits |
| Revisit Rate | `returning_visitors / unique_visitors` |

---

## 5. State Management

- Single in-process `StateManager` singleton — no external database.
- `threading.RLock` protects all mutable state.
- Events are stored in a circular buffer (last 20 000 events).
- Per-person tracking state: entry time, exit time, session count.
- Hourly footfall buckets for anomaly detection.

---

## 6. Anomaly Detection

Three checks run periodically (every ~5 s of video or every 12 demo ticks):

1. **FOOTFALL_SPIKE** — last completed hour's footfall > 2× rolling average.
2. **UNUSUAL_DWELL** — visitor dwell > `DWELL_TIME_ANOMALY_MINUTES` (60 min).
3. **CROWD_FORMATION** — current occupancy > `CROWD_THRESHOLD` (10).

---

## 7. API Design

All endpoints return JSON. Error responses include a `detail` field.
Prometheus metrics are scraped on a separate port (8001) to avoid auth
conflicts with the business API.

```
GET  /health       → liveness probe
GET  /metrics      → footfall, unique_visitors, conversion_rate, avg_dwell_time …
GET  /funnel       → entered, engaged, converted + rates
GET  /events       → last N events (filterable by event_type)
GET  /anomalies    → detected anomalies (optionally re-run checks)
POST /process      → upload & process a video file asynchronously
```

---

## 8. Scalability Considerations

| Concern | Current approach | Scale-out path |
|---------|-----------------|----------------|
| State | In-process dict | Redis with pub/sub |
| Events | Circular buffer | Kafka / Kinesis |
| Detection | Single thread | Multiple camera threads / GPU workers |
| API | Single uvicorn worker | Gunicorn + multiple uvicorn workers |
| Storage | Ephemeral | PostgreSQL for historical analytics |

---

## 9. Trade-offs

- **In-memory state** is sufficient for single-store operation and keeps
  the system zero-dependency (no Redis/Postgres required for `docker compose up`).
- **Frame skipping** (every 2nd frame) halves CPU cost at negligible accuracy
  loss for typical 25 fps retail footage.
- **ByteTrack via supervision** is used instead of a raw C++ tracker to keep
  the dependency stack pure Python while still achieving near-SORT-level tracking
  performance.
- **YOLOv8n** is chosen over larger variants for CPU deployability. A heavier
  variant (s/m) can be dropped in by changing `YOLO_MODEL`.
