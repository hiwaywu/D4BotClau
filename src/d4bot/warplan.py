"""Teleport to the Helltide with the map's Warplan "Teleport" button.

The user sets the Warplan's next activity to Helltide; its Teleport button
then goes straight there (no map searching). Measured on 2560x1440:
  * map open + Warplan panel: Teleport button 0.99-1.00, elsewhere <= 0.31
  * optional "TELEPORT CONFIRMATION" dialog with Accept: 1.00 / <= 0.32
  * arrived in a Helltide: the "Helltide:" timer label by the minimap,
    0.82-1.00 on 465 Helltide frames, <= 0.34 everywhere else
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

_EMBLEM: np.ndarray | None = None


def is_loading(frame: np.ndarray) -> bool:
    """Loading screen: black, or the loading art with the emblem bottom right.

    The emblem template (live run: the Warplan test landed on a loading
    screen that is not dark) scores <= 0.55 on 1105 non-loading frames.
    """
    small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
    if float(small.mean()) < 15.0:
        return True
    global _EMBLEM
    if _EMBLEM is None:
        path = Path(__file__).resolve().parents[2] / "templates" / "warplan" / "loading_emblem.png"
        _EMBLEM = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if _EMBLEM is None:
            _EMBLEM = np.zeros((1, 1), np.uint8)
    if _EMBLEM.shape[0] < 2:
        return False
    h, w = frame.shape[:2]
    sx, sy = w / BASE_W, h / BASE_H
    t = _EMBLEM if abs(sx - 1.0) < 0.01 else cv2.resize(_EMBLEM, (max(4, int(_EMBLEM.shape[1] * sx)), max(4, int(_EMBLEM.shape[0] * sy))))
    roi = cv2.cvtColor(frame[int(1150 * sy) :, int(2250 * sx) :], cv2.COLOR_BGR2GRAY)
    if roi.shape[0] < t.shape[0] or roi.shape[1] < t.shape[1]:
        return False
    return float(cv2.minMaxLoc(cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED))[1]) >= 0.75

BASE_W, BASE_H = 2560, 1440
AREAS = {
    "teleport_button": (0, 900, 900, 540),
    "confirm_header": (600, 300, 1400, 500),
    "accept": (600, 600, 1400, 500),
    "helltide_label": (1700, 0, 860, 200),
}


@dataclass(frozen=True)
class WarplanConfig:
    map_key: str = "tab"
    threshold: float = 0.75
    map_timeout_s: float = 4.0
    confirm_wait_s: float = 3.0
    loading_timeout_s: float = 12.0
    arrive_timeout_s: float = 30.0
    step_pause_s: float = 1.2         # user: steps were too fast; pause like a person would
    settle_s: float = 4.0             # after the loading screen, before judging where we are
    templates: str = "templates/warplan"


@dataclass
class WarplanResult:
    ok: bool
    reason: str = ""


class WarplanVision:
    def __init__(self, root: Path, config: WarplanConfig):
        self.config = config
        self._templates = {}
        for name in ("teleport_button", "confirm_header", "accept", "helltide_label"):
            image = cv2.imread(str(root / config.templates / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise FileNotFoundError(f"warplan template missing: {name}.png")
            self._templates[name] = image
        # Between Helltides the Warplan teleport sends you back to Ichorfall
        # ("Sanctuary is at peace"); anywhere else means it worked.
        town = cv2.imread(str(root / "templates" / "town" / "ichorfall.png"), cv2.IMREAD_GRAYSCALE)
        if town is not None:
            self._templates["ichorfall"] = town
            AREAS.setdefault("ichorfall", (2200, 0, 360, 70))
        self._scaled: dict[tuple[str, int], np.ndarray] = {}

    def find(self, frame: np.ndarray, name: str) -> tuple[float, tuple[int, int]]:
        h, w = frame.shape[:2]
        sx, sy = w / BASE_W, h / BASE_H
        key = (name, h)
        if key not in self._scaled:
            t = self._templates[name]
            if abs(sx - 1.0) > 0.01:
                t = cv2.resize(t, (max(4, int(t.shape[1] * sx)), max(4, int(t.shape[0] * sy))), interpolation=cv2.INTER_AREA)
            self._scaled[key] = t
        t = self._scaled[key]
        ax, ay, aw, ah = AREAS[name]
        x, y = int(ax * sx), int(ay * sy)
        roi = cv2.cvtColor(frame[y : y + int(ah * sy), x : x + int(aw * sx)], cv2.COLOR_BGR2GRAY)
        if roi.shape[0] < t.shape[0] or roi.shape[1] < t.shape[1]:
            return 0.0, (0, 0)
        result = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        return float(np.nan_to_num(score)), (x + loc[0] + t.shape[1] // 2, y + loc[1] + t.shape[0] // 2)

    def seen(self, frame: np.ndarray, name: str) -> bool:
        return self.find(frame, name)[0] >= self.config.threshold

    @staticmethod
    def loading(frame: np.ndarray) -> bool:
        return is_loading(frame)


class WarplanRunner:
    """Open the map, press the Warplan Teleport, accept, confirm we are in a Helltide."""

    def __init__(self, vision: WarplanVision, config: WarplanConfig, grab, send, stopped, sleep=time.sleep, clock=time.monotonic, debug=None):  # type: ignore[no-untyped-def]
        self.vision = vision
        self.config = config
        self._grab = grab
        self._send = send
        self._stopped = stopped
        self._sleep = sleep
        self._clock = clock
        self._debug = debug or (lambda frame, label: None)

    def _act(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        from .brain import Action

        self._send([Action(**kwargs)])

    def _wait_for(self, check, timeout: float, poll: float = 0.1):  # type: ignore[no-untyped-def]
        end = self._clock() + timeout
        while self._clock() < end:
            if self._stopped():
                return None
            frame = self._grab()
            if check(frame):
                return frame
            self._sleep(poll)
        return None

    def run(self) -> WarplanResult:
        cfg, vision = self.config, self.vision
        frame = self._grab()
        self._debug(frame, "w0_start")
        if vision.loading(frame):
            log.info("Warplan: waiting for a loading screen to finish first")
            frame = self._wait_for(lambda f: not vision.loading(f), cfg.arrive_timeout_s)
            if frame is None:
                return WarplanResult(False, "stuck on a loading screen")
            self._sleep(2.0)
            frame = self._grab()
        if vision.seen(frame, "helltide_label"):
            log.info("Warplan: already in a Helltide")
            return WarplanResult(True, "already in a Helltide")
        if not vision.seen(frame, "teleport_button"):
            self._sleep(cfg.step_pause_s)
            log.info("Warplan: opening the map (%s)", cfg.map_key)
            self._act(kind="press", key=cfg.map_key)
            frame = self._wait_for(lambda f: vision.seen(f, "teleport_button"), cfg.map_timeout_s)
            if frame is None:
                self._debug(self._grab(), "w1_no_button")
                return WarplanResult(False, "map opened but no Warplan Teleport button (or the map did not open)")
        self._debug(frame, "w1_map")
        self._sleep(cfg.step_pause_s)
        _, button = vision.find(frame, "teleport_button")
        log.info("Warplan: clicking Teleport at %s", button)
        self._act(kind="click", x=button[0], y=button[1])

        # Either a confirmation dialog or straight to the loading screen.
        def next_screen(f) -> bool:  # type: ignore[no-untyped-def]
            return vision.seen(f, "confirm_header") or vision.loading(f)

        frame = self._wait_for(next_screen, cfg.confirm_wait_s)
        if frame is not None and vision.seen(frame, "confirm_header"):
            self._debug(frame, "w2_confirm")
            self._sleep(cfg.step_pause_s)
            _, accept = vision.find(frame, "accept")
            log.info("Warplan: accepting the teleport at %s", accept)
            self._act(kind="click", x=accept[0], y=accept[1])
            frame = None
        if frame is None and self._wait_for(vision.loading, cfg.loading_timeout_s, poll=0.05) is None:
            self._debug(self._grab(), "w3_no_loading")
            if vision.seen(self._grab(), "teleport_button"):
                self._act(kind="press", key="esc")  # leave the map open state tidy
            return WarplanResult(False, "teleport did not start (no loading screen)")
        if self._wait_for(lambda f: not vision.loading(f), cfg.arrive_timeout_s) is None:
            return WarplanResult(False, "stuck on a loading screen")
        self._sleep(cfg.settle_s)
        arrived = self._grab()
        if vision.seen(arrived, "helltide_label"):
            self._debug(arrived, "w4_helltide")
            log.info("Warplan: arrived in the Helltide")
            return WarplanResult(True)
        if "ichorfall" in self._templates_names() and vision.seen(arrived, "ichorfall"):
            self._debug(arrived, "w4_not_helltide")
            return WarplanResult(False, "landed, but not in a Helltide (back in Ichorfall: no Helltide right now?)")
        # Usually the waypoint town next to the Helltide (live: Kyovashad); the
        # Helltide timer only shows once you walk out of town.
        self._debug(arrived, "w4_waypoint_town")
        log.info("Warplan: arrived at the Helltide's waypoint town")
        return WarplanResult(True)

    def _templates_names(self) -> set[str]:
        return set(self.vision._templates)
