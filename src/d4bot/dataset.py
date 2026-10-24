"""YOLO training data: frame collection, label files, and dataset building.

Layout (all under data/, which is not in git):

    data/yolo/raw/<session>/frame_00001.jpg     collected frames, one folder per session
    data/yolo/raw/<session>/frame_00001.txt     YOLO labels written by tools/yolo_label.py
    data/yolo/dataset/images/{train,val}/...    built by tools/yolo_dataset.py
    data/yolo/dataset/labels/{train,val}/...
    data/yolo/dataset/data.yaml

A label file holds one line per box: `class_id cx cy w h`, all normalised to
0..1 (the Ultralytics format). An existing empty .txt means "labelled, nothing
in it" - useful negatives (town, empty ground, red Helltide scenery).
"""

from __future__ import annotations

import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


class FrameDeduper:
    """Skip frames that barely differ from the last kept one (standing still, menus)."""

    def __init__(self, min_change: float):
        self.min_change = min_change
        self._last: np.ndarray | None = None

    def keep(self, frame: np.ndarray) -> bool:
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA)
        if self._last is not None and float(np.mean(cv2.absdiff(small, self._last))) < self.min_change:
            return False
        self._last = small
        return True


@dataclass(frozen=True)
class Box:
    cls: int
    x: float  # pixel box in the image
    y: float
    w: float
    h: float


def label_path(image: Path) -> Path:
    return image.with_suffix(".txt")


def read_labels(image: Path, width: int, height: int) -> list[Box] | None:
    """Boxes in pixels, or None if the image has no label file yet."""
    path = label_path(image)
    if not path.exists():
        return None
    boxes: list[Box] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        cls, cx, cy, bw, bh = int(parts[0]), *(float(v) for v in parts[1:])
        boxes.append(Box(cls, (cx - bw / 2) * width, (cy - bh / 2) * height, bw * width, bh * height))
    return boxes


def write_labels(image: Path, boxes: list[Box], width: int, height: int) -> None:
    lines = []
    for b in boxes:
        x0, y0 = max(0.0, b.x), max(0.0, b.y)
        x1, y1 = min(float(width), b.x + b.w), min(float(height), b.y + b.h)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        lines.append(
            f"{b.cls} {(x0 + x1) / 2 / width:.6f} {(y0 + y1) / 2 / height:.6f} "
            f"{(x1 - x0) / width:.6f} {(y1 - y0) / height:.6f}"
        )
    label_path(image).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


@dataclass(frozen=True)
class DatasetSummary:
    train_images: int
    val_images: int
    boxes_per_class: dict[str, int]
    val_sessions: tuple[str, ...]
    yaml_path: Path


def build_dataset(
    raw_root: Path,
    out: Path,
    classes: list[str],
    val_fraction: float = 0.2,
    seed: int = 0,
) -> DatasetSummary:
    """Copy labelled frames into a train/val split, by SESSION, and write data.yaml.

    Splitting by session keeps near-identical neighbouring frames out of the
    validation set, so the val score means "works on a run it has not seen".
    """
    sessions: dict[str, list[Path]] = {}
    for image in list_images(raw_root):
        if label_path(image).exists():
            session = image.relative_to(raw_root).parts[0] if image.parent != raw_root else "_root"
            sessions.setdefault(session, []).append(image)
    if not sessions:
        raise ValueError(f"no labelled images under {raw_root} (label some with tools/yolo_label.py)")

    names = sorted(sessions)
    rng = random.Random(seed)
    rng.shuffle(names)
    val_sessions: list[str] = []
    if len(names) >= 2:
        total = sum(len(v) for v in sessions.values())
        for name in names:
            if sum(len(sessions[s]) for s in val_sessions) >= total * val_fraction:
                break
            val_sessions.append(name)
        if len(val_sessions) == len(names):
            val_sessions.pop()
    split: dict[str, list[Path]] = {"train": [], "val": []}
    for name in names:
        split["val" if name in val_sessions else "train"].extend(sessions[name])
    if not split["val"]:
        # One session only: fall back to every 5th frame, better than nothing.
        images = split["train"]
        split["val"] = images[::5]
        split["train"] = [p for i, p in enumerate(images) if i % 5]

    if out.exists():
        shutil.rmtree(out)
    counts = {c: 0 for c in classes}
    for part, images in split.items():
        (out / "images" / part).mkdir(parents=True, exist_ok=True)
        (out / "labels" / part).mkdir(parents=True, exist_ok=True)
        for image in images:
            stem = "_".join(image.relative_to(raw_root).with_suffix("").parts)
            shutil.copy2(image, out / "images" / part / f"{stem}{image.suffix.lower()}")
            text = label_path(image).read_text(encoding="utf-8")
            (out / "labels" / part / f"{stem}.txt").write_text(text, encoding="utf-8")
            for line in text.splitlines():
                parts = line.split()
                if parts and 0 <= int(parts[0]) < len(classes):
                    counts[classes[int(parts[0])]] += 1

    yaml_path = out / "data.yaml"
    yaml_path.write_text(
        f"path: {out.resolve().as_posix()}\n"
        "train: images/train\n"
        "val: images/val\n"
        "names:\n" + "".join(f"  {i}: {name}\n" for i, name in enumerate(classes)),
        encoding="utf-8",
    )
    return DatasetSummary(len(split["train"]), len(split["val"]), counts, tuple(val_sessions), yaml_path)
