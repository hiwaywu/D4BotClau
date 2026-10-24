"""Recorded routes: a stitched minimap of a Helltide zone plus a walked path.

Recording (no input sent): every few frames the minimap is placed into a big
mosaic at the player's position. The position comes from frame-to-frame
minimap motion, corrected by matching the new minimap against what is
already in the mosaic, so it does not drift.

Playback: matching the live minimap against the mosaic gives the player's
absolute position in that zone. The path then works as a guide: follow it to
its end (usually a big open area), then hunt freely on a leash around there.

Only static terrain is matched: monster dots, loot stars and the player arrow
are painted over before matching or stitching.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import NavConfig, Region
from .minimap import dot_masks

log = logging.getLogger(__name__)

CANVAS = 4096          # mosaic canvas (minimap px); a zone fits comfortably
SAMPLE_EVERY_PX = 6.0  # add a route point after moving this far


def terrain_gray(crop: np.ndarray, config: NavConfig) -> np.ndarray:
    """Grayscale minimap with moving content (dots, stars, player arrow) removed."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    monster, elite = dot_masks(crop, config)
    dynamic = cv2.bitwise_or(monster, elite)
    h, w = gray.shape
    cv2.circle(dynamic, (w // 2, h // 2), 24, 255, -1)  # arrow + the star behind it
    dynamic = cv2.dilate(dynamic, np.ones((5, 5), np.uint8))
    filled = cv2.medianBlur(gray, 21)
    gray = np.where(dynamic > 0, filled, gray).astype(np.uint8)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _match(image: np.ndarray, template: np.ndarray) -> tuple[float, tuple[int, int]]:
    if image.shape[0] < template.shape[0] or image.shape[1] < template.shape[1]:
        return -1.0, (0, 0)
    result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return float(np.nan_to_num(score)), loc


class RouteRecorder:
    """Builds a mosaic + path from a stream of minimap crops."""

    def __init__(self, config: NavConfig):
        self.config = config
        self._sum = np.zeros((CANVAS, CANVAS), dtype=np.float32)
        self._count = np.zeros((CANVAS, CANVAS), dtype=np.float32)
        self._pos: np.ndarray | None = None  # canvas coords of the player
        self._prev: np.ndarray | None = None
        self._window: np.ndarray | None = None
        self.path: list[tuple[float, float]] = []
        self.corrections = 0
        self.frames = 0
        self._warned = False

    def add(self, crop: np.ndarray) -> None:
        gray = terrain_gray(crop, self.config)
        h, w = gray.shape
        if self._pos is None:
            self._pos = np.array([CANVAS / 2.0, CANVAS / 2.0])
        else:
            # Predict from frame-to-frame motion, then correct against the mosaic.
            g = gray.astype(np.float32)
            if self._window is None or self._window.shape != g.shape:
                self._window = cv2.createHanningWindow((w, h), cv2.CV_32F)
            (sx, sy), response = cv2.phaseCorrelate(self._prev, g, self._window)
            if response >= 0.05 and math.hypot(sx, sy) <= 40:
                self._pos = self._pos - np.array([sx, sy])
            self._pos = self._refine(gray, self._pos)
        self._prev = gray.astype(np.float32)
        self._paste(gray, self._pos)
        self.frames += 1
        if not self.path or math.hypot(self._pos[0] - self.path[-1][0], self._pos[1] - self.path[-1][1]) >= SAMPLE_EVERY_PX:
            self.path.append((float(self._pos[0]), float(self._pos[1])))

    def _refine(self, gray: np.ndarray, pos: np.ndarray, search: int = 14) -> np.ndarray:
        h, w = gray.shape
        x0 = int(round(pos[0] - w / 2)) - search
        y0 = int(round(pos[1] - h / 2)) - search
        region_count = self._count[y0 : y0 + h + 2 * search, x0 : x0 + w + 2 * search]
        if region_count.size == 0 or (region_count > 0).mean() < 0.6:
            return pos
        mosaic = self._mosaic_patch(x0, y0, w + 2 * search, h + 2 * search)
        # Match the inner part of the frame (its edges are partly new ground).
        mh, mw = int(h * 0.15), int(w * 0.15)
        template = gray[mh : h - mh, mw : w - mw]
        score, (lx, ly) = _match(mosaic, template)
        if score < 0.55:
            return pos
        self.corrections += 1
        # The template is the frame's inner part, offset (mw, mh) from its corner.
        return np.array([x0 + lx - mw + w / 2.0, y0 + ly - mh + h / 2.0])

    def _mosaic_patch(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        s = self._sum[y : y + h, x : x + w]
        c = self._count[y : y + h, x : x + w]
        return np.where(c > 0, s / np.maximum(c, 1), 0).astype(np.uint8)

    def _paste(self, gray: np.ndarray, pos: np.ndarray) -> None:
        h, w = gray.shape
        x = int(round(pos[0] - w / 2))
        y = int(round(pos[1] - h / 2))
        if x < 0 or y < 0 or x + w > CANVAS or y + h > CANVAS:
            if not self._warned:
                log.warning("Route recording left the canvas; the mosaic stops here")
                self._warned = True
            return
        self._sum[y : y + h, x : x + w] += gray
        self._count[y : y + h, x : x + w] += 1

    def save(self, folder: Path, name: str) -> Path:
        ys, xs = np.nonzero(self._count)
        if len(xs) == 0 or not self.path:
            raise ValueError("nothing recorded")
        x0, y0, x1, y1 = xs.min(), ys.min(), xs.max() + 1, ys.max() + 1
        mosaic = self._mosaic_patch(int(x0), int(y0), int(x1 - x0), int(y1 - y0))
        covered = (self._count[y0:y1, x0:x1] > 0).astype(np.uint8) * 255
        path = [(px - x0, py - y0) for px, py in self.path]
        start, end = path[0], path[-1]
        out = folder / name
        out.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(out / "mosaic.png"), mosaic)
        cv2.imwrite(str(out / "covered.png"), covered)
        meta = {
            "name": name,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "path": [[round(px, 1), round(py, 1)] for px, py in path],
            "loop": math.hypot(end[0] - start[0], end[1] - start[1]) <= 40.0,
            "frames": self.frames,
        }
        (out / "route.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return out


@dataclass
class RouteFix:
    valid: bool
    x: float = 0.0
    y: float = 0.0
    score: float = 0.0
    name: str = ""


@dataclass
class RouteMap:
    name: str
    mosaic: np.ndarray
    covered: np.ndarray
    path: np.ndarray            # (N, 2) mosaic coords
    loop: bool
    _small: np.ndarray | None = field(default=None, repr=False)
    max_correction_px: float = 25.0  # dead reckoning is better than this over a few frames

    @classmethod
    def load(cls, folder: Path) -> "RouteMap":
        meta = json.loads((folder / "route.json").read_text(encoding="utf-8"))
        mosaic = cv2.imread(str(folder / "mosaic.png"), cv2.IMREAD_GRAYSCALE)
        covered = cv2.imread(str(folder / "covered.png"), cv2.IMREAD_GRAYSCALE)
        if mosaic is None or covered is None:
            raise ValueError(f"route {folder} is missing mosaic.png/covered.png")
        return cls(
            name=str(meta.get("name", folder.name)),
            mosaic=mosaic,
            covered=covered,
            path=np.array(meta["path"], dtype=np.float64),
            loop=bool(meta.get("loop", False)),
        )

    def _inside(self, cx: float, cy: float, w: int, h: int, minimum: float = 0.85) -> bool:
        """Was (most of) a frame centred here actually seen while recording?

        Past the edge of the recording, matching finds look-alike terrain
        somewhere else (sim: a fix 800px off), so such matches are rejected.
        """
        x, y = int(round(cx - w / 2)), int(round(cy - h / 2))
        window = self.covered[max(0, y) : y + h, max(0, x) : x + w]
        return window.size > 0 and float((window > 0).sum()) / float(w * h) >= minimum

    def locate(self, gray: np.ndarray, prior: tuple[float, float] | None, search: int = 40) -> RouteFix:
        """Player position in mosaic coords. Local search near prior, else global."""
        h, w = gray.shape
        mh, mw = int(h * 0.15), int(w * 0.15)
        template = gray[mh : h - mh, mw : w - mw]
        th, tw = template.shape
        if prior is not None:
            if not self._inside(prior[0], prior[1], w, h):
                # Off the recorded map: matching would snap onto its edge.
                return RouteFix(False, name=self.name)
            x0 = int(round(prior[0] - w / 2)) + mw - search
            y0 = int(round(prior[1] - h / 2)) + mh - search
            x0c, y0c = max(0, x0), max(0, y0)
            patch = self.mosaic[y0c : y0 + th + 2 * search, x0c : x0 + tw + 2 * search]
            score, (lx, ly) = _match(patch, template)
            cx, cy = x0c + lx - mw + w / 2.0, y0c + ly - mh + h / 2.0
            close = math.hypot(cx - prior[0], cy - prior[1]) <= self.max_correction_px
            if score >= 0.5 and close and self._inside(cx, cy, w, h):
                return RouteFix(True, cx, cy, score, self.name)
            # Never jump to a far-away look-alike while we still know roughly
            # where we are; the caller keeps the fix by dead reckoning.
            return RouteFix(False, score=score, name=self.name)
        # Global: coarse at half size, then refine locally.
        if self._small is None:
            self._small = cv2.resize(self.mosaic, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        small_t = cv2.resize(template, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        score, (lx, ly) = _match(self._small, small_t)
        if score < 0.45:
            return RouteFix(False, score=score, name=self.name)
        guess = (lx * 2 - mw + w / 2.0, ly * 2 - mh + h / 2.0)
        if not self._inside(guess[0], guess[1], w, h):
            return RouteFix(False, score=score, name=self.name)
        if prior is None:
            refined = self.locate(gray, guess, search=6)
            if refined.valid:
                return refined
        return RouteFix(True, guess[0], guess[1], score, self.name)


def load_routes(folder: Path) -> list[RouteMap]:
    routes = []
    if not folder.is_dir():
        return routes
    for sub in sorted(folder.iterdir()):
        if (sub / "route.json").is_file():
            try:
                routes.append(RouteMap.load(sub))
            except Exception:
                log.exception("Could not load route %s", sub)
    return routes


class RouteLocator:
    """Keeps a running fix on whichever recorded zone the player is in."""

    def __init__(self, routes: list[RouteMap], config: NavConfig, every_n: int = 3):
        self.routes = routes
        self.config = config
        self.every_n = every_n
        self._frame = 0
        self._active: RouteMap | None = None
        self._fix = RouteFix(False)
        self._misses = 0
        self.max_misses = 200  # locate attempts (every every_n frames): ~1 min at 10 fps

    @property
    def active(self) -> RouteMap | None:
        return self._active

    def update(self, crop: np.ndarray, motion: tuple[float, float]) -> RouteFix:
        self._frame += 1
        if self._fix.valid:
            # Between matches, carry the fix along with minimap motion.
            self._fix = RouteFix(True, self._fix.x + motion[0], self._fix.y + motion[1], self._fix.score, self._fix.name)
        if self._frame % self.every_n and self._fix.valid:
            return self._fix
        gray = terrain_gray(crop, self.config)
        if self._active is not None:
            prior = (self._fix.x, self._fix.y) if self._fix.valid else None
            fix = self._active.locate(gray, prior)
            if fix.valid:
                self._fix, self._misses = fix, 0
                return fix
            # Off the recorded area: keep the fix by dead reckoning (minimap
            # motion) for a while, so the leash still knows the way back.
            self._misses += 1
            if self._misses < self.max_misses:
                return self._fix
            self._active, self._fix = None, RouteFix(False)
        best = RouteFix(False)
        for route in self.routes:
            fix = route.locate(gray, None)
            if fix.valid and fix.score > best.score:
                best, self._active = fix, route
        if best.valid:
            log.info("Route: located in %r (score %.2f)", best.name, best.score)
        self._fix = best
        return best


@dataclass
class RouteFollower:
    """Turns a fix into a goal: follow the path, then stay on a leash at its end."""

    lookahead: int = 5          # path points ahead (~6px each)
    leash_px: float = 140.0
    window: int = 40            # only search this many points around the last index
    index: int = -1
    done: bool = False

    def reset(self) -> None:
        self.index, self.done = -1, False

    def goal(self, route: RouteMap, fix: RouteFix) -> tuple[float, float, str] | None:
        """(goal dx, goal dy, mode) relative to the player, or None when free to roam."""
        path = route.path
        here = np.array([fix.x, fix.y])
        if self.index < 0:
            self.index = int(np.argmin(np.linalg.norm(path - here, axis=1)))
        else:
            lo, hi = max(0, self.index - self.window), min(len(path), self.index + self.window)
            self.index = lo + int(np.argmin(np.linalg.norm(path[lo:hi] - here, axis=1)))
        last = len(path) - 1
        if not self.done and self.index >= last - 1:
            if route.loop:
                self.index = 0
            else:
                self.done = True
        if self.done:
            end = path[-1]
            if np.linalg.norm(end - here) > self.leash_px:
                return float(end[0] - fix.x), float(end[1] - fix.y), "leash"
            return None
        target = path[min(self.index + self.lookahead, last)]
        return float(target[0] - fix.x), float(target[1] - fix.y), "route"
