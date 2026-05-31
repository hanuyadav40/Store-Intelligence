"""
Unit tests for PersonDetector.
YOLO is mocked so no GPU / model file is required.
"""
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from detector.detector import PersonDetector


def _make_mock_results(n_persons: int = 2):
    """Fabricate a ultralytics-style result object."""
    boxes = MagicMock()
    if n_persons > 0:
        boxes.xyxy.cpu().numpy.return_value = np.array(
            [[10, 20, 50, 100]] * n_persons, dtype=np.float32
        )
        boxes.conf.cpu().numpy.return_value = np.array(
            [0.9] * n_persons, dtype=np.float32
        )
        boxes.cls.cpu().numpy.return_value = np.array(
            [0] * n_persons, dtype=np.float32
        )
    else:
        boxes = None

    result = MagicMock()
    result.boxes = boxes
    return [result]


class TestPersonDetector:
    def test_detect_returns_correct_shapes(self):
        with patch("detector.detector.YOLO") as MockYOLO:
            mock_model = MagicMock()
            mock_model.predict.return_value = _make_mock_results(3)
            MockYOLO.return_value = mock_model

            detector = PersonDetector()
            detector.load()

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            xyxy, confs, cls_ids = detector.detect(frame)

            assert xyxy.shape == (3, 4)
            assert confs.shape == (3,)
            assert cls_ids.shape == (3,)

    def test_detect_empty_frame(self):
        with patch("detector.detector.YOLO") as MockYOLO:
            mock_model = MagicMock()
            mock_model.predict.return_value = _make_mock_results(0)
            MockYOLO.return_value = mock_model

            detector = PersonDetector()
            detector.load()

            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            xyxy, confs, cls_ids = detector.detect(frame)

            assert xyxy.shape[1] == 4
            assert len(confs) == 0

    def test_is_loaded_false_before_load(self):
        with patch("detector.detector.YOLO"):
            d = PersonDetector()
            assert d.is_loaded() is False

    def test_is_loaded_true_after_load(self):
        with patch("detector.detector.YOLO") as MockYOLO:
            MockYOLO.return_value = MagicMock()
            d = PersonDetector()
            d.load()
            assert d.is_loaded() is True
