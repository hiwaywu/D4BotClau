"""Town routine: town portal, open the stash, move the inventory into it.

Default town is Ichorfall: the stash chest is on screen at the portal landing
spot (user's pick after Temis needed a walk that kept failing).

All positions were measured on the 2560x1440 client (HUD Medium) from the
user's screenshots (data/town/snap_*.png) and scale with the frame size.

Safety: a right-click on an inventory item with the stash CLOSED equips or
uses it. Every right-click batch is preceded by a fresh check that the stash
panel is open, and the routine aborts the moment it is not.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)

BASE_W, BASE_H = 2560, 1440

# Inventory panel (right side): 11 x 3 grid, five category tabs above it.
INV_XS = (1688, 1761, 1834, 1908, 1981, 2055, 2128, 2202, 2275, 2349, 2422, 2496)
INV_YS = (962, 1069, 1177, 1285)
INV_TABS = ((2209, 917), (2275, 917), (2341, 917), (2407, 917), (2471, 917))
INV_TAB_NAMES = ("equipment", "talismans", "socketables", "consumables", "keys")
# Stash panel (left side): seven chest tabs.
STASH_TABS = ((220, 226), (302, 226), (385, 226), (468, 226), (551, 226), (634, 226), (716, 226))

# Template search areas (x, y, w, h).
TEMIS_AREA = (2200, 0, 360, 70)
STASH_HEADER_AREA = (300, 60, 350, 130)
CHEST_AREA = (0, 0, 2560, 1300)


# Where the stash chest sits on screen once standing at the landing spot.
TOWN_CHEST = {
    "ichorfall": (930, 555),   # visible right at the portal landing
    "temis": (2183, 800),      # only after walking up the stairs
}


@dataclass(frozen=True)
class TownConfig:
    town: str = "ichorfall"
    town_key: str = "t"
    portal_timeout_s: float = 25.0
    settle_s: float = 2.0
    # Open floor just right of the town-portal landing spot (user: the stash is a
    # little to the right, off screen). (0.74, 0.44) hit NPCs/players.
    walk_point: tuple[float, float] = (0.62, 0.58)
    walk_timeout_s: float = 6.0       # per walk click: wait this long for the character to stop
    walk_attempts: int = 5
    still_diff: float = 8.0           # view change while walking ~33, standing ~3
    chest_expected: tuple[int, int] | None = None   # default: TOWN_CHEST[town]
    hover_points: int = 60
    hover_wait_s: float = 0.12
    arrive_settle_s: float = 6.0      # after the portal: wait for the view to settle (max)
    town_threshold: float = 0.62    # Temis 0.64-1.00 / Ichorfall 1.00; other zone names <= 0.54
    loading_timeout_s: float = 10.0  # T must show a loading screen within this
    stash_threshold: float = 0.80   # stash open 1.00, closed <= 0.24
    chest_threshold: float = 0.80   # chest on screen 0.84-1.00, elsewhere <= 0.67
    click_gap_s: float = 0.12
    after_batch_s: float = 0.5
    templates: str = "templates/town"


@dataclass
class TownResult:
    ok: bool
    reason: str = ""
    moved: dict[str, int] = field(default_factory=dict)
    left: dict[str, int] = field(default_factory=dict)
    stash_tab: int = -1


def _scale(frame: np.ndarray) -> tuple[float, float]:
    h, w = frame.shape[:2]
    return w / BASE_W, h / BASE_H


def _pt(frame: np.ndarray, x: float, y: float) -> tuple[int, int]:
    sx, sy = _scale(frame)
    return int(round(x * sx)), int(round(y * sy))


class TownVision:
    def __init__(self, root: Path, config: TownConfig):
        self.config = config
        folder = root / config.templates
        self._templates: dict[str, np.ndarray] = {}
        files = {
            "town": config.town,
            "chest": f"chest_{config.town}",
            "stash_header": "stash_header",
            "stash_tooltip": "stash_tooltip",
        }
        for key, name in files.items():
            image = cv2.imread(str(folder / f"{name}.png"), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise FileNotFoundError(f"town template missing: {folder / name}.png")
            self._templates[key] = image
        self._scaled: dict[tuple[str, int], np.ndarray] = {}

    def _match(self, frame: np.ndarray, name: str, area: tuple[int, int, int, int]) -> tuple[float, tuple[int, int]]:
        sx, sy = _scale(frame)
        key = (name, frame.shape[0])
        if key not in self._scaled:
            t = self._templates[name]
            if abs(sx - 1.0) > 0.01:
                t = cv2.resize(t, (max(4, int(t.shape[1] * sx)), max(4, int(t.shape[0] * sy))), interpolation=cv2.INTER_AREA)
            self._scaled[key] = t
        t = self._scaled[key]
        x, y, w, h = (int(area[0] * sx), int(area[1] * sy), int(area[2] * sx), int(area[3] * sy))
        roi = cv2.cvtColor(frame[y : y + h, x : x + w], cv2.COLOR_BGR2GRAY)
        if roi.shape[0] < t.shape[0] or roi.shape[1] < t.shape[1]:
            return 0.0, (0, 0)
        result = cv2.matchTemplate(roi, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        return float(np.nan_to_num(score)), (x + loc[0] + t.shape[1] // 2, y + loc[1] + t.shape[0] // 2)

    def in_town(self, frame: np.ndarray) -> bool:
        return self._match(frame, "town", TEMIS_AREA)[0] >= self.config.town_threshold

    in_temis = in_town  # older name

    def stash_open(self, frame: np.ndarray) -> bool:
        return self._match(frame, "stash_header", STASH_HEADER_AREA)[0] >= self.config.stash_threshold

    @staticmethod
    def loading(frame: np.ndarray) -> bool:
        """Black screen or loading art (the brief fade of a town portal, or a full load)."""
        from .warplan import is_loading

        return is_loading(frame)

    def stash_tooltip(self, frame: np.ndarray) -> bool:
        """The "Stash" label shown while hovering the chest: 1.00 shown, <= 0.41 otherwise."""
        return self._match(frame, "stash_tooltip", CHEST_AREA)[0] >= 0.7

    def find_chest(self, frame: np.ndarray) -> tuple[float, tuple[int, int]]:
        return self._match(frame, "chest", CHEST_AREA)


def occupied_cells(frame: np.ndarray) -> list[tuple[int, int, tuple[int, int]]]:
    """(row, col, centre) of inventory cells holding an item.

    Empty cells are dark flat stone (mean ~40, std <= 7); items are bright
    (mean >= 79) or busy (std >= 24) on every tab in the user's screenshots.
    """
    sx, sy = _scale(frame)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    cells = []
    for r in range(len(INV_YS) - 1):
        for c in range(len(INV_XS) - 1):
            x0, x1 = int((INV_XS[c] + 6) * sx), int((INV_XS[c + 1] - 6) * sx)
            y0, y1 = int((INV_YS[r] + 6) * sy), int((INV_YS[r + 1] - 6) * sy)
            cell = gray[y0:y1, x0:x1].astype(np.float32)
            if cell.size and (cell.mean() > 60 or cell.std() > 15):
                cells.append((r, c, ((x0 + x1) // 2, (y0 + y1) // 2)))
    return cells


def active_tab(frame: np.ndarray, buttons: tuple[tuple[int, int], ...]) -> int | None:
    """Index of the highlighted (red) tab button, if exactly one is."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    sx, sy = _scale(frame)
    lit = []
    for i, (bx, by) in enumerate(buttons):
        x, y = int(bx * sx), int(by * sy)
        # Active buttons: 0.43-0.59 red; inactive: 0.00. The icon in the
        # middle is white, so look at the whole button, not its centre.
        r = max(6, int(26 * sx))
        patch = hsv[max(0, y - r) : y + r, max(0, x - r) : x + r]
        red = ((patch[..., 0] <= 8) | (patch[..., 0] >= 172)) & (patch[..., 1] > 150) & (patch[..., 2] > 110)
        if red.mean() > 0.2:
            lit.append(i)
    return lit[0] if len(lit) == 1 else None


