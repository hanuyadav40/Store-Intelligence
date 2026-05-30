"""
Main detection pipeline entry point.

Processes a single video file (or simulates events if --simulate flag given)
through the full pipeline:

  1. Open video file with OpenCV
  2. Per-frame: run ByteTrack wrapper (YOLOv8 + ByteTrack)
  3. Per-detection: classify zone, check staff heuristic
  4. Per-track: run Re-ID to detect re-entries
  5. EventEmitter state machine → batch POST to API

Simulation mode (--simulate):
  Generates realistic synthetic events using the zone definitions from
  store_layout.json. No video file required — useful for testing the API
  and dashboard without actual CCTV footage.

Usage:
  python detect.py --video /data/cctv/CAM_1.mp4 \
                   --store STORE_BLR_001 --camera CAM_BLR_001_ENTRY \
                   --layout /app/data/store_layout.json \
                   --api-url http://localhost:8000

  python detect.py --simulate \
                   --store STORE_BLR_001 --camera CAM_BLR_001_FLOOR \
                   --layout /app/data/store_layout.json \
                   --api-url http://localhost:8000 \
                   --sim-visitors 25 --sim-duration 120
"""
import argparse
import logging
import os
import random
import time
import uuid
from pathlib import Path

import cv2
import numpy as np

from pipeline.emit import EventEmitter
from pipeline.reid import ReIDGallery
from pipeline.staff_detector import StaffDetector
from pipeline.tracker import ByteTrackWrapper
from pipeline.zones import ZoneClassifier

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("pipeline.detect")


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------
def process_video(
    video_path: str,
    store_id: str,
    camera_id: str,
    layout_path: str,
    api_url: str,
    model_path: str = "yolov8n.pt",
    device: str = "cpu",
) -> int:
    """Process a video file. Returns total events emitted."""
    logger.info("Processing %s | store=%s camera=%s", video_path, store_id, camera_id)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video file: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logger.info("Video: %.1f fps, %d frames", fps, total_frames)

    tracker = ByteTrackWrapper(model_path=model_path, device=device)
    zone_clf = ZoneClassifier(layout_path, store_id, camera_id)
    reid_gallery = ReIDGallery()
    staff_detector = StaffDetector()
    emitter = EventEmitter(store_id, camera_id, api_url)

    # Tracks seen in current session (track_id → visitor_id)
    track_to_visitor: dict[int, str] = {}
    # Tracks that have been active this frame
    active_track_ids: set[int] = set()
    # Tracks we emitted ENTRY for
    entered: set[int] = set()

    frame_idx = 0
    process_every_n = max(1, int(fps / 5))  # process ~5 fps for speed

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break

        frame_idx += 1
        if frame_idx % process_every_n != 0:
            continue

        detections = tracker.process_frame(frame)
        current_ids = {d.track_id for d in detections}
        staff_detector.update(frame_idx, list(current_ids))

        # Detect newly appeared tracks (ENTRY or REENTRY)
        for det in detections:
            tid = det.track_id
            active_track_ids.add(tid)

            if tid not in entered:
                crop = tracker.extract_crop(frame, det)
                matched_id, sim = reid_gallery.identify(crop)
                is_reentry = matched_id is not None

                if is_reentry and matched_id not in track_to_visitor.values():
                    visitor_id = matched_id
                    reid_gallery.remove(visitor_id)
                    logger.debug(
                        "REENTRY: track %d matched visitor %s (sim=%.3f)",
                        tid, visitor_id, sim,
                    )
                else:
                    visitor_id = str(uuid.uuid4())

                track_to_visitor[tid] = visitor_id
                is_staff = staff_detector.is_staff(tid)
                emitter.on_entry(visitor_id, is_staff=is_staff, is_reentry=is_reentry)
                entered.add(tid)

            # Zone classification
            visitor_id = track_to_visitor[tid]
            zone = zone_clf.classify(det.foot_nx, det.foot_ny)
            zone_id = zone.zone_id if zone else None
            sku_zone = zone.sku_zone if zone else None
            emitter.on_zone_change(visitor_id, zone_id, sku_zone, det.confidence)
            emitter.on_dwell_tick(visitor_id, det.confidence)

        # Detect exits: tracks present last frame but gone now
        prev_ids = set(track_to_visitor.keys()) & entered
        exited_tids = prev_ids - current_ids
        for tid in exited_tids:
            visitor_id = track_to_visitor.get(tid)
            if visitor_id:
                is_staff = staff_detector.is_staff(tid)
                emitter.on_exit(visitor_id)
                # Get last crop for ReID (unavailable at exit frame; use empty array)
                reid_gallery.add_exit(visitor_id, np.zeros((1, 1, 3), dtype=np.uint8))
                entered.discard(tid)

    cap.release()

    # Mark all still-active visitors as exited at end of clip
    for tid in list(entered):
        visitor_id = track_to_visitor.get(tid)
        if visitor_id:
            emitter.on_exit(visitor_id)

    # Final staff identification pass: re-check after seeing all frames
    staff_ids_final = staff_detector.get_staff_ids()
    logger.info(
        "Clip done: %d tracks, %d staff detected, %d frames",
        len(track_to_visitor),
        len(staff_ids_final),
        frame_idx,
    )

    total = emitter.flush()
    logger.info("Pipeline complete: %d events emitted to API", total)
    return total


