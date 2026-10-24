"""Draw YOLO boxes on collected frames (no game needed).

    python tools/yolo_label.py [folder] [--model models/d4.onnx]
    (or .\\run.cmd -Label)

Mouse:  left-drag = new box with the current class; right-click a box = delete it.
Keys:   1-9   pick the class (shown at the top)
        SPACE / ENTER / n   save and go to the next frame
        b     save and go back one frame
        u     undo the last box          c   clear all boxes
        s     skip this frame (no label file written)
        q / ESC   save and quit

A frame saved with no boxes is a "nothing here" example - just as useful
(town, empty ground, red Helltide scenery that must not count as a monster).
With --model (or once models/d4.onnx exists), unlabelled frames open with the
current model's boxes already drawn: fix them instead of drawing from scratch.
Starts at the first frame without a label file.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4bot.config import load_config  # noqa: E402
from d4bot.dataset import Box, label_path, list_images, read_labels, write_labels  # noqa: E402
from d4bot.detector import Detector  # noqa: E402
from d4bot.vision import fit_for_display  # noqa: E402

COLORS = [(0, 165, 255), (255, 0, 255), (255, 255, 255), (0, 215, 255), (0, 255, 0),
          (255, 128, 0), (128, 0, 255), (0, 128, 255), (255, 255, 0)]
WINDOW = "YOLO labels"


class Labeler:
    def __init__(self, images: list[Path], classes: list[str], detector: Detector | None):
        self.images = images
        self.classes = classes
        self.detector = detector
        self.index = next((i for i, p in enumerate(images) if not label_path(p).exists()), 0)
        self.current = 0
        self.boxes: list[Box] = []
        self.image: np.ndarray | None = None
        self.scale = 1.0
        self.drag: tuple[int, int] | None = None
        self.mouse = (0, 0)
        self.proposed = False

    def load(self) -> None:
        path = self.images[self.index]
        self.image = cv2.imread(str(path))
        h, w = self.image.shape[:2]
        boxes = read_labels(path, w, h)
        self.proposed = False
        if boxes is None and self.detector is not None:
            boxes = []
            for d in self.detector.detect(self.image):
                if d.label in self.classes:
                    boxes.append(Box(self.classes.index(d.label), *map(float, d.bbox)))
            self.proposed = bool(boxes)
        self.boxes = boxes or []

    def save(self) -> None:
        assert self.image is not None
        h, w = self.image.shape[:2]
        write_labels(self.images[self.index], self.boxes, w, h)

    def on_mouse(self, event: int, x: int, y: int, flags: int, param: object) -> None:
        self.mouse = (x, y)
        ix, iy = x / self.scale, y / self.scale
        if event == cv2.EVENT_LBUTTONDOWN:
            self.drag = (x, y)
        elif event == cv2.EVENT_LBUTTONUP and self.drag is not None:
            x0, y0 = self.drag[0] / self.scale, self.drag[1] / self.scale
            self.drag = None
            if abs(ix - x0) >= 4 and abs(iy - y0) >= 4:
                self.boxes.append(Box(self.current, min(x0, ix), min(y0, iy), abs(ix - x0), abs(iy - y0)))
        elif event == cv2.EVENT_RBUTTONDOWN:
            hits = [b for b in self.boxes if b.x <= ix <= b.x + b.w and b.y <= iy <= b.y + b.h]
            if hits:
                self.boxes.remove(min(hits, key=lambda b: b.w * b.h))

    def render(self) -> np.ndarray:
        assert self.image is not None
        view, self.scale = fit_for_display(self.image, 1600, 860)
        view = view.copy()
        for b in self.boxes:
            color = COLORS[b.cls % len(COLORS)]
            p0 = (int(b.x * self.scale), int(b.y * self.scale))
            p1 = (int((b.x + b.w) * self.scale), int((b.y + b.h) * self.scale))
            cv2.rectangle(view, p0, p1, color, 2)
            name = self.classes[b.cls] if b.cls < len(self.classes) else str(b.cls)
            cv2.putText(view, name, (p0[0], max(12, p0[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
        if self.drag is not None:
            cv2.rectangle(view, self.drag, self.mouse, COLORS[self.current % len(COLORS)], 1)
        bar = np.zeros((44, view.shape[1], 3), dtype=np.uint8)
        done = sum(1 for p in self.images if label_path(p).exists())
        status = f"{self.index + 1}/{len(self.images)}  labelled {done}  boxes {len(self.boxes)}"
        if self.proposed:
            status += "  (model guesses - fix and save)"
        cv2.putText(bar, status, (8, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (220, 220, 220), 1, cv2.LINE_AA)
        x = 8
        for i, name in enumerate(self.classes):
            text = f"{i + 1}:{name}"
            color = COLORS[i % len(COLORS)] if i == self.current else (120, 120, 120)
            cv2.putText(bar, text, (x, 37), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2 if i == self.current else 1, cv2.LINE_AA)
            x += 14 + 10 * len(text)
        return np.vstack([bar, view])

    def run(self) -> None:
        cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
        # The view sits under a 44px status bar.
        cv2.setMouseCallback(WINDOW, lambda e, x, y, f, p: self.on_mouse(e, x, y - 44, f, p))
        self.load()
        while True:
            cv2.imshow(WINDOW, self.render())
            key = cv2.waitKey(20) & 0xFF
            if key == 255:
                if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    break
                continue
            if ord("1") <= key <= ord("9") and key - ord("1") < len(self.classes):
                self.current = key - ord("1")
            elif key in (ord(" "), 13, ord("n")):
                self.save()
                if self.index + 1 >= len(self.images):
                    print("Last frame labelled.")
                    break
                self.index += 1
                self.load()
            elif key == ord("b"):
                self.save()
                self.index = max(0, self.index - 1)
                self.load()
            elif key == ord("s"):
                self.index = min(len(self.images) - 1, self.index + 1)
                self.load()
            elif key == ord("u") and self.boxes:
                self.boxes.pop()
            elif key == ord("c"):
                self.boxes = []
            elif key in (ord("q"), 27):
                self.save()
                break
        cv2.destroyAllWindows()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?", default=str(ROOT / "data" / "yolo" / "raw"))
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--model", default=None, help="pre-label with this ONNX model (default: detector.model if it exists)")
    args = parser.parse_args()

    config = load_config(args.config)
    classes = list(config.detector.classes)
    images = list_images(Path(args.folder))
    if not images:
        print(f"No images in {args.folder}. Collect some first: .\\run.cmd -Collect helltide")
        return 1
    model = Path(args.model) if args.model else ROOT / config.detector.model
    detector = None
    if model.exists():
        det_config = replace(config.detector, enabled=True, model=str(model), every_n_frames=1, roi=(0.0, 0.0, 1.0, 1.0))
        detector = Detector.create(det_config, ROOT)
        if detector is not None:
            classes = detector.labels
            print(f"Pre-labelling with {model.name}")
    print(__doc__)
    Labeler(images, classes, detector).run()
    done = sum(1 for p in images if label_path(p).exists())
    print(f"{done}/{len(images)} frames labelled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
