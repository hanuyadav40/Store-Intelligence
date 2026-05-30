# System Design — Store Intelligence Platform

## Overview

The platform transforms raw CCTV footage into real-time retail analytics via a four-layer pipeline: **Detection → Tracking → Re-ID → Event Emission → API → Dashboard**.

---

## Architecture Layers

### Layer 1: Detection & Tracking (pipeline/)

**YOLOv8n** is run on each video frame at ~5 fps (1-in-N frame sampling for throughput). Person detections (COCO class 0) are fed directly into **ByteTrack** via the ultralytics `model.track(persist=True, tracker='bytetrack.yaml')` API. ByteTrack maintains track IDs across frames using Kalman filter prediction + IoU assignment, handling occlusions and momentary disappearances.

Frame-rate subsampling is configurable — the pipeline processes every `fps/5`-th frame, giving ~5 effective fps regardless of input frame rate.

### Layer 2: Re-Identification (pipeline/reid.py)

When a track exits the scene (track_id disappears), appearance features are extracted and stored in an in-memory gallery with a TTL equal to `REENTRY_WINDOW_SECONDS`. For each new track, cosine similarity against the gallery determines if this is a returning visitor.

Feature vector: **HOG descriptor** (9 bins, 64×128 window) concatenated with **RGB colour histogram** (32 bins × 3 channels = 96 dims). Both halves are L2-normalised before concatenation. This requires no GPU and runs on commodity hardware.

### Layer 3: Event Emission (pipeline/emit.py)

A per-visitor state machine converts raw detection frames into typed store events. The machine tracks: current zone, zone entry time, dwell timer, billing entry, session sequence number. Zone changes trigger ZONE_ENTER + ZONE_EXIT + ZONE_DWELL events. Billing zone entry triggers BILLING_QUEUE_JOIN (with live queue count).

Events are buffered in memory and batch-POSTed to the API in groups of 200 to minimise HTTP round-trips.

### Layer 4: API (app/)

FastAPI async service with four business modules:

- **ingestion.py** — batch event ingestion with SELECT-before-INSERT idempotency
- **metrics.py** — real-time KPI computation via async SQLAlchemy queries
- **funnel.py** — 4-stage conversion funnel with data_confidence flag
- **heatmap.py** — zone visit frequency normalised to 0–100
- **anomalies.py** — three anomaly detectors (queue spike, conversion drop, dead zone)
- **health.py** — DB/Redis health + per-store feed staleness

### Layer 5: Dashboard

Single-page application (Chart.js) connected to the API via **Server-Sent Events**. The API emits a metrics tick every 15 seconds over `/dashboard/stream/{store_id}`. A fallback polling interval (30s) handles SSE reconnections transparently.

---

## Database Schema

Three tables managed by Alembic migrations:

```
events              — raw event log (composite index on store_id + timestamp)
visitor_sessions    — session lifecycle (billing_entry_time for POS correlation)
pos_transactions    — POS data loaded from CSV at startup
```

**Session management** is handled entirely in `ingestion.py`: ENTRY opens a session, EXIT closes it and triggers POS correlation within a 5-minute time window. REENTRY increments `reentry_count` and opens a new session for the same `visitor_id`.

---

## AI-Assisted Decisions

### 1. Re-ID feature design

The initial plan was to use a pretrained ReID neural network (e.g., OSNet). **AI suggestion:** Use HOG + colour histogram instead — no GPU required, no model download, runs in-process. The cosine similarity threshold of 0.72 was suggested based on empirical analysis of retail CCTV characteristics (large clothing variety, consistent lighting). This avoids a significant deployment dependency while achieving acceptable recall for the re-entry detection use case.

**Adopted as-is.** The threshold is configurable via `REID_SIMILARITY_THRESHOLD` for tuning per-store.

### 2. Idempotency strategy

Two options were considered: `INSERT ... ON CONFLICT DO NOTHING` vs SELECT-before-INSERT. **AI suggestion:** Use SELECT-before-INSERT so the API can differentiate between _new duplicate_ (race condition) and _known duplicate_ (client retry). The `IngestResponse` therefore returns separate `duplicate` and `rejected` counts, giving the pipeline operator clear observability into replay behaviour.

### 3. Staff detection heuristic

Options considered: dedicated classifier, uniform colour detection (retail staff uniforms), presence duration. **AI suggestion:** Duration heuristic — a track present in >80% of clip frames is classified as staff. This is zero-labelled-data, zero-model, and still precise in retail CCTV where customers typically dwell 2–20 minutes while staff are present for entire shifts. The ratio is configurable via `STAFF_TRACK_RATIO`.
