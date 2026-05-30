"""
Staff detector.

Heuristic: a track that is present for > STAFF_TRACK_RATIO of the total clip
frames is almost certainly a staff member (cashier, floor associate, etc.)
who stays in frame for the entire shift.

The ratio is configurable via the STAFF_TRACK_RATIO environment variable.
Default: 0.80 (track must be visible in 80%+ of all frames).
"""
import logging
import os

logger = logging.getLogger("pipeline.staff")

STAFF_TRACK_RATIO = float(os.getenv("STAFF_TRACK_RATIO", "0.80"))


class StaffDetector:
    """
    Tracks appearance counts per track_id and classifies long-duration
    tracks as staff when the clip has finished processing.

    Usage:
        detector = StaffDetector()
        for frame_idx, track_ids in enumerate(frame_track_ids):
            detector.update(frame_idx, track_ids)
        staff_ids = detector.get_staff_ids()
    """

    def __init__(self, ratio: float = STAFF_TRACK_RATIO):
        self.ratio = ratio
        self._frame_count: int = 0
        # track_id -> number of frames the track was present
        self._presence: dict[int, int] = {}

    def update(self, frame_idx: int, track_ids: list[int]) -> None:
        """Call once per processed frame with the list of active track IDs."""
        self._frame_count = frame_idx + 1
        for tid in track_ids:
            self._presence[tid] = self._presence.get(tid, 0) + 1

    def is_staff(self, track_id: int) -> bool:
        """
        True if the track was present in >= ratio * total_frames.
        Can be called incrementally during processing with current frame count.
        """
        if self._frame_count == 0:
            return False
        return self._presence.get(track_id, 0) / self._frame_count >= self.ratio

    def get_staff_ids(self) -> set[int]:
        """Return the set of all track IDs classified as staff."""
        return {
            tid
            for tid, count in self._presence.items()
            if self._frame_count > 0
            and count / self._frame_count >= self.ratio
        }

    @property
    def total_frames(self) -> int:
        return self._frame_count
