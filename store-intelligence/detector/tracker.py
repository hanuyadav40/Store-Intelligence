"""
ByteTrack wrapper via the supervision library.
Converts raw YOLO detections into tracked detections with persistent track IDs.
"""
from __future__ import annotations

from typing import Optional, Tuple
import numpy as np

from app.utils.config import settings
from app.utils.logging_config import get_logger

logger = get_logger(__name__)


class PersonTracker:
    """Thin wrapper around supervision.ByteTrack."""

    def __init__(
        self,
        track_activation_threshold: float = settings.TRACK_ACTIVATION_THRESHOLD,
        lost_track_buffer: int = settings.LOST_TRACK_BUFFER,
        minimum_matching_threshold: float = settings.MINIMUM_MATCHING_THRESHOLD,
        frame_rate: int = settings.FRAME_RATE,
    ) -> None:
        self._threshold = track_activation_threshold
        self._buffer = lost_track_buffer
        self._match_thresh = minimum_matching_threshold
        self._fps = frame_rate
        self._tracker = None

    def load(self) -> None:
        """Initialise ByteTrack (call once at startup)."""
        import supervision as sv  # type: ignore

        self._tracker = sv.ByteTrack(
            track_activation_threshold=self._threshold,
            lost_track_buffer=self._buffer,
            minimum_matching_threshold=self._match_thresh,
            frame_rate=self._fps,
        )
        logger.info("bytetrack_initialised")

    def update(
        self,
        xyxy: np.ndarray,
        confidences: np.ndarray,
        class_ids: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Parameters
        ----------
        xyxy        : (N, 4) float32
        confidences : (N,)   float32
        class_ids   : (N,)   int32

        Returns
        -------
        tracked_xyxy  : (M, 4)
        tracked_confs : (M,)
        track_ids     : (M,)  int  — stable across frames
        """
        if self._tracker is None:
            self.load()

        import supervision as sv  # type: ignore

        if len(xyxy) == 0:
            empty4 = np.empty((0, 4), dtype=np.float32)
            empty1 = np.empty((0,), dtype=np.float32)
            empty_id = np.empty((0,), dtype=np.int32)
            return empty4, empty1, empty_id

        detections = sv.Detections(
            xyxy=xyxy,
            confidence=confidences,
            class_id=class_ids,
        )

        tracked = self._tracker.update_with_detections(detections)

        if tracked.tracker_id is None or len(tracked.tracker_id) == 0:
            empty4 = np.empty((0, 4), dtype=np.float32)
            empty1 = np.empty((0,), dtype=np.float32)
            empty_id = np.empty((0,), dtype=np.int32)
            return empty4, empty1, empty_id

        return (
            tracked.xyxy.astype(np.float32),
            tracked.confidence.astype(np.float32),
            tracked.tracker_id.astype(np.int32),
        )

    def reset(self) -> None:
        """Reset tracker state (e.g., when switching video sources)."""
        if self._tracker is not None:
            import supervision as sv  # type: ignore

            self._tracker = sv.ByteTrack(
                track_activation_threshold=self._threshold,
                lost_track_buffer=self._buffer,
                minimum_matching_threshold=self._match_thresh,
                frame_rate=self._fps,
            )
            logger.info("bytetrack_reset")
