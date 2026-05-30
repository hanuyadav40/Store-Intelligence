# Purplle Store Intelligence System

Retail analytics platform: raw CCTV footage → structured events → REST API + live dashboard.

**Stack:** Python 3.12 · FastAPI · YOLOv8 · ByteTrack · PostgreSQL 16 · Redis 7 · Docker Compose

---

## Quick Start (5 commands)

```bash
git clone <repo-url> && cd store-intelligence

# 1. Copy environment config
cp .env.example .env

# 2. Start all services (API + DB + Redis + Dashboard)
docker compose up --build

# 3. (Optional) Run detection pipeline on real CCTV footage
#    Place CAM 1.mp4 … CAM 5.mp4 in CCTV Footage/ then:
docker compose run --rm pipeline bash pipeline/run.sh

# 4. Run simulation mode (no video files needed)
docker compose run --rm pipeline bash pipeline/run.sh --simulate

# 5. Run acceptance assertions
python data/assertions.py
```

API docs auto-open at **http://localhost:8000/docs**  
Live dashboard at **http://localhost:3000**

---

## Architecture

```
CCTV Footage (5 × .mp4)
        │
        ▼
pipeline/detect.py          ← YOLOv8n + ByteTrack + ReID (HOG+colour histogram)
        │ POST /events/ingest
        ▼
FastAPI (port 8000)
   ├── POST /events/ingest   ← batch ingestion, idempotency, session management
   ├── GET  /stores/{id}/metrics
   ├── GET  /stores/{id}/funnel
   ├── GET  /stores/{id}/heatmap
   ├── GET  /stores/{id}/anomalies
   ├── GET  /health
   └── GET  /dashboard/stream/{id}  ← SSE live feed
        │
        ├── PostgreSQL 16   ← events, visitor_sessions, pos_transactions
        └── Redis 7         ← SSE pub/sub caching

Dashboard (port 3000)
   └── Chart.js real-time KPI dashboard consuming SSE stream
```

---

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/events/ingest` | Ingest batch of store events (max 500) |
| `GET`  | `/stores/{store_id}/metrics` | Real-time KPIs for a store |
| `GET`  | `/stores/{store_id}/funnel` | 4-stage conversion funnel |
| `GET`  | `/stores/{store_id}/heatmap` | Zone visit heatmap (0–100 normalised) |
| `GET`  | `/stores/{store_id}/anomalies` | Active anomalies (QUEUE_SPIKE, CONVERSION_DROP, DEAD_ZONE) |
| `GET`  | `/health` | Service health + per-store feed staleness |
| `GET`  | `/dashboard/stream/{store_id}` | SSE real-time metrics stream |

---

## Event Schema

```json
{
  "event_id":   "550e8400-e29b-41d4-a716-446655440000",  // UUID v4
  "store_id":   "STORE_BLR_001",
  "camera_id":  "CAM_BLR_001_FLOOR",
  "visitor_id": "visitor-abc123",
  "event_type": "ZONE_ENTER",   // ENTRY|EXIT|ZONE_ENTER|ZONE_EXIT|ZONE_DWELL|BILLING_QUEUE_JOIN|BILLING_QUEUE_ABANDON|REENTRY
  "timestamp":  "2026-03-03T10:15:30Z",
  "zone_id":    "SKINCARE",
  "dwell_ms":   45000,
  "is_staff":   false,
  "confidence": 0.92,
  "metadata":   {"queue_depth": 3, "sku_zone": "SKINCARE", "session_seq": 2}
}
```

---

## Detection Pipeline

```bash
# Process single video
python pipeline/detect.py \
  --video "/data/cctv/CAM 1.mp4" \
  --store STORE_BLR_001 \
  --camera CAM_BLR_001_ENTRY \
  --layout data/store_layout.json \
  --api-url http://localhost:8000

# Simulation mode (no video required)
python pipeline/detect.py \
  --simulate \
  --store STORE_BLR_001 \
  --camera CAM_BLR_001_FLOOR \
  --layout data/store_layout.json \
  --api-url http://localhost:8000 \
  --sim-visitors 30
```

---

## Running Tests

```bash
pip install -r requirements-dev.txt
pytest --cov=app --cov-report=term-missing
```

Tests use in-memory SQLite via `aiosqlite`. No Postgres or Redis required.  
Minimum coverage enforced: **70%** (see `pytest.ini`).

---

## Configuration

Key environment variables (see `.env.example` for full list):

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `postgresql+asyncpg://...` | Async DB URL (runtime) |
| `SYNC_DATABASE_URL` | `postgresql+psycopg2://...` | Sync DB URL (Alembic) |
| `REDIS_URL` | `redis://redis:6379/0` | Redis connection |
| `REID_SIMILARITY_THRESHOLD` | `0.72` | ReID cosine match threshold |
| `REENTRY_WINDOW_SECONDS` | `300` | Window to detect re-entries |
| `STAFF_TRACK_RATIO` | `0.80` | Fraction of frames for staff classification |
| `QUEUE_SPIKE_WARN_THRESHOLD` | `5` | Queue depth for WARN anomaly |
| `QUEUE_SPIKE_CRITICAL_THRESHOLD` | `10` | Queue depth for CRITICAL anomaly |

---

## Store Layout

`data/store_layout.json` defines 5 stores (STORE_BLR_001, STORE_BLR_002, STORE_MUM_001, STORE_DEL_001, STORE_HYD_001) with 3 cameras each (entry/floor/billing), zone bounding boxes (normalised 0–1), and source video file mappings.
