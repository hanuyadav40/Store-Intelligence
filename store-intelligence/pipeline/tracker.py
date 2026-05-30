"""
ByteTrack wrapper.

Wraps the ultralytics `model.track()` API and normalises output
into a consistent list of `TrackedPerson` dataclasses.

YOLOv8 ByteTrack integration:
  - class 0 = person (COCO)
  - persist=True: ByteTrack maintains track state across frames
  - tracker='bytetrack.yaml': uses ByteTrack algorithm (ships with ultralytics)
"""
import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger("pipeline.tracker")


@dataclass
class TrackedPerson:
    track_id: int
    bbox_xyxy: tuple[float, float, float, float]  # absolute pixels
    confidence: float
    # Normalised foot-point (bottom-centre of bbox) in [0, 1]
    foot_nx: float
    foot_ny: float


class ByteTrackWrapper:
    """
    Thin wrapper around ultralytics YOLO to:
      1. Run inference + tracking in one call
      2. Extract only person detections (class 0)
      3. Return `TrackedPerson` objects with normalised foot coordinates
    """

    def __init__(self, model_path: str = "yolov8n.pt", device: str = "cpu"):
        from ultralytics import YOLO

        logger.info("Loading YOLO model from %s on %s", model_path, device)
        self._model = YOLO(model_path)
        self._device = device

    def process_frame(
        self,
        frame: np.ndarray,
    ) -> list[TrackedPerson]:
        """
        Run YOLOv8 + ByteTrack on a single frame.

        Parameters
        ----------
        frame: np.ndarray — BGR frame (H x W x 3)

        Returns
        -------
        List of TrackedPerson for every person detection with a valid track ID.
        """
        h, w = frame.shape[:2]

        results = self._model.track(
            frame,
            persist=True,
            tracker="bytetrack.yaml",
            classes=[0],  # person only
            device=self._device,
            verbose=False,
        )

        persons: list[TrackedPerson] = []

        if not results or results[0].boxes is None:
            return persons

        boxes = results[0].boxes
        if boxes.id is None:
            # No tracked IDs yet (ByteTrack needs a couple of frames to initialise)
            return persons

        for i in range(len(boxes)):
            track_id_tensor = boxes.id[i]
            if track_id_tensor is None:
                continue

            track_id = int(track_id_tensor.item())
            conf = float(boxes.conf[i].item())
            x1, y1, x2, y2 = boxes.xyxy[i].tolist()

            # Foot point = bottom-centre of bbox, normalised
            foot_nx = ((x1 + x2) / 2) / w
            foot_ny = y2 / h

            persons.append(
                TrackedPerson(
                    track_id=track_id,
                    bbox_xyxy=(x1, y1, x2, y2),
                    confidence=conf,
                    foot_nx=foot_nx,
                    foot_ny=foot_ny,
                )
            )

        return persons

    def extract_crop(self, frame: np.ndarray, person: TrackedPerson) -> np.ndarray:
        """
        Extract a tight crop from the frame for the given person bbox.
        Used for Re-ID feature extraction.
        """
        x1, y1, x2, y2 = (int(v) for v in person.bbox_xyxy)
        h, w = frame.shape[:2]
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(w, x2)
        y2 = min(h, y2)
        return frame[y1:y2, x1:x2].copy()
