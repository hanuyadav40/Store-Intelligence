#!/usr/bin/env bash
# run.sh — Process all CCTV clips through the detection pipeline.
#
# Maps each video file to its store_id and camera_id using store_layout.json.
# Expects video files at /data/cctv/ named exactly as the source_file field
# in store_layout.json (e.g., "CAM 1.mp4").
#
# Usage:
#   ./run.sh                          # process all clips
#   ./run.sh --simulate               # simulation mode (no video needed)
#   API_BASE_URL=http://api:8000 ./run.sh
#
# Environment variables:
#   API_BASE_URL          Default: http://localhost:8000
#   CCTV_DIR              Default: /data/cctv
#   STORE_LAYOUT_PATH     Default: /app/data/store_layout.json
#   YOLO_MODEL            Default: yolov8n.pt

set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
CCTV_DIR="${CCTV_DIR:-/data/cctv}"
LAYOUT="${STORE_LAYOUT_PATH:-/app/data/store_layout.json}"
MODEL="${YOLO_MODEL:-yolov8n.pt}"
SIMULATE="${1:-}"

# Wait for API to be healthy before starting
echo "[run.sh] Waiting for API at ${API_BASE_URL}/health ..."
for i in $(seq 1 30); do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_BASE_URL}/health" || true)
  if [ "$STATUS" = "200" ]; then
    echo "[run.sh] API is ready."
    break
  fi
  echo "[run.sh] Attempt $i/30: API not ready (HTTP $STATUS), retrying in 5s..."
  sleep 5
done

if [ "$SIMULATE" = "--simulate" ]; then
  echo "[run.sh] Running in SIMULATION mode"
  # Process each store/camera combination from the layout
  python -c "
import json, sys
data = json.load(open('${LAYOUT}'))
for store_id, store in data['stores'].items():
    for cam_id in store['cameras']:
        print(store_id, cam_id)
" | while read -r store_id cam_id; do
    echo "[run.sh] Simulate: ${store_id} / ${cam_id}"
    python pipeline/detect.py \
      --simulate \
      --store "${store_id}" \
      --camera "${cam_id}" \
      --layout "${LAYOUT}" \
      --api-url "${API_BASE_URL}" \
      --sim-visitors 25 \
      --sim-duration 120
  done
  echo "[run.sh] Simulation complete."
  exit 0
fi

# Real video mode: iterate over source_file references in store_layout.json
python -c "
import json
data = json.load(open('${LAYOUT}'))
for store_id, store in data['stores'].items():
    for cam in store['cameras']:
        src = cam.get('source_file', '')
        cam_id = cam.get('camera_id', '')
        if src and cam_id:
            print(store_id, cam_id, src)
" | while read -r store_id cam_id source_file; do
  video_path="${CCTV_DIR}/${source_file}"
  if [ ! -f "${video_path}" ]; then
    echo "[run.sh] WARNING: Video not found: ${video_path} — skipping"
    continue
  fi
  echo "[run.sh] Processing: ${video_path} | store=${store_id} camera=${cam_id}"
  python pipeline/detect.py \
    --video "${video_path}" \
    --store "${store_id}" \
    --camera "${cam_id}" \
    --layout "${LAYOUT}" \
    --api-url "${API_BASE_URL}" \
    --model "${MODEL}"
done

echo "[run.sh] All clips processed."
