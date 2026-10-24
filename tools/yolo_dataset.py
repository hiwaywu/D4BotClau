"""Build the YOLO train/val split from labelled frames.

    python tools/yolo_dataset.py [--raw data/yolo/raw] [--out data/yolo/dataset]
    (tools/yolo_train.py runs this for you)

Validation frames come from whole sessions the model never trains on, so the
validation score predicts how it does on a new run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4bot.config import load_config  # noqa: E402
from d4bot.dataset import build_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--raw", default=str(ROOT / "data" / "yolo" / "raw"))
    parser.add_argument("--out", default=str(ROOT / "data" / "yolo" / "dataset"))
    parser.add_argument("--val-fraction", type=float, default=0.2)
    args = parser.parse_args()
    classes = list(load_config(args.config).detector.classes)
    summary = build_dataset(Path(args.raw), Path(args.out), classes, args.val_fraction)
    print(f"train {summary.train_images} / val {summary.val_images} frames"
          f" (val sessions: {', '.join(summary.val_sessions) or 'every 5th frame of the only session'})")
    for name, count in summary.boxes_per_class.items():
        print(f"  {name:10s} {count} boxes")
    print(f"-> {summary.yaml_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
