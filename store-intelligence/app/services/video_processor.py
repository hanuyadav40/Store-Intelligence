"""
Video processor service.

Modes:
  1. VideoFileProcessor – reads a video file through the full CV pipeline
  2. DemoProcessor      – generates realistic synthetic events when no video is available

Both run in a background daemon thread and push events into StateManager.
"""
from __future__ import annotations

import os
import threading
import time
import random
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from app.models.event_model import EventType, PersonEvent
from app.services.state_manager import state_manager
from app.utils.config import settings
from app.utils.logging_config import get_logger
from analytics.anomalies import check_dwell_anomaly, run_all_checks

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _push_event(event: PersonEvent) -> None:
    state_manager.add_event(event)


def _exit_dwell_callback(person_id: int, exit_time: datetime) -> None:
    """Called by EventGenerator on EXIT; records dwell and checks anomaly."""
    state_manager.record_exit_dwell(person_id, exit_time)
    # Re-read dwell from records
    snap = state_manager.get_metrics_snapshot()
    avg = snap["avg_dwell_time"]
    if avg > settings.DWELL_TIME_ANOMALY_MINUTES * 60:
        alert = check_dwell_anomaly(person_id, avg)
        if alert:
            state_manager.add_anomaly(alert)


# ---------------------------------------------------------------------------
# Real video processor
# ---------------------------------------------------------------------------

class VideoFileProcessor:
    """Processes a video file using YOLOv8 + ByteTrack + EventGenerator."""

    def __init__(self, video_path: str) -> None:
        self._path = video_path
        # Derive camera_id from filename, e.g. "CAM 1.mp4" -> "cam_1"
        stem = os.path.splitext(os.path.basename(video_path))[0]
        self._camera_id = stem.lower().replace(" ", "_")
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("video_processor_started", path=self._path)

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        import cv2  # type: ignore

        from detector.detector import PersonDetector
        from detector.tracker import PersonTracker
        from detector.event_generator import EventGenerator

        detector = PersonDetector()
        detector.load()

        tracker = PersonTracker()
        tracker.load()

        generator = EventGenerator(camera_id=self._camera_id)
        generator.set_exit_dwell_callback(_exit_dwell_callback)

        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            logger.error("video_open_failed", path=self._path)
            return

        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        generator.set_frame_size(frame_w, frame_h)

        logger.info("video_opened", fps=fps, width=frame_w, height=frame_h)

        frame_index = 0
        start_ts = datetime.utcnow()

        while not self._stop.is_set():
            ret, frame = cap.read()
            if not ret:
                logger.info("video_end_reached", path=self._path)
                break

            frame_index += 1
            # Skip frames to match real-time speed (process every 2nd frame)
            if frame_index % 2 != 0:
                continue

            frame_ts = start_ts + timedelta(seconds=frame_index / fps)

            try:
                xyxy, confs, cls_ids = detector.detect(frame)
                t_xyxy, t_confs, t_ids = tracker.update(xyxy, confs, cls_ids)
                events = generator.process_tracks(t_xyxy, t_confs, t_ids, frame_ts)
                for ev in events:
                    _push_event(ev)
                    logger.debug("event_generated", event_type=ev.event_type, pid=ev.person_id)
            except Exception as exc:
                logger.error("frame_processing_error", frame=frame_index, error=str(exc))
                continue

            # Periodic anomaly check every ~5 seconds of video time
            if frame_index % int(fps * 5) == 0:
                run_all_checks()

        cap.release()
        logger.info("video_processor_finished", path=self._path)


# ---------------------------------------------------------------------------
# Demo / synthetic event generator
# ---------------------------------------------------------------------------

_DEMO_SCENARIOS = [
    # (event_type, weight)  — weighted random selection
    (EventType.ENTRY, 40),
    (EventType.EXIT, 30),
    (EventType.REENTRY, 10),
    (EventType.GROUP_ENTRY, 10),
    (EventType.STAFF, 5),
]
_SCENARIO_POOL = [etype for etype, w in _DEMO_SCENARIOS for _ in range(w)]


