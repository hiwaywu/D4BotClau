"""Train the YOLO detector and install it for the bot.

    python tools/yolo_train.py [--epochs 100] [--model yolo11n.pt]
    (or .\\run.cmd -Train ; needs .\\setup.cmd -Yolo once)

Builds the dataset from data/yolo/raw (labelled frames), trains with
Ultralytics (uses an NVIDIA GPU when torch sees one, else CPU - slow but
works for a few hundred frames), exports ONNX and copies it to
models/d4.onnx + models/d4.classes.txt, which the bot loads when
`detector.enabled: true`. Older models are kept as models/d4_<date>.onnx.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4bot.config import load_config  # noqa: E402
from d4bot.dataset import build_dataset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--raw", default=str(ROOT / "data" / "yolo" / "raw"))
    parser.add_argument("--model", default="yolo11n.pt", help="starting weights (nano = fastest at run time)")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--imgsz", type=int, default=None, help="default: detector.input_size")
    parser.add_argument("--device", default=None, help="e.g. 0 for the first GPU, cpu; default: auto")
    parser.add_argument("--out", default=str(ROOT / "models" / "d4.onnx"))
    args = parser.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Ultralytics is not installed. Run  .\\setup.cmd -Yolo  once (or pip install -r requirements-yolo.txt).")
        return 1

    config = load_config(args.config)
    classes = list(config.detector.classes)
    imgsz = args.imgsz or config.detector.input_size
    dataset = ROOT / "data" / "yolo" / "dataset"
    summary = build_dataset(Path(args.raw), dataset, classes)
    print(f"Dataset: train {summary.train_images} / val {summary.val_images} frames;"
          f" boxes {summary.boxes_per_class}")
    if summary.train_images < 50:
        print("Warning: under 50 labelled training frames; expect a weak model (aim for 300+ to start).")

    model = YOLO(args.model)
    train_kwargs = dict(
        data=str(summary.yaml_path),
        epochs=args.epochs,
        imgsz=imgsz,
        batch=args.batch,
        project=str(ROOT / "runs"),
        name=time.strftime("d4_%Y%m%d_%H%M%S"),
        patience=30,
        # The game camera never flips upside down; left/right flips are fine.
        flipud=0.0,
        fliplr=0.5,
        verbose=False,
    )
    if args.device is not None:
        train_kwargs["device"] = args.device
    model.train(**train_kwargs)
    best = Path(model.trainer.best)
    print(f"Best weights: {best}")

    metrics = YOLO(str(best)).val(
        data=str(summary.yaml_path), imgsz=imgsz, verbose=False,
        project=str(best.parent.parent), name="val",
    )
    print(f"Validation mAP50 {metrics.box.map50:.3f}  mAP50-95 {metrics.box.map:.3f}")
    for i, name in enumerate(classes):
        try:
            p, r, ap50, ap = metrics.box.class_result(i)
            print(f"  {name:10s} precision {p:.2f} recall {r:.2f} mAP50 {ap50:.2f}")
        except (IndexError, ValueError):
            print(f"  {name:10s} (no validation boxes)")

    # simplify=False: the simplifier pulls in onnxruntime, which would clash with
    # onnxruntime-directml (GPU at run time). OpenCV loads the plain export fine.
    onnx = Path(YOLO(str(best)).export(format="onnx", imgsz=imgsz, opset=12, simplify=False, dynamic=False))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        shutil.copy2(out, out.with_name(f"{out.stem}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(out.stat().st_mtime))}.onnx"))
    shutil.copy2(onnx, out)
    out.with_suffix(".classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    print(f"Installed {out} (+ {out.with_suffix('.classes.txt').name}).")
    print("Turn it on in config.yaml:  detector: enabled: true   then check a dry run with -Snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
