"""Death screen -> "Revive at Checkpoint" -> back in the game.

Measured from the first real death on this PC (2026-10-26, Torment X,
`data/death_20261026_140305.png`): the "YOU HAVE DIED" banner sits at the top
centre (template 1.00 on the death frame, <= 0.21 on 516 other captures); the
"Revive at Checkpoint" button is centred at (1280, 1273) at 2560x1440 and stays
greyed out for a few seconds, so it is simply clicked every couple of seconds
until the HUD is back (a click on the greyed button does nothing).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger(__name__)

BUTTON = (0.5, 0.884)                   # "Revive at Checkpoint", fraction of the frame
BANNER_ROI = (0.27, 0.08, 0.46, 0.21)   # x, y, w, h fraction: around "YOU HAVE DIED"


@dataclass
class ReviveResult:
    ok: bool
    reason: str = ""
    clicks: int = 0


class DeathScreen:
    def __init__(self, project_root: Path, threshold: float = 0.6):
        path = Path(project_root) / "templates" / "death_banner.png"
        self.template = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if self.template is None:
            raise FileNotFoundError(path)
        self.threshold = threshold

    def score(self, frame: np.ndarray) -> float:
        h, w = frame.shape[:2]
        x, y, rw, rh = BANNER_ROI
        roi = cv2.cvtColor(frame[int(y * h) : int((y + rh) * h), int(x * w) : int((x + rw) * w)], cv2.COLOR_BGR2GRAY)
        t = self.template
        if abs(w / 2560.0 - 1.0) > 0.01:
            t = cv2.resize(t, (max(8, int(t.shape[1] * w / 2560.0)), max(8, int(t.shape[0] * h / 1440.0))))
        if roi.shape[0] < t.shape[0] or roi.shape[1] < t.shape[1]:
            return 0.0
        return float(cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1])

    def seen(self, frame: np.ndarray) -> bool:
        return self.score(frame) >= self.threshold


class ReviveRunner:
    def __init__(
        self,
        screen: DeathScreen,
        hud_visible: Callable[[np.ndarray], bool],
        grab: Callable[[], np.ndarray],
        send: Callable[[list], None],
        stopped: Callable[[], bool],
        sleep: Callable[[float], None] = time.sleep,
        timeout_s: float = 90.0,
        click_every_s: float = 2.0,
    ):
        self.screen = screen
        self.hud_visible = hud_visible
        self.grab = grab
        self.send = send
        self.stopped = stopped
        self.sleep = sleep
        self.timeout_s = timeout_s
        self.click_every_s = click_every_s

    def run(self) -> ReviveResult:
        from .brain import Action
        from .warplan import is_loading

        frame = self.grab()
        if not self.screen.seen(frame):
            return ReviveResult(False, "no death screen")
        clicks = 0
        waited = 0.0
        last_click = -self.click_every_s
        while waited < self.timeout_s:
            if self.stopped():
                return ReviveResult(False, "stopped", clicks)
            if self.screen.seen(frame):
                if waited - last_click >= self.click_every_s:
                    h, w = frame.shape[:2]
                    self.send([Action(kind="click", x=int(BUTTON[0] * w), y=int(BUTTON[1] * h))])
                    clicks += 1
                    last_click = waited
            elif not is_loading(frame) and self.hud_visible(frame):
                log.info("Revive: back in the game after %.0fs (%d click(s))", waited, clicks)
                return ReviveResult(True, clicks=clicks)
            self.sleep(0.25)
            waited += 0.25
            frame = self.grab()
        return ReviveResult(False, f"still not revived after {self.timeout_s:.0f}s", clicks)