class DemoProcessor:
    """
    Generates realistic synthetic store events.

    Uses a simple state machine:
      - A pool of "active" track IDs (people currently in the store)
      - Each tick, some people enter, some exit
      - Peak hours have higher entry rates
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._next_id = 1
        self._active: dict = {}   # id → entry_datetime
        self._exited: dict = {}   # id → exit_datetime

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("demo_processor_started")

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        interval = settings.DEMO_INTERVAL_SECONDS
        tick = 0

        while not self._stop.is_set():
            now = datetime.utcnow()
            hour = now.hour

            # Higher rate during peak hours (11-21)
            if 11 <= hour <= 21:
                n_entries = random.randint(1, 3)
            else:
                n_entries = random.randint(0, 1)

            for _ in range(n_entries):
                self._simulate_entry(now)

            # Some active people exit
            to_exit = [
                pid for pid, et in list(self._active.items())
                if (now - et).total_seconds() > random.uniform(120, 1800)
            ]
            for pid in to_exit[:2]:   # cap at 2 exits per tick
                self._simulate_exit(pid, now)

            # Periodic anomaly check
            if tick % 12 == 0:
                run_all_checks()

            tick += 1
            time.sleep(interval)

        logger.info("demo_processor_stopped")

    def _new_id(self) -> int:
        pid = self._next_id
        self._next_id += 1
        return pid

    def _simulate_entry(self, now: datetime) -> None:
        # Decide if this is a re-entering visitor or fresh
        if self._exited and random.random() < 0.15:
            pid = random.choice(list(self._exited.keys()))
            last_exit = self._exited[pid]
            gap_minutes = (now - last_exit).total_seconds() / 60
            event_type = (
                EventType.REENTRY
                if gap_minutes < settings.SESSION_WINDOW_MINUTES
                else EventType.ENTRY
            )
        else:
            pid = self._new_id()
            event_type = EventType.ENTRY

        # Group entry: occasionally add another person at same tick
        if random.random() < 0.12:
            companion = self._new_id()
            self._active[companion] = now
            _push_event(
                PersonEvent(
                    person_id=companion,
                    timestamp=now,
                    event_type=EventType.GROUP_ENTRY,
                    confidence=round(random.uniform(0.7, 0.99), 2),
                    camera_id=settings.CAMERA_ID,
                )
            )

        self._active[pid] = now
        _push_event(
            PersonEvent(
                person_id=pid,
                timestamp=now,
                event_type=event_type,
                confidence=round(random.uniform(0.7, 0.99), 2),
                camera_id=settings.CAMERA_ID,
            )
        )

    def _simulate_exit(self, pid: int, now: datetime) -> None:
        entry_time = self._active.pop(pid, now)
        dwell = (now - entry_time).total_seconds()
        self._exited[pid] = now

        state_manager.record_exit_dwell(pid, now)

        _push_event(
            PersonEvent(
                person_id=pid,
                timestamp=now,
                event_type=EventType.EXIT,
                confidence=round(random.uniform(0.7, 0.99), 2),
                camera_id=settings.CAMERA_ID,
            )
        )

        # Dwell anomaly check
        alert = check_dwell_anomaly(pid, dwell)
        if alert:
            state_manager.add_anomaly(alert)


# ---------------------------------------------------------------------------
# Orchestrator – picks the right processor at startup
# ---------------------------------------------------------------------------

class ProcessorOrchestrator:
    """
    Decides whether to use VideoFileProcessor or DemoProcessor.
    Recursively scans DATA_DIR for video files; starts one processor per
    camera feed in parallel threads.  Falls back to DemoProcessor when no
    videos are found.
    """

    VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

    def __init__(self) -> None:
        self._processors: list = []

    def start(self) -> None:
        video_paths = self._find_all_videos()
        if video_paths:
            logger.info("using_video_processors", count=len(video_paths), paths=video_paths)
            for path in video_paths:
                proc = VideoFileProcessor(path)
                self._processors.append(proc)
                proc.start()
        else:
            logger.info("no_video_found_using_demo_mode")
            demo = DemoProcessor()
            self._processors.append(demo)
            demo.start()

    def stop(self) -> None:
        for proc in self._processors:
            proc.stop()

    def _find_all_videos(self) -> list:
        """Recursively find all video files under DATA_DIR, sorted by name."""
        data_dir = settings.DATA_DIR
        found = []
        if not os.path.isdir(data_dir):
            return found
        for root, _dirs, files in os.walk(data_dir):
            for fname in sorted(files):
                ext = os.path.splitext(fname)[1].lower()
                if ext in self.VIDEO_EXTENSIONS:
                    found.append(os.path.join(root, fname))
        return sorted(found)


# Module-level singleton
orchestrator = ProcessorOrchestrator()
