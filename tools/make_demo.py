"""Generate a synthetic overlay preview (no game required)."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from d4bot.brain import CombatBrain  # noqa: E402
from d4bot.config import load_config  # noqa: E402
from d4bot.overlay import DebugOverlay  # noqa: E402
from d4bot.vision import Vision  # noqa: E402


def main() -> int:
    config = load_config(ROOT / "config.yaml")
    # Synthetic preview: make the form gate easy to demonstrate in two frames.
    config = replace(
        config,
        form=replace(config.form, confirm_frames=1, demon_threshold=0.40, hysteresis=0.01),
    )
    width, height = 1600, 900
    frame = np.zeros((height, width, 3), dtype=np.uint8)

    # Dark, red-tinted "Helltide-ish" background for the preview only.
    for y in range(height):
        t = y / height
        frame[y, :, :] = (int(18 + 22 * t), int(12 + 10 * t), int(28 + 34 * t))
    cv2.circle(frame, (1180, 260), 190, (25, 20, 45), -1)
    cv2.circle(frame, (1180, 260), 190, (40, 30, 70), 2)
    cv2.putText(frame, "SYNTHETIC PREVIEW - NOT GAMEPLAY", (40, 865), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (120, 120, 140), 1, cv2.LINE_AA)

    regions = config.regions
    hx, hy, hw, hh = regions["health_globe"].pixels(width, height)
    cv2.ellipse(frame, (hx + hw // 2, hy + hh // 2), (hw // 3, hh // 3), 0, 0, 360, (0, 0, 235), -1)
    rx, ry, rw, rh = regions["resource_globe"].pixels(width, height)
    cv2.ellipse(frame, (rx + rw // 2, ry + rh // 2), (rw // 3, rh // 3), 0, 0, 360, (235, 90, 10), -1)

    sx, sy, sw, sh = regions["skill_bar"].pixels(width, height)
    cv2.rectangle(frame, (sx, sy), (sx + sw, sy + sh), (18, 18, 24), -1)
    gap = 8
    cell = (sw - gap * 7) // 6
    for i in range(6):
        ix = sx + gap + i * (cell + gap)
        iy = sy + 20
        color = (0, 95, 160)
        border = (150, 190, 230)
        if i == 5:  # rightmost icon: demon-form glow reference
            color = (0, 150, 255)
            border = (0, 220, 255)
        cv2.rectangle(frame, (ix, iy), (ix + cell, iy + cell), color, -1)
        cv2.rectangle(frame, (ix, iy), (ix + cell, iy + cell), border, 2)

    for bx, by, bw, bh in ((720, 265, 95, 7), (900, 340, 70, 6), (620, 390, 110, 8)):
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (0, 0, 235), -1)

    vision = Vision(config)
    # Two passes so the two-frame enemy-bar persistence gate opens, as it would live.
    vision.observe(frame)
    obs = vision.observe(frame)
    decision = CombatBrain(config).decide(obs, foreground=True)
    rendered = DebugOverlay(config).render(obs, decision, input_enabled=False)

    out = ROOT / "docs" / "demo_overlay.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out), rendered):
        raise SystemExit(f"failed to write {out}")
    print(f"wrote {out}")
    print(f"state={decision.state} enemies={len(obs.enemies)} health={obs.health.level:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
