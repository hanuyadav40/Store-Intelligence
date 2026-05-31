# Store Intelligence — README

## Quick Start

```bash
# 1. Clone / open the project
cd store-intelligence

# 2. (Optional) Place video footage in data/
#    e.g.  cp /path/to/store.mp4 data/
#    e.g.  cp /path/to/sales.csv data/sales.csv

# 3. Single command to build and run
docker compose up --build
```

The API is available at **http://localhost:8000**
Prometheus metrics scrape endpoint: **http://localhost:8001**

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Liveness probe |
| `/metrics` | GET | Store business metrics |
| `/funnel` | GET | Customer journey funnel |
| `/events` | GET | Event stream (supports `?limit=N&event_type=ENTRY`) |
| `/anomalies` | GET | Detected anomaly alerts |
| `/process` | POST | Upload and process a video file |
| `/docs` | GET | Auto-generated OpenAPI UI |

### Sample responses

**GET /metrics**
```json
{
  "footfall": 142,
  "unique_visitors": 119,
  "conversion_rate": 0.21,
  "avg_dwell_time": 384.5,
  "current_occupancy": 7,
  "total_exits": 135,
  "revisit_rate": 0.09,
  "staff_count": 3,
  "timestamp": "2026-04-10T17:30:00"
}
```

**GET /funnel**
```json
{
  "entered": 119,
  "engaged": 87,
  "converted": 25,
  "engagement_rate": 0.73,
  "conversion_rate": 0.21
}
```

**GET /events?limit=3**
```json
[
  {"event_id": "...", "person_id": 42, "event_type": "ENTRY",  "confidence": 0.91, "camera_id": "cam_01", "timestamp": "..."},
  {"event_id": "...", "person_id": 43, "event_type": "GROUP_ENTRY", "confidence": 0.88, "camera_id": "cam_01", "timestamp": "..."},
  {"event_id": "...", "person_id": 41, "event_type": "EXIT",   "confidence": 0.94, "camera_id": "cam_01", "timestamp": "..."}
]
```

---

## Local Development (without Docker)

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Run the API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Run tests
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## Configuration

All settings are controlled via environment variables (see `.env.example`).

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_MODE` | `true` | Use synthetic data when no video found |
| `DATA_DIR` | `/app/data` | Directory scanned for video files |
| `SALES_CSV_PATH` | `/app/data/sales.csv` | Sales data for conversion rate |
| `YOLO_MODEL` | `yolov8n.pt` | YOLO model variant |
| `CONFIDENCE_THRESHOLD` | `0.4` | Detection confidence floor |
| `SESSION_WINDOW_MINUTES` | `30` | Unique-visitor session window |
| `ENTRY_LINE_Y_FRACTION` | `0.4` | Virtual entry line position |
| `CROWD_THRESHOLD` | `10` | Occupancy threshold for crowd alert |

---

## Adding Real Video

1. Drop a `.mp4` / `.avi` file into `data/`
2. Optionally copy `sales.csv` into `data/` for real conversion metrics
3. `docker compose up --build`

The system auto-detects the video and switches from demo to real-video mode.

---

## Project Structure

```
store-intelligence/
├── app/
│   ├── main.py                # FastAPI app + startup
│   ├── api/routes.py          # All endpoints
│   ├── models/                # Pydantic schemas
│   ├── services/
│   │   ├── state_manager.py   # Thread-safe event/metric store
│   │   └── video_processor.py # Video + demo pipeline orchestration
│   └── utils/                 # Config + structured logging
├── detector/
│   ├── detector.py            # YOLOv8n wrapper
│   ├── tracker.py             # ByteTrack wrapper
│   └── event_generator.py     # Virtual line + business rules
├── analytics/
│   ├── metrics.py             # Business metrics calculator
│   ├── funnel.py              # Customer journey funnel
│   └── anomalies.py           # Spike / dwell / crowd detection
├── tests/                     # pytest unit + integration + edge-case tests
├── data/                      # Mount point for videos and CSVs
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── DESIGN.md
└── CHOICES.md
```
