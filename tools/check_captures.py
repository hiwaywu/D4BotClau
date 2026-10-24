"""Run the perception stack over saved screenshots (no game needed).

    python tools/check_captures.py [data/*.png ...] [--overlay out_dir]

Prints, per image: demon-buff score/verdict, minimap readability and best
open direction, screen saturation (death check), and enemy bars. Also runs a
desaturated copy of each image through the death check, which should fire.
"""

from __future__ import annotations

import argparse
import glob
import math
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4bot.brain import CombatBrain  # noqa: E402
from d4bot.config import load_config  # noqa: E402
from d4bot.overlay import DebugOverlay  # noqa: E402
from d4bot.vision import Vision  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="*")
    parser.add_argument("--config", default=str(ROOT / "config.yaml"))
    parser.add_argument("--overlay", default=None, help="write debug overlays to this folder")
    args = parser.parse_args()

    config = load_config(args.config)
    paths = args.images or sorted(glob.glob(str(ROOT / "data" / "*.png")))
    out_dir = Path(args.overlay) if args.overlay else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    for path in paths:
        image = cv2.imread(path)
        if image is None or image.shape[0] < 600:
            continue
        vision = Vision(config)
        brain = CombatBrain(config)
        obs = None
        for _ in range(max(3, config.form.confirm_frames)):
            obs = vision.observe(image)
        assert obs is not None
        decision = brain.decide(obs, foreground=True)
        mm = obs.minimap
        mm_text = "minimap ---"
        if mm.valid:
            best = int(mm.clearance.argmax())
            mm_text = f"minimap ok walk={mm.walkable_ratio:.2f} best={math.degrees(mm.angles[best]):3.0f}"
        grey = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)
        dv = Vision(config)
        grey_obs = dv.observe(grey)
        print(
            f"{Path(path).name:28s} buff={obs.buff.score:.2f}->{obs.buff.present!s:5s} "
            f"{mm_text:38s} sat={obs.screen.saturation:5.1f} grey={obs.screen.grey!s:5s} "
            f"enemies={len(obs.enemies)} | grey copy -> grey={grey_obs.screen.grey} "
            f"| {decision.state} {decision.note}"
        )
        if out_dir:
            rendered = DebugOverlay(config).render(obs, decision, False, brain)
            cv2.imwrite(str(out_dir / (Path(path).stem + "_overlay.jpg")), rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
