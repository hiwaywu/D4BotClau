"""Debug overlay for calibration and dry-run inspection."""

from __future__ import annotations

import logging
import math

import cv2
import numpy as np

from .brain import Decision
from .config import BotConfig, Region
from .vision import Observation, fit_for_display

log = logging.getLogger(__name__)

WINDOW = "d4-visual-bot | debug (q/esc to quit)"


class DebugOverlay:
    def __init__(self, config: BotConfig):
        self.config = config
        self._shown = False

    def render(self, obs: Observation, decision: Decision, input_enabled: bool, brain=None) -> np.ndarray:  # type: ignore[no-untyped-def]
        frame = obs.frame.copy()
        scale = max(0.65, min(1.6, obs.width / 1920.0))

        colors = {
            "health_globe": (0, 0, 255),
            "resource_globe": (255, 120, 0),
            "skill_bar": (0, 220, 0),
            "enemy_roi": (0, 180, 255),
            "center": (255, 0, 255),
            "minimap": (255, 255, 255),
            "buff_bar": (0, 255, 255),
        }
        for name, region in self.config.regions.items():
            x, y, w, h = region.pixels(obs.width, obs.height)
            color = colors.get(name, (160, 160, 160))
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 1)
            cv2.putText(
                frame,
                name,
                (x + 4, max(14, y + 16)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42 * scale,
                color,
                1,
                cv2.LINE_AA,
            )

        if self.config.form.enabled:
            form_region = self.config.form.roi
            if form_region is None:
                bar = self.config.regions["skill_bar"]
                form_region = Region(bar.x + bar.w * 5.0 / 6.0, bar.y, bar.w / 6.0, bar.h)
            fx, fy, fw, fh = form_region.pixels(obs.width, obs.height)
            cv2.rectangle(frame, (fx, fy), (fx + fw, fy + fh), (255, 255, 0), 2)
            cv2.putText(
                frame,
                f"form {obs.form.metric:.2f}",
                (fx, max(14, fy - 4)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42 * scale,
                (255, 255, 0),
                1,
                cv2.LINE_AA,
            )

        self._minimap(frame, obs, brain, scale)
        for lx, ly, lw, lh in obs.loot_labels:
            cv2.rectangle(frame, (lx - 3, ly - 3), (lx + lw + 3, ly + lh + 3), (255, 255, 255), 2)

        for enemy in obs.enemies:
            x, y, w, h = enemy.bbox
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(frame, enemy.center, 3, (0, 255, 0), -1)

        if obs.enemies:
            cx, cy = self.config.regions["center"].center_pixels(obs.width, obs.height)
            nearest = min(
                obs.enemies,
                key=lambda e: (e.center[0] - cx) ** 2 + (e.center[1] - cy) ** 2,
            )
            cv2.line(frame, (cx, cy), nearest.center, (255, 255, 0), 1)

        bar_x, bar_y = 14, 64
        self._bar(frame, bar_x, bar_y, obs.health.level, (0, 0, 255), "HP", scale)
        self._bar(frame, bar_x + int(190 * scale), bar_y, obs.resource.level, (255, 140, 0), "RES", scale)

        if not self.config.form.enabled:
            form_label = "OFF"
        elif self.config.form.mode in {"buff", "glow"}:
            form_label = {True: "DEMON", False: "HUMAN", None: "?"}[obs.buff.present]
            form_label += f" {obs.buff.score:.2f}"
        elif self.config.form.mode == "toggle_state":
            form_label = "READY" if obs.form.metric >= self.config.form.ready_threshold else "COOLDOWN"
        elif obs.form.demon is None:
            form_label = "?"
        else:
            form_label = "DEMON" if obs.form.demon else "HUMAN"
        panel = frame.copy()
        cv2.rectangle(panel, (0, 0), (int(920 * scale), int(52 * scale)), (20, 20, 20), -1)
        frame = cv2.addWeighted(panel, 0.72, frame, 0.28, 0)
        status = (
            f"{decision.state:<6}  FORM {form_label:<10}  HP {obs.health.level:0.2f}  "
            f"RES {obs.resource.level:0.2f}  ENEMIES {len(obs.enemies)}  "
            f"INPUT {'ON' if input_enabled else 'DRY-RUN'}"
        )
        cv2.putText(
            frame,
            status,
            (12, int(34 * scale)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.62 * scale,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )
        if decision.note:
            cv2.putText(
                frame,
                decision.note[:110],
                (12, int(52 * scale) + 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45 * scale,
                (180, 220, 180),
                1,
                cv2.LINE_AA,
            )
        return frame

    def _minimap(self, frame: np.ndarray, obs: Observation, brain, scale: float) -> None:  # type: ignore[no-untyped-def]
        mm = obs.minimap
        if not mm.valid:
            return
        x, y, w, h = mm.region
        cx, cy = x + w / 2.0, y + h / 2.0
        start = self.config.nav.ray_start_px
        for angle, clear in zip(mm.angles, mm.clearance):
            dx, dy = math.cos(angle), math.sin(angle)
            limits = [(w / 2.0 - 1) / abs(dx)] if abs(dx) > 1e-6 else []
            limits += [(h / 2.0 - 1) / abs(dy)] if abs(dy) > 1e-6 else []
            length = min(limits)
            r = start + float(clear) * (length - start)
            color = (0, 220, 0) if clear >= self.config.nav.min_clearance else (0, 0, 220)
            cv2.line(frame, (int(cx), int(cy)), (int(cx + dx * r), int(cy + dy * r)), color, 1, cv2.LINE_AA)
        radius = self.config.combat.fight_radius_px
        cv2.circle(frame, (int(cx), int(cy)), int(radius), (0, 200, 255), 1, cv2.LINE_AA)
        for pack in mm.packs:
            px, py = int(cx + pack.dx), int(cy + pack.dy)
            color = (255, 0, 255) if pack.elite else (0, 255, 255)
            cv2.circle(frame, (px, py), 6 + 2 * min(pack.dots, 5), color, 2, cv2.LINE_AA)
            if not pack.clear and pack.via is not None:
                # Planned route around a wall: player -> waypoint -> pack.
                vx, vy = int(cx + pack.via[0]), int(cy + pack.via[1])
                cv2.line(frame, (int(cx), int(cy)), (vx, vy), (0, 255, 0), 1, cv2.LINE_AA)
                cv2.line(frame, (vx, vy), (px, py), (0, 160, 0), 1, cv2.LINE_AA)
        for sx, sy in mm.loot:
            cv2.circle(frame, (int(cx + sx), int(cy + sy)), 14, (255, 255, 255), 2, cv2.LINE_AA)
        if brain is not None:
            heading = brain.heading
            tip = (int(cx + math.cos(heading) * 60), int(cy + math.sin(heading) * 60))
            cv2.arrowedLine(frame, (int(cx), int(cy)), tip, (255, 255, 0), 2, cv2.LINE_AA, tipLength=0.3)
            cv2.putText(
                frame,
                f"rot {math.degrees(brain.rotation):+.0f}  stuck {brain.stuck_count}",
                (x, y + h + int(18 * scale)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45 * scale,
                (255, 255, 0),
                1,
                cv2.LINE_AA,
            )

    @staticmethod
    def _bar(frame: np.ndarray, x: int, y: int, level: float, color, label: str, scale: float) -> None:  # type: ignore[no-untyped-def]
        width, height = int(160 * scale), int(16 * scale)
        cv2.putText(
            frame,
            label,
            (x, y - 5),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42 * scale,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.rectangle(frame, (x, y), (x + width, y + height), (60, 60, 60), -1)
        fill = int(width * float(np.clip(level, 0.0, 1.0)))
        if fill > 0:
            cv2.rectangle(frame, (x, y), (x + fill, y + height), color, -1)
        cv2.rectangle(frame, (x, y), (x + width, y + height), (230, 230, 230), 1)
        cv2.putText(
            frame,
            f"{level:0.2f}",
            (x + width + 8, y + height - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45 * scale,
            (230, 230, 230),
            1,
            cv2.LINE_AA,
        )

    def show(self, frame: np.ndarray) -> str | None:
        try:
            display, _ = fit_for_display(frame, 1600, 900)
            cv2.imshow(WINDOW, display)
            self._shown = True
            key = cv2.waitKey(1) & 0xFF
            if key == 255:
                return None
            return chr(key)
        except cv2.error:
            log.exception("OpenCV window unavailable; disabling overlay")
            return "q"

    def close(self) -> None:
        if self._shown:
            try:
                cv2.destroyWindow(WINDOW)
            except cv2.error:
                pass
            self._shown = False