# ---------------------------------------------------------------------------
# Simulation mode
# ---------------------------------------------------------------------------
def simulate(
    store_id: str,
    camera_id: str,
    layout_path: str,
    api_url: str,
    n_visitors: int = 20,
    duration_seconds: int = 120,
) -> int:
    """Generate synthetic events without a real video file."""
    import json

    logger.info(
        "SIMULATE mode | store=%s camera=%s visitors=%d duration=%ds",
        store_id, camera_id, n_visitors, duration_seconds,
    )

    # Load zone names for simulation
    data = json.loads(Path(layout_path).read_text())
    store = data.get("stores", {}).get(store_id, {})
    camera = store.get("cameras", {}).get(camera_id, {})
    zone_names = list(camera.get("zones", {}).keys()) or ["FLOOR_ZONE_1"]
    billing_zones = [z for z in zone_names if "BILLING" in z.upper() or "CHECKOUT" in z.upper()]
    product_zones = [z for z in zone_names if z not in billing_zones]
    if not product_zones:
        product_zones = zone_names[:max(1, len(zone_names) - 1)]

    emitter = EventEmitter(store_id, camera_id, api_url)
    staff_ids = {str(uuid.uuid4()) for _ in range(2)}

    # Emit staff entries
    for sid in staff_ids:
        emitter.on_entry(sid, is_staff=True)

    visitors: list[str] = []
    for _ in range(n_visitors):
        vid = str(uuid.uuid4())
        visitors.append(vid)
        emitter.on_entry(vid, is_staff=False)

        # Visit 1-3 product zones
        for zone_id in random.sample(product_zones, min(random.randint(1, 3), len(product_zones))):
            emitter.on_zone_change(vid, zone_id, zone_id)
            time.sleep(0.01)  # simulate time passing

        # ~60% reach billing
        if billing_zones and random.random() < 0.6:
            bzone = random.choice(billing_zones)
            emitter.on_zone_change(vid, bzone, None)

        emitter.on_exit(vid)

    total = emitter.flush()
    logger.info("Simulation complete: %d events emitted", total)
    return total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Store Intelligence Detection Pipeline")
    parser.add_argument("--video", help="Path to input video file")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode")
    parser.add_argument("--store", required=True, help="Store ID")
    parser.add_argument("--camera", required=True, help="Camera ID")
    parser.add_argument(
        "--layout",
        default=os.getenv("STORE_LAYOUT_PATH", "/app/data/store_layout.json"),
        help="Path to store_layout.json",
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("API_BASE_URL", "http://localhost:8000"),
        help="Store Intelligence API base URL",
    )
    parser.add_argument("--model", default="yolov8n.pt", help="YOLO model weights path")
    parser.add_argument("--device", default="cpu", help="torch device (cpu or cuda:0)")
    parser.add_argument("--sim-visitors", type=int, default=20)
    parser.add_argument("--sim-duration", type=int, default=120)

    args = parser.parse_args()

    if args.simulate:
        simulate(
            store_id=args.store,
            camera_id=args.camera,
            layout_path=args.layout,
            api_url=args.api_url,
            n_visitors=args.sim_visitors,
            duration_seconds=args.sim_duration,
        )
    else:
        if not args.video:
            parser.error("--video is required unless --simulate is set")
        if not Path(args.video).exists():
            raise FileNotFoundError(f"Video file not found: {args.video}")
        process_video(
            video_path=args.video,
            store_id=args.store,
            camera_id=args.camera,
            layout_path=args.layout,
            api_url=args.api_url,
            model_path=args.model,
            device=args.device,
        )


if __name__ == "__main__":
    main()