def _replace_expected(config: TownConfig, xy: tuple[int, int]) -> TownConfig:
    from dataclasses import replace

    return replace(config, chest_expected=xy, hover_points=30)


def load_stash_path(root: Path, config: TownConfig) -> list[dict] | None:
    path = root / config.templates / "stash_path.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("clicks", [])) or None


def save_stash_path(root: Path, config: TownConfig, clicks: list[dict]) -> Path:
    path = root / config.templates / "stash_path.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"resolution": [BASE_W, BASE_H], "clicks": clicks}
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


class TownRunner:
    """Runs the town routine step by step, checking the screen after every step.

    `grab()` returns (frame, window, region); `send(actions)` sends a list of
    brain.Action through the InputController (which enforces F8/foreground).
    """

    def __init__(self, vision: TownVision, config: TownConfig, grab, send, stopped, sleep=time.sleep, clock=time.monotonic, debug=None):  # type: ignore[no-untyped-def]
        self.vision = vision
        self.config = config
        self._grab = grab
        self._send = send
        self._stopped = stopped
        self._sleep = sleep
        self._clock = clock
        self._debug = debug or (lambda frame, label: None)
        self.path: list[dict] | None = None

    # -- helpers ----------------------------------------------------------
    def _press(self, key: str) -> None:
        from .brain import Action

        self._send([Action(kind="press", key=key)])

    def _click(self, xy: tuple[int, int], kind: str = "click") -> None:
        from .brain import Action

        self._send([Action(kind=kind, x=int(xy[0]), y=int(xy[1]))])

    def _wait_for(self, check, timeout: float, poll: float = 0.15):  # type: ignore[no-untyped-def]
        end = self._clock() + timeout
        while self._clock() < end:
            if self._stopped():
                return None
            frame = self._grab()
            if check(frame):
                return frame
            self._sleep(poll)
        return None

    # -- steps ------------------------------------------------------------
    def go_to_town(self) -> bool:
        """Always press T, even in town: it lands on the same spot every time.

        Success = a loading screen, then the town's zone name. Requiring the
        loading screen means a look-alike zone name can never skip the portal.
        """
        cfg = self.config
        self._debug(self._grab(), "00_start")
        for attempt in range(2):
            log.info("Town: pressing %s (town portal), attempt %d", cfg.town_key, attempt + 1)
            self._press(cfg.town_key)
            if self._wait_for(self.vision.loading, cfg.loading_timeout_s, poll=0.05) is None:
                log.warning("Town: no loading screen after %s", cfg.town_key)
                self._debug(self._grab(), f"01_no_loading_{attempt + 1}")
                continue
            arrived = self._wait_for(
                lambda f: not self.vision.loading(f) and self.vision.in_town(f), cfg.portal_timeout_s
            )
            if arrived is not None:
                # User: it started walking before the arrival had settled.
                self._sleep(cfg.settle_s)
                self._wait_still(cfg.arrive_settle_s)
                self._debug(self._grab(), "02_in_town")
                return True
            self._debug(self._grab(), f"02_not_in_town_{attempt + 1}")
        return False

    def _wait_still(self, timeout: float) -> bool:
        """Wait until the view stops moving (character arrived / stopped)."""
        end = self._clock() + timeout
        previous = None
        still = 0
        while self._clock() < end and not self._stopped():
            frame = self._grab()
            small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.float32)
            if previous is not None and float(np.abs(small - previous).mean()) < self.config.still_diff:
                still += 1
                if still >= 3:
                    return True
            else:
                still = 0
            previous = small
            self._sleep(0.15)
        return False

    def _hover_for_stash(self) -> tuple[int, int] | None:
        """Hover candidate spots until the game shows the "Stash" label.

        The label is UI text, so unlike the chest itself it looks the same at
        any time of day (live run 2: the chest template scored 0.61 in new
        lighting while standing right next to it).
        """
        from .brain import Action

        frame = self._grab()
        cx, cy = _pt(frame, *(self.config.chest_expected or TOWN_CHEST[self.config.town]))
        sx, _ = _scale(frame)
        step = int(110 * sx)
        points = [
            (cx + dx * step, cy + dy * step)
            for dx in range(-6, 4)
            for dy in range(-3, 3)
        ]
        points.sort(key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)
        h, w = frame.shape[:2]
        for x, y in points[: self.config.hover_points]:
            if self._stopped():
                return None
            if not (0 < x < w and int(0.08 * h) < y < int(0.80 * h)):
                continue
            self._send([Action(kind="aim", x=int(x), y=int(y))])
            self._sleep(self.config.hover_wait_s)
            seen = self._grab()
            if self.vision.stash_tooltip(seen):
                self._debug(seen, "03_stash_hover")
                return int(x), int(y)
        return None

    def replay_path(self, path: list[dict]) -> bool:
        """Replay the user's recorded clicks from the landing spot to the stash."""
        frame = self._grab()
        sx, sy = _scale(frame)
        for i, step in enumerate(path):
            if self._stopped():
                return False
            self._sleep(max(0.25, float(step.get("delay", 0.5))))
            x, y = int(step["x"] * sx), int(step["y"] * sy)
            log.info("Town: recorded click %d/%d at %d,%d", i + 1, len(path), x, y)
            self._click((x, y))
            if i < len(path) - 1:
                # Let the walk finish before the next click, like the user did.
                self._sleep(0.3)
                self._wait_still(self.config.walk_timeout_s)
        if self._wait_for(self.vision.stash_open, 5.0) is not None:
            log.info("Town: stash open (recorded path)")
            return True
        self._debug(self._grab(), "03_after_replay")
        return False

    def open_stash(self) -> bool:
        """Replay the taught path if there is one; otherwise search for the stash."""
        cfg = self.config
        path = self.path or []
        if path:
            if self.replay_path(path):
                return True
            # Someone stood in the way? The chest is where the last click was.
            last = path[-1]
            frame = self._grab()
            self.config = _replace_expected(cfg, (int(last["x"]), int(last["y"])))
            target = self._hover_for_stash()
            self.config = cfg
            if target is not None:
                self._click(target)
                if self._wait_for(self.vision.stash_open, 4.0) is not None:
                    return True
            log.warning("Town: recorded path did not open the stash")
            return False
        for attempt in range(cfg.walk_attempts):
            if self._stopped():
                return False
            frame = self._grab()
            if self.vision.stash_open(frame):
                return True
            # Fast path: the chest template, when the lighting matches.
            score, centre = self.vision.find_chest(frame)
            target = centre if score >= cfg.chest_threshold else self._hover_for_stash()
            if target is not None:
                log.info("Town: stash found at %s; clicking it", target)
                self._click(target)
                if self._wait_for(self.vision.stash_open, 4.0) is not None:
                    log.info("Town: stash open")
                    return True
                continue
            if cfg.town != "temis":
                # The chest is visible from the landing spot; walking only
                # makes things worse. Give the scene a moment and look again.
                self._sleep(1.0)
                continue
            # Not in view yet: walk toward it. A single click right after
            # loading was swallowed in live run 3, so re-click until the view
            # actually moves, then wait for the character to stop.
            h, w = frame.shape[:2]
            self._debug(frame, f"03_walk_{attempt + 1}")
            log.info("Town: walking toward the stash (attempt %d, chest score %.2f, no hover label)", attempt + 1, score)
            before = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.float32)
            moved = False
            for click in range(3):
                self._click((int(w * cfg.walk_point[0]), int(h * cfg.walk_point[1])))
                self._sleep(0.6)
                now_small = cv2.resize(cv2.cvtColor(self._grab(), cv2.COLOR_BGR2GRAY), (160, 90)).astype(np.float32)
                if float(np.abs(now_small - before).mean()) >= cfg.still_diff:
                    moved = True
                    break
                log.info("Town: walk click %d did not move the view; clicking again", click + 1)
            if not moved:
                log.warning("Town: the character is not moving")
            self._wait_still(cfg.walk_timeout_s)
        if self._stopped():
            log.warning("Town: open_stash interrupted (F8 or focus lost)")
        return False

    def _select_tab(self, buttons, index: int) -> bool:  # type: ignore[no-untyped-def]
        frame = self._grab()
        if active_tab(frame, buttons) == index:
            return True
        for _ in range(3):
            self._click(_pt(frame, *buttons[index]))
            if self._wait_for(lambda f: active_tab(f, buttons) == index, 1.5) is not None:
                return True
        return False

    def empty_inventory(self, result: TownResult) -> bool:
        cfg = self.config
        frame = self._grab()
        stash_tab = active_tab(frame, STASH_TABS)
        stash_tab = 0 if stash_tab is None else stash_tab
        if not self._select_tab(STASH_TABS, stash_tab):
            result.reason = "could not select a stash tab"
            return False
        tabs_tried = 1
        for index, name in enumerate(INV_TAB_NAMES):
            if not self._select_tab(INV_TABS, index):
                result.reason = f"could not open inventory tab {name}"
                return False
            moved = 0
            no_progress = 0
            for _ in range(12):
                if self._stopped():
                    result.reason = "stopped (F8)"
                    return False
                frame = self._grab()
                if not self.vision.stash_open(frame):
                    # Right-clicking with the stash closed would equip/use items.
                    result.reason = "stash closed unexpectedly"
                    return False
                cells = occupied_cells(frame)
                if not cells:
                    break
                for _, _, centre in cells:
                    # Re-check before EVERY right-click: if the stash closed
                    # (hit by something, a menu opened) the click would equip
                    # or use the item instead.
                    if self._stopped():
                        result.reason = "stopped (F8)"
                        return False
                    if not self.vision.stash_open(self._grab()):
                        result.reason = "stash closed unexpectedly"
                        return False
                    self._click(centre, kind="rclick")
                    self._sleep(cfg.click_gap_s)
                self._sleep(cfg.after_batch_s)
                after = self._grab()
                if not self.vision.stash_open(after):
                    result.reason = "stash closed unexpectedly"
                    return False
                remaining = occupied_cells(after)
                moved += max(0, len(cells) - len(remaining))
                if len(remaining) < len(cells):
                    no_progress = 0
                    continue
                # Nothing moved: this stash tab is full (or the items cannot
                # be stashed). Try the next stash tabs; three fresh tabs in a
                # row taking nothing means the items cannot be stashed.
                no_progress += 1
                if no_progress >= 3 or tabs_tried >= len(STASH_TABS):
                    log.warning("Town: %d %s item(s) could not be stashed", len(remaining), name)
                    result.left[name] = len(remaining)
                    break
                stash_tab = (stash_tab + 1) % len(STASH_TABS)
                tabs_tried += 1
                log.info("Town: stash tab full; switching to chest %d", stash_tab + 1)
                if not self._select_tab(STASH_TABS, stash_tab):
                    result.reason = "could not switch stash tab"
                    return False
            result.moved[name] = moved
            log.info("Town: %s: moved %d item(s)", name, moved)
        result.stash_tab = stash_tab
        return True

    def run(self) -> TownResult:
        result = TownResult(ok=False)
        if not self.go_to_town():
            result.reason = f"town portal did not reach {self.config.town.title()}"
            return result
        if not self.open_stash():
            result.reason = "could not open the stash"
            self._debug(self._grab(), "04_no_stash")
            return result
        self._debug(self._grab(), "04_stash_open")
        ok = self.empty_inventory(result)
        self._debug(self._grab(), "05_done")
        # Esc only while the stash is open: with nothing open it opens the game menu.
        if self.vision.stash_open(self._grab()):
            self._sleep(1.0)
            self._press("esc")
            self._sleep(1.5)
        result.ok = ok
        return result
