"""
YOLOv8n person detector wrapper.
Returns bounding boxes, confidences, and class IDs for detected persons.
"""
from __future__ import annotations

from typing import Tuple, Optional
import numpy as np
import cv2

from app.utils.config import settings
from app.utils.logging_config import get_logger

try:
    from ultralytics import YOLO  # type: ignore
except ImportError:  # pragma: no cover
    YOLO = None  # type: ignore

logger = get_logger(__name__)


class PersonDetector:
    """Wraps YOLOv8n to detect persons (class 0) in a single frame."""

    def __init__(
        self,
        model_path: str = settings.YOLO_MODEL,
        confidence: float = settings.CONFIDENCE_THRESHOLD,
        iou: float = settings.IOU_THRESHOLD,
        device: str = "cpu",
    ) -> None:
        self.confidence = confidence
        self.iou = iou
        self.device = device
        self._model = None
        self._model_path = model_path

    def load(self) -> None:
        """Lazy-load the YOLO model (call once at startup)."""
        try:
            if YOLO is None:  # pragma: no cover
                raise ImportError("ultralytics is not installed")
            self._model = YOLO(self._model_path)
            self._model.to(self.device)
            logger.info("yolo_model_loaded", model=self._model_path, device=self.device)
        except Exception as exc:
            logger.error("yolo_load_failed", error=str(exc))
            raise

    def detect(
        self, frame: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Run inference on a single BGR frame.

        Returns
        -------
        xyxy        : (N, 4) float32 — absolute pixel coords [x1, y1, x2, y2]
        confidences : (N,)   float32
        class_ids   : (N,)   int32
        """
        if self._model is None:
            self.load()

        results = self._model.predict(
            source=frame,
            classes=[0],          # person only
            conf=self.confidence,
            iou=self.iou,
            verbose=False,
            device=self.device,
        )

        boxes = results[0].boxes
        if boxes is None:
            empty = np.empty((0, 4), dtype=np.float32)
            return empty, np.empty((0,), dtype=np.float32), np.empty((0,), dtype=np.int32)

        xyxy = boxes.xyxy.cpu().numpy().astype(np.float32)
        if len(xyxy) == 0:
            return xyxy, np.empty((0,), dtype=np.float32), np.empty((0,), dtype=np.int32)
        confs = boxes.conf.cpu().numpy().astype(np.float32)
        cls_ids = boxes.cls.cpu().numpy().astype(np.int32)
        return xyxy, confs, cls_ids

    def is_loaded(self) -> bool:
        return self._model is not None
