"""Minimap perception: open directions, map motion, and dead-reckoned position.

The minimap is player-centred, so:
  * casting rays from its centre over a walkable-pixel mask tells us which
    directions are open ground and which run into cliffs or the zone edge;
  * the frame-to-frame shift of the minimap image (phase correlation) is the
    player's motion. Integrating it gives a rough position for stuck detection
    and "don't go back where we've been" exploration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import NavConfig, Region


@dataclass(frozen=True)
class Pack:
    """A group of monster dots on the minimap, relative to the player (px)."""

    dx: float
    dy: float
    dots: int
    elite: bool
    clear: bool = True  # straight line from the player stays on walkable ground

    @property
    def distance(self) -> float:
        return math.hypot(self.dx, self.dy)

    @property
    def angle(self) -> float:
        return math.atan2(self.dy, self.dx)


@dataclass(frozen=True)
class MinimapReading:
    valid: bool
    angles: np.ndarray = field(default_factory=lambda: np.zeros(0))     # radians, image coords (y down)
    clearance: np.ndarray = field(default_factory=lambda: np.zeros(0))  # 0..1 per angle
    shift: tuple[float, float] = (0.0, 0.0)  # player motion this frame, minimap px
    response: float = 0.0
    position: tuple[float, float] = (0.0, 0.0)
    walkable_ratio: float = 0.0
    region: tuple[int, int, int, int] = (0, 0, 0, 0)
    packs: tuple[Pack, ...] = ()
    loot: tuple[tuple[float, float], ...] = ()  # legendary stars, offset from the player


INVALID = MinimapReading(valid=False)


def _hsv_mask(hsv: np.ndarray, ranges) -> np.ndarray:  # type: ignore[no-untyped-def]
    mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for low, high in ranges:
        mask |= cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
    return mask


def dot_masks(crop: np.ndarray, config: NavConfig) -> tuple[np.ndarray, np.ndarray]:
    """(monster, elite) dot masks: Subo's yellow dots and red dots/skull."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    return _hsv_mask(hsv, config.monster_hsv), _hsv_mask(hsv, config.elite_hsv)


def _star_template(outer: float = 13.0, inner: float = 5.5, pad: int = 3) -> np.ndarray:
    size = int(2 * (outer + pad))
    image = np.zeros((size, size), dtype=np.uint8)
    c = size / 2.0
    points = [
        (c + (outer if i % 2 == 0 else inner) * math.sin(i * math.pi / 5),
         c - (outer if i % 2 == 0 else inner) * math.cos(i * math.pi / 5))
        for i in range(10)
    ]
    cv2.fillPoly(image, [np.array(points, dtype=np.int32)], 255)
    return image


STAR = _star_template()


