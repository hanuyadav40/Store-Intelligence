"""
Re-Identification gallery.

Uses HOG features + colour histograms for appearance matching.
No GPU required — runs on CPU in O(gallery_size) per lookup.

Each track that exits the store is added to the gallery with a TTL.
When a new track appears, we compare it against the gallery;
if cosine similarity exceeds THRESHOLD, the visitor is treated as a REENTRY.

Gallery entry pruning: entries older than REENTRY_WINDOW_SECONDS are removed
on each insert/lookup to keep memory bounded.
"""
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger("pipeline.reid")

REID_THRESHOLD = float(os.getenv("REID_SIMILARITY_THRESHOLD", "0.72"))
REENTRY_WINDOW = int(os.getenv("REENTRY_WINDOW_SECONDS", "300"))

_HOG = cv2.HOGDescriptor(
    _winSize=(64, 128),
    _blockSize=(16, 16),
    _blockStride=(8, 8),
    _cellSize=(8, 8),
    _nbins=9,
)


def _extract_features(crop: np.ndarray) -> np.ndarray:
    """
    Concatenate HOG descriptor + RGB colour histogram (32 bins per channel).
    Returns a 1-D float32 feature vector.
    """
    if crop is None or crop.size == 0:
        return np.zeros(1, dtype=np.float32)

    # Resize to fixed HOG window size
    resized = cv2.resize(crop, (64, 128))

    # HOG on grayscale
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    hog_feat = _HOG.compute(gray).flatten()

    # Colour histogram (RGB)
    color_feat = np.concatenate(
        [
            cv2.calcHist([resized], [c], None, [32], [0, 256]).flatten()
            for c in range(3)
        ]
    )

    feat = np.concatenate([hog_feat, color_feat]).astype(np.float32)
    norm = np.linalg.norm(feat)
    if norm > 0:
        feat /= norm
    return feat


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape or a.size == 0:
        return 0.0
    return float(np.dot(a, b))  # Both already L2-normalised


@dataclass
class GalleryEntry:
    visitor_id: str
    features: np.ndarray
    exit_time: float = field(default_factory=time.monotonic)


class ReIDGallery:
    """
    Maintains a gallery of recently-exited visitors.
    Thread-safe for single-threaded pipeline use (no locks needed).
    """

    def __init__(
        self,
        threshold: float = REID_THRESHOLD,
        reentry_window: int = REENTRY_WINDOW,
    ):
        self.threshold = threshold
        self.reentry_window = reentry_window
        self._gallery: list[GalleryEntry] = []

    # ------------------------------------------------------------------
    def add_exit(self, visitor_id: str, crop: np.ndarray) -> None:
        """
        Record appearance features for an exiting visitor.
        Called when an EXIT event is emitted for a track.
        """
        self._prune()
        features = _extract_features(crop)
        # Replace existing entry if visitor_id already in gallery
        self._gallery = [e for e in self._gallery if e.visitor_id != visitor_id]
        self._gallery.append(GalleryEntry(visitor_id=visitor_id, features=features))
        logger.debug("Added %s to ReID gallery (%d entries)", visitor_id, len(self._gallery))

    def identify(
        self, crop: np.ndarray
    ) -> tuple[Optional[str], float]:
        """
        Try to match a new detection crop against the exit gallery.

        Returns (visitor_id, similarity) if a match above threshold is found,
        else (None, 0.0).
        """
        self._prune()
        if not self._gallery:
            return None, 0.0

        features = _extract_features(crop)
        best_id: Optional[str] = None
        best_sim = 0.0

        for entry in self._gallery:
            sim = _cosine_similarity(features, entry.features)
            if sim > best_sim:
                best_sim = sim
                best_id = entry.visitor_id

        if best_sim >= self.threshold:
            logger.debug(
                "ReID match: %s (sim=%.3f, threshold=%.3f)",
                best_id,
                best_sim,
                self.threshold,
            )
            return best_id, best_sim

        return None, best_sim

    def remove(self, visitor_id: str) -> None:
        """Remove an entry from the gallery (e.g., after confirmed reentry)."""
        self._gallery = [e for e in self._gallery if e.visitor_id != visitor_id]

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.reentry_window
        before = len(self._gallery)
        self._gallery = [e for e in self._gallery if e.exit_time >= cutoff]
        pruned = before - len(self._gallery)
        if pruned:
            logger.debug("Pruned %d stale ReID gallery entries", pruned)

    @property
    def size(self) -> int:
        return len(self._gallery)
