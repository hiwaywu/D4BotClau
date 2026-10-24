"""YOLO pipeline pieces that run without a trained model or the game."""

from __future__ import annotations

import random
from pathlib import Path

import cv2
import numpy as np
import pytest

from d4bot.config import DetectorConfig
from d4bot.dataset import Box, FrameDeduper, build_dataset, read_labels, write_labels
from d4bot.detector import Detection, Detector, decode, letterbox
from test_nav import clock  # noqa: F401  (fixture)

CLASSES = ["monster", "elite", "loot", "chest", "spawner"]


def _head(boxes: list[tuple[float, float, float, float, int, float]], n: int = 50) -> np.ndarray:
    """A fake YOLO detect output (1, 4 + classes, n) holding the given boxes."""
    out = np.zeros((1, 4 + len(CLASSES), n), dtype=np.float32)
    for i, (cx, cy, w, h, cls, score) in enumerate(boxes):
        out[0, :4, i] = (cx, cy, w, h)
        out[0, 4 + cls, i] = score
    return out


def test_letterbox_and_decode_map_boxes_back_to_the_frame() -> None:
    frame = np.zeros((1440, 2560, 3), dtype=np.uint8)
    image, scale, pad = letterbox(frame, 640)
    assert image.shape == (640, 640, 3) and scale == pytest.approx(0.25) and pad == (0, 140)
    # A 100x80 monster at frame (1000, 600)-(1100, 680), as the model sees it.
    cx, cy = 1050 * scale + pad[0], 640 * scale + pad[1]
    out = _head([
        (cx, cy, 100 * scale, 80 * scale, 0, 0.90),
        (cx + 1, cy, 100 * scale, 80 * scale, 0, 0.80),      # duplicate -> removed by NMS
        (cx, cy, 30 * scale, 30 * scale, 2, 0.70),            # loot on top of it: other class, kept
        (100.0, 300.0, 20.0, 20.0, 1, 0.20),                  # below the confidence threshold
    ])
    found = decode(out, CLASSES, scale, pad, (2560, 1440), confidence=0.40, iou=0.5)
    assert [d.label for d in found] == ["monster", "loot"]
    x, y, w, h = found[0].bbox
    assert abs(x - 1000) <= 2 and abs(y - 600) <= 2 and abs(w - 100) <= 2 and abs(h - 80) <= 2


def test_decode_rejects_a_model_with_other_classes() -> None:
    with pytest.raises(ValueError):
        decode(_head([]), CLASSES[:3], 1.0, (0, 0), (640, 640), 0.4, 0.5)


def test_missing_model_disables_the_detector(tmp_path: Path) -> None:
    config = DetectorConfig(enabled=True, model="models/none.onnx")
    assert Detector.create(config, tmp_path) is None
    assert Detector.create(DetectorConfig(enabled=False), tmp_path) is None


def test_label_files_round_trip(tmp_path: Path) -> None:
    image = tmp_path / "frame_00001.jpg"
    cv2.imwrite(str(image), np.zeros((1440, 2560, 3), dtype=np.uint8))
    assert read_labels(image, 2560, 1440) is None                # not labelled yet
    write_labels(image, [Box(1, 100, 200, 50, 40), Box(0, 2550, 10, 30, 30)], 2560, 1440)
    boxes = read_labels(image, 2560, 1440)
    assert boxes is not None and len(boxes) == 2
    assert boxes[0].cls == 1 and abs(boxes[0].x - 100) < 0.1 and abs(boxes[0].w - 50) < 0.1
    assert abs(boxes[1].w - 10) < 0.1                            # clipped at the frame edge
    write_labels(image, [], 2560, 1440)
    assert read_labels(image, 2560, 1440) == []                  # labelled: nothing here