def find_stars(crop: np.ndarray, config: NavConfig, threshold: float) -> list[tuple[int, int]]:
    """Centres (crop px) of yellow stars: legendary/unique drops on the minimap."""
    monster, _ = dot_masks(crop, config)
    if monster.shape[0] < STAR.shape[0] or monster.shape[1] < STAR.shape[1]:
        return []
    result = cv2.matchTemplate(monster, STAR, cv2.TM_CCOEFF_NORMED)
    stars: list[tuple[int, int]] = []
    th, tw = STAR.shape[:2]
    for _ in range(6):
        _, score, _, loc = cv2.minMaxLoc(result)
        if score < threshold:
            break
        stars.append((loc[0] + tw // 2, loc[1] + th // 2))
        cv2.rectangle(result, (loc[0] - tw, loc[1] - th), (loc[0] + tw, loc[1] + th), -1.0, -1)
    return stars


def monster_packs(
    crop: np.ndarray, config: NavConfig, stars: list[tuple[int, int]] | None = None
) -> tuple[Pack, ...]:
    """Group monster dots into packs, positions relative to the minimap centre."""
    monster, elite = dot_masks(crop, config)
    for sx, sy in stars or []:  # a loot star is not a monster
        half = STAR.shape[0] // 2 + 2
        monster[max(0, sy - half) : sy + half, max(0, sx - half) : sx + half] = 0
    # Elite dots are small (11-24 px area). Large red shapes are map icons
    # (live: the red sword marker, the spiky event icon) - not enemies; the
    # bot got stuck attacking the sword. Drop them, and any yellow blob that
    # touches one (the event icon's small flame).
    n_red, red_labels, red_stats, _ = cv2.connectedComponentsWithStats(elite)
    big = np.zeros_like(elite)
    for i in range(1, n_red):
        if red_stats[i][cv2.CC_STAT_AREA] > config.max_elite_dot_px:
            big[red_labels == i] = 255
    if big.any():
        elite = cv2.bitwise_and(elite, cv2.bitwise_not(big))
        near_icon = cv2.dilate(big, np.ones((9, 9), np.uint8))
        n_y, y_labels = cv2.connectedComponents(monster)
        for i in set(np.unique(y_labels[near_icon > 0]).tolist()) - {0}:
            monster[y_labels == i] = 0
    # Same for yellow: monster dots are 18-24 px. Every yellow blob over ~45 px
    # on 11k live minimap blobs was an icon (event flames, gem-marker tips, the
    # orange objective triangle the bot got stuck on). Drop those, and any red
    # dot stuck to one (the triangle carries a small red dot).
    n_y, y_labels, y_stats, _ = cv2.connectedComponentsWithStats(monster)
    big_yellow = np.zeros_like(monster)
    for i in range(1, n_y):
        if y_stats[i][cv2.CC_STAT_AREA] > config.max_monster_dot_px:
            big_yellow[y_labels == i] = 255
    if big_yellow.any():
        monster = cv2.bitwise_and(monster, cv2.bitwise_not(big_yellow))
        near_icon = cv2.dilate(big_yellow, np.ones((9, 9), np.uint8))
        n_r, r_labels = cv2.connectedComponents(elite)
        for i in set(np.unique(r_labels[near_icon > 0]).tolist()) - {0}:
            elite[r_labels == i] = 0
    dots = cv2.bitwise_or(monster, elite)
    n, labels_dots, stats, centers = cv2.connectedComponentsWithStats(dots)
    # A yellow star sits behind the player arrow (the player's own marker,
    # live run 6). Drop any blob touching the arrow: the arrow is the bright,
    # unsaturated shape at the centre.
    h0, w0 = crop.shape[:2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    near = np.zeros((h0, w0), dtype=np.uint8)
    cv2.circle(near, (w0 // 2, h0 // 2), 26, 255, -1)
    arrow = ((hsv[:, :, 1] < 40) & (hsv[:, :, 2] > 200)).astype(np.uint8) * 255  # near-white only
    arrow = cv2.dilate(cv2.bitwise_and(arrow, near), np.ones((7, 7), np.uint8))
    touching = set(np.unique(labels_dots[arrow > 0]).tolist()) - {0}
    blobs = [
        (float(centers[i][0]), float(centers[i][1]), int(stats[i][cv2.CC_STAT_AREA]))
        for i in range(1, n)
        if stats[i][cv2.CC_STAT_AREA] >= config.min_dot_px and i not in touching
    ]
    if not blobs:
        return ()
    joined = cv2.dilate(dots, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (config.pack_join_px, config.pack_join_px)))
    _, labels = cv2.connectedComponents(joined)
    h, w = crop.shape[:2]
    groups: dict[int, list[tuple[float, float, int]]] = {}
    for x, y, area in blobs:
        groups.setdefault(int(labels[int(y), int(x)]), []).append((x, y, area))
    packs = []
    for members in groups.values():
        weight = sum(a for _, _, a in members)
        cx = sum(x * a for x, _, a in members) / weight
        cy = sum(y * a for _, y, a in members) / weight
        is_elite = any(elite[int(y), int(x)] > 0 for x, y, _ in members)
        packs.append(Pack(dx=cx - w / 2.0, dy=cy - h / 2.0, dots=len(members), elite=is_elite))
    return tuple(sorted(packs, key=lambda p: p.distance))


def line_is_clear(mask: np.ndarray, dx: float, dy: float, start_px: float, stop_short_px: float = 8.0) -> bool:
    """True if the straight minimap line from the centre toward (dx, dy) stays walkable."""
    h, w = mask.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    length = math.hypot(dx, dy) - stop_short_px
    if length <= start_px:
        return True
    ux, uy = dx / math.hypot(dx, dy), dy / math.hypot(dx, dy)
    samples = np.arange(start_px, length, 2.0)
    xs = np.clip((cx + ux * samples).astype(int), 0, w - 1)
    ys = np.clip((cy + uy * samples).astype(int), 0, h - 1)
    return float((mask[ys, xs] > 0).mean()) >= 0.9


def walkable_mask(crop: np.ndarray, config: NavConfig) -> np.ndarray:
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = _hsv_mask(hsv, config.walkable_hsv)
    # Monster dots are saturated colours but sit on walkable ground.
    mask |= _hsv_mask(hsv, config.monster_hsv)
    # The Helltide "brick" hatching draws pale lines across blocked red areas;
    # opening removes them, closing fills small icons/dots on open ground.
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    # The player arrow's dark outline is not terrain; we are standing on open ground.
    h, w = mask.shape[:2]
    cv2.circle(mask, (w // 2, h // 2), config.ray_start_px + 4, 255, -1)
    return mask


def ray_clearance(mask: np.ndarray, rays: int, start_px: int) -> tuple[np.ndarray, np.ndarray]:
    """Fraction of each ray (from the centre to the minimap edge) that is open."""
    h, w = mask.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    angles = np.linspace(0.0, math.tau, rays, endpoint=False)
    clearance = np.zeros(rays, dtype=np.float32)
    for i, angle in enumerate(angles):
        dx, dy = math.cos(angle), math.sin(angle)
        # Distance to the minimap border along this ray.
        limits = []
        if abs(dx) > 1e-6:
            limits.append((w / 2.0 - 1) / abs(dx))
        if abs(dy) > 1e-6:
            limits.append((h / 2.0 - 1) / abs(dy))
        length = min(limits)
        if length <= start_px:
            continue
        open_px = length
        blocked_run = 0
        for r in np.arange(start_px, length, 2.0):
            x = int(cx + dx * r)
            y = int(cy + dy * r)
            if mask[y, x] == 0:
                blocked_run += 1
                if blocked_run >= 3:  # ~6px of solid blocked ground
                    open_px = r - 4.0
                    break
            else:
                blocked_run = 0
        clearance[i] = float(np.clip((open_px - start_px) / (length - start_px), 0.0, 1.0))
    return angles, clearance


class MinimapTracker:
    def __init__(self, config: NavConfig):
        self.config = config
        self._prev: np.ndarray | None = None
        self._window: np.ndarray | None = None
        self._arrow_mask: np.ndarray | None = None
        self.star_threshold = 0.70
        self._position = np.zeros(2, dtype=np.float64)

    def reset(self) -> None:
        self._prev = None
        self._window = None
        self._position[:] = 0.0

    def _gray(self, crop: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        h, w = gray.shape
        # Hide the player arrow: it never moves and would bias the shift to zero.
        # Feather the patch so its own edge does not become a static feature.
        if self._arrow_mask is None or self._arrow_mask.shape != gray.shape:
            disc = np.zeros((h, w), dtype=np.float32)
            r = max(8, int(min(h, w) * 0.07))
            cv2.circle(disc, (w // 2, h // 2), r, 1.0, -1)
            self._arrow_mask = cv2.GaussianBlur(disc, (0, 0), r / 2.0)
        gray = gray * (1.0 - self._arrow_mask) + float(gray.mean()) * self._arrow_mask
        return cv2.GaussianBlur(gray, (5, 5), 0)

    def update(self, frame: np.ndarray, region: Region) -> MinimapReading:
        fh, fw = frame.shape[:2]
        x, y, w, h = region.pixels(fw, fh)
        crop = frame[y : y + h, x : x + w]
        if crop.shape[0] < 40 or crop.shape[1] < 40:
            return INVALID

        mask = walkable_mask(crop, self.config)
        walkable = float(cv2.countNonZero(mask)) / float(mask.size)
        if not 0.04 <= walkable <= 0.97:
            # Minimap hidden, big map open, or a zone with different colours.
            self._prev = None
            return MinimapReading(valid=False, walkable_ratio=walkable, region=(x, y, w, h))

        angles, clearance = ray_clearance(mask, self.config.rays, self.config.ray_start_px)
        stars = find_stars(crop, self.config, self.star_threshold)

        gray = self._gray(crop)
        shift = (0.0, 0.0)
        response = 0.0
        if self._prev is not None and self._prev.shape == gray.shape:
            if self._window is None or self._window.shape != gray.shape:
                self._window = cv2.createHanningWindow((gray.shape[1], gray.shape[0]), cv2.CV_32F)
            (sx, sy), response = cv2.phaseCorrelate(self._prev, gray, self._window)
            if response >= self.config.min_response and math.hypot(sx, sy) <= self.config.max_shift_px:
                # Map content moves opposite to the player.
                shift = (-float(sx), -float(sy))
                self._position += shift
        self._prev = gray

        return MinimapReading(
            valid=True,
            angles=angles,
            clearance=clearance,
            shift=shift,
            response=float(response),
            position=(float(self._position[0]), float(self._position[1])),
            walkable_ratio=walkable,
            region=(x, y, w, h),
            packs=tuple(
                Pack(p.dx, p.dy, p.dots, p.elite, line_is_clear(mask, p.dx, p.dy, self.config.ray_start_px))
                for p in monster_packs(crop, self.config, stars)
            ),
            loot=tuple((sx - w / 2.0, sy - h / 2.0) for sx, sy in stars),
        )
