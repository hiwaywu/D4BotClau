"""Optional YOLO object detector for things colour rules cannot separate.

The model is trained offline (tools/yolo_train.py, Ultralytics) and exported
to ONNX; at run time it is loaded with OpenCV's DNN module (CPU), so playing
needs no torch/ultralytics install. If onnxruntime is installed (on Windows:
`pip install onnxruntime-directml` for any DirectX 12 GPU) it is used instead
and runs on the GPU. Off unless `detector.enabled` and the model file exists.
See docs/YOLO.md for the collect -> label -> train loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import DetectorConfig

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]  # x, y, w, h in frame pixels

    @property
    def center(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return x + w // 2, y + h // 2


def letterbox(image: np.ndarray, size: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize keeping aspect ratio and pad to size x size (Ultralytics style, grey 114)."""
    h, w = image.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized
    return canvas, scale, (pad_x, pad_y)


def decode(
    output: np.ndarray,
    labels: list[str],
    scale: float,
    pad: tuple[int, int],
    frame_size: tuple[int, int],
    confidence: float,
    iou: float,
) -> list[Detection]:
    """Turn a YOLOv8/YOLO11 detect head output (1, 4 + classes, N) into detections."""
    out = np.squeeze(output, axis=0)
    if out.shape[0] != 4 + len(labels) and out.shape[1] == 4 + len(labels):
        out = out.T
    if out.shape[0] != 4 + len(labels):
        raise ValueError(f"model has {out.shape[0] - 4} classes, config lists {len(labels)}")
    scores = out[4:]
    class_ids = scores.argmax(axis=0)
    best = scores[class_ids, np.arange(scores.shape[1])]
    keep = best >= confidence
    if not keep.any():
        return []
    cx, cy, bw, bh = out[0, keep], out[1, keep], out[2, keep], out[3, keep]
    best, class_ids = best[keep], class_ids[keep]
    fw, fh = frame_size
    x = np.clip((cx - bw / 2 - pad[0]) / scale, 0, fw - 1)
    y = np.clip((cy - bh / 2 - pad[1]) / scale, 0, fh - 1)
    w = np.clip(bw / scale, 1, fw - x)
    h = np.clip(bh / scale, 1, fh - y)
    boxes = [[int(a), int(b), int(c), int(d)] for a, b, c, d in zip(x, y, w, h)]
    detections: list[Detection] = []
    # Per-class NMS: a monster and its loot drop may overlap.
    for cls in np.unique(class_ids):
        idx = np.flatnonzero(class_ids == cls)
        kept = cv2.dnn.NMSBoxes([boxes[i] for i in idx], best[idx].tolist(), confidence, iou)
        for k in np.array(kept).reshape(-1):
            i = idx[int(k)]
            detections.append(Detection(labels[int(cls)], float(best[i]), tuple(boxes[i])))  # type: ignore[arg-type]
    return sorted(detections, key=lambda d: d.confidence, reverse=True)


def load_labels(model_path: Path, fallback: list[str]) -> list[str]:
    """Class names from `<model>.classes.txt` (written by tools/yolo_train.py), else config."""
    sidecar = model_path.with_suffix(".classes.txt")
    if sidecar.exists():
        names = [line.strip() for line in sidecar.read_text(encoding="utf-8").splitlines() if line.strip()]
        if names:
            return names
    return list(fallback)


class Detector:
    def __init__(self, config: DetectorConfig, project_root: Path):
        self.config = config
        self.model_path = Path(config.model)
        if not self.model_path.is_absolute():
            self.model_path = project_root / self.model_path
        self.labels = load_labels(self.model_path, list(config.classes))
        self.backend = "opencv"
        self._session = None
        self.net = None
        if config.backend in {"auto", "onnxruntime"}:
            self._session = self._onnxruntime_session(config.backend == "onnxruntime")
        if self._session is None:
            self.net = cv2.dnn.readNetFromONNX(str(self.model_path))
        self._frame = 0
        self._last: list[Detection] = []

    def _onnxruntime_session(self, required: bool):  # type: ignore[no-untyped-def]
        try:
            import onnxruntime as ort  # type: ignore[import-not-found]
        except ImportError:
            if required:
                raise
            return None
        available = ort.get_available_providers()
        preferred = ["DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
        providers = [p for p in preferred if p in available] or available
        session = ort.InferenceSession(str(self.model_path), providers=providers)
        self.backend = f"onnxruntime ({session.get_providers()[0]})"
        self._input_name = session.get_inputs()[0].name
        return session

    @classmethod
    def create(cls, config: DetectorConfig, project_root: Path) -> "Detector | None":
        if not config.enabled:
            return None
        path = Path(config.model) if Path(config.model).is_absolute() else project_root / config.model
        if not path.is_file():
            log.warning("Detector disabled: %s not found (train one with -Train, see docs/YOLO.md)", path)
            return None
        try:
            detector = cls(config, project_root)
        except Exception as exc:  # missing/corrupt model must not stop the bot
            log.warning("Detector disabled: cannot load %s (%s)", config.model, exc)
            return None
        log.info(
            "Detector loaded: %s via %s, classes %s",
            detector.model_path.name, detector.backend, ", ".join(detector.labels),
        )
        return detector

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run every `every_n_frames` frames; in between return the last result."""
        self._frame += 1
        every = max(1, self.config.every_n_frames)
        if (self._frame - 1) % every != 0:
            return self._last
        size = self.config.input_size
        image, scale, pad = letterbox(frame, size)
        blob = cv2.dnn.blobFromImage(image, 1.0 / 255.0, (size, size), swapRB=True, crop=False)
        if self._session is not None:
            output = self._session.run(None, {self._input_name: blob})[0]
        else:
            assert self.net is not None
            self.net.setInput(blob)
            output = self.net.forward()
        h, w = frame.shape[:2]
        found = decode(output, self.labels, scale, pad, (w, h), self.config.confidence, self.config.iou)
        self._last = [d for d in found if self._in_play_area(d, w, h)]
        return self._last

    def _in_play_area(self, detection: Detection, width: int, height: int) -> bool:
        x, y, w, h = self.config.roi
        cx, cy = detection.center
        return x * width <= cx <= (x + w) * width and y * height <= cy <= (y + h) * height