def test_dataset_splits_by_session(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    for session in ("helltide_a", "helltide_b", "helltide_c", "town_d"):
        (raw / session).mkdir(parents=True)
        for i in range(10):
            image = raw / session / f"frame_{i:05d}.jpg"
            cv2.imwrite(str(image), np.zeros((36, 64, 3), dtype=np.uint8))
            if i < 8:  # two unlabelled frames per session are left out
                write_labels(image, [Box(0, 5, 5, 10, 10)] if i % 2 else [], 64, 36)
    summary = build_dataset(raw, tmp_path / "dataset", CLASSES, val_fraction=0.2)
    assert summary.train_images + summary.val_images == 32
    assert len(summary.val_sessions) == 1 and summary.val_images == 8
    val_names = [p.name for p in (tmp_path / "dataset" / "images" / "val").iterdir()]
    assert all(name.startswith(summary.val_sessions[0]) for name in val_names)
    assert summary.boxes_per_class["monster"] == 16
    text = summary.yaml_path.read_text()
    assert "train: images/train" in text and "4: spawner" in text


def test_deduper_skips_frames_that_did_not_change() -> None:
    rng = np.random.default_rng(0)
    frame = rng.integers(0, 255, (360, 640, 3), dtype=np.uint8)
    deduper = FrameDeduper(min_change=6.0)
    assert deduper.keep(frame)
    assert not deduper.keep(frame)
    assert deduper.keep(255 - frame)


def test_hunt_aims_at_the_detected_monster(tmp_path: Path, clock) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    from test_nav import H, W, _config, _dot, _frame, _maze

    from d4bot.brain import CombatBrain
    from d4bot.config import CombatConfig
    from d4bot.vision import Vision

    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt", fight_radius_px=40.0, attack_range_px=40.0),
        detector=DetectorConfig(aim=True),
    )
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    _dot(frame, 20, 0)                                    # minimap: pack to the right
    obs = Vision(config).observe(frame)
    body = (int(W * 0.5) + 160, int(H * 0.48) + 70, 60, 90)
    far = (100, 100, 60, 90)                              # outside aim_radius_px: ignored
    obs.detections = [Detection("monster", 0.9, far), Detection("monster", 0.8, body)]
    clock.now += 0.05
    decision = CombatBrain(config, rng=random.Random(1)).decide(obs, True)
    assert decision.state == "FIGHT"
    aim = [a for a in decision.actions if a.kind == "aim"][0]
    assert (aim.x, aim.y) == (body[0] + 30, body[1] + 45)


def test_labeler_draws_deletes_and_saves_boxes(tmp_path: Path) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("yolo_label", Path(__file__).parents[1] / "tools" / "yolo_label.py")
    assert spec is not None and spec.loader is not None
    yolo_label = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(yolo_label)

    images = []
    for i in range(2):
        image = tmp_path / f"frame_{i:05d}.jpg"
        cv2.imwrite(str(image), np.zeros((1440, 2560, 3), dtype=np.uint8))
        images.append(image)
    labeler = yolo_label.Labeler(images, CLASSES, detector=None)
    labeler.load()
    view = labeler.render()                               # sets the display scale
    assert view.shape[1] <= 1600
    s = labeler.scale
    labeler.current = 1                                   # "elite"
    labeler.on_mouse(cv2.EVENT_LBUTTONDOWN, int(100 * s), int(200 * s), 0, None)
    labeler.on_mouse(cv2.EVENT_LBUTTONUP, int(300 * s), int(400 * s), 0, None)
    labeler.on_mouse(cv2.EVENT_LBUTTONDOWN, int(1000 * s), int(500 * s), 0, None)
    labeler.on_mouse(cv2.EVENT_LBUTTONUP, int(1100 * s), int(600 * s), 0, None)
    labeler.on_mouse(cv2.EVENT_RBUTTONDOWN, int(1050 * s), int(550 * s), 0, None)  # delete the 2nd
    labeler.save()
    boxes = read_labels(images[0], 2560, 1440)
    assert boxes is not None and len(boxes) == 1 and boxes[0].cls == 1
    assert abs(boxes[0].x - 100) < 4 and abs(boxes[0].w - 200) < 4
    # Restarting opens the first frame without a label file.
    assert yolo_label.Labeler(images, CLASSES, detector=None).index == 1
