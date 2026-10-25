"""OpenCV perception: globes, enemy health bars, skill cooldowns, form buff, minimap, death."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from .config import BotConfig, HsvRange, Region
from .detector import Detection, Detector
from .minimap import INVALID as MINIMAP_INVALID
from .minimap import MinimapReading, MinimapTracker
from .routes import RouteFix, RouteLocator, RouteMap, load_routes

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GlobeReading:
    level: float
    mask_ratio: float
    confidence: float


@dataclass(frozen=True)
class SkillReading:
    ready: bool | None
    score: float | None


@dataclass(frozen=True)
class Enemy:
    bbox: tuple[int, int, int, int]
    score: float
    center: tuple[int, int]


@dataclass(frozen=True)
class FormReading:
    """Visual state of a toggle form (e.g. Warlock demon form)."""

    demon: bool | None
    metric: float
    threshold: float
    confidence: float


@dataclass(frozen=True)
class BuffReading:
    """Demon-form indicator (skill-frame glow or buff icon). present=None until confirmed."""

    present: bool | None
    score: float
    raw: bool = False


@dataclass(frozen=True)
class ScreenReading:
    saturation: float = 0.0
    brightness: float = 0.0
    grey: bool = False        # this frame looks like the desaturated death screen
    black: bool = False       # loading screen / black frame
    template_score: float | None = None
    hud_score: float | None = None
    no_hud: bool = False      # HUD missing this frame (death screen, menu, cutscene)


@dataclass
class Observation:
    frame: np.ndarray
    health: GlobeReading
    resource: GlobeReading
    enemies: list[Enemy]
    skills: dict[str, SkillReading]
    dead: bool
    timestamp: float
    width: int
    height: int
    form: FormReading = field(default_factory=lambda: FormReading(None, 0.0, 0.0, 0.0))
    buff: BuffReading = field(default_factory=lambda: BuffReading(None, 0.0))
    minimap: MinimapReading = MINIMAP_INVALID
    screen: ScreenReading = field(default_factory=ScreenReading)
    kill_signal: bool = False  # Helltide cinder counter changed this frame
    loot_labels: list[tuple[int, int, int, int]] = field(default_factory=list)  # orange item labels (x, y, w, h)
    route: RouteFix = field(default_factory=lambda: RouteFix(False))
    route_map: RouteMap | None = None
    detections: list[Detection] = field(default_factory=list)  # YOLO (detector.enabled), else empty

    @property
    def black(self) -> bool:
        return self.screen.black


@dataclass
class _Track:
    bbox: tuple[int, int, int, int]
    hits: int
    misses: int
    score: float


def _center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x, y, w, h = bbox
    return x + w // 2, y + h // 2


def _distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return float(((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5)


def measure_glow(frame: np.ndarray, region: Region, ring_px: int = 7) -> float:
    """Fraction of a skill slot's frame that is burning orange (demon form).

    Only the border ring is measured: the icon art is orange in both forms.
    """
    height, width = frame.shape[:2]
    x, y, w, h = region.pixels(width, height)
    scale = height / 1440.0
    pad = max(2, int(round(4 * scale)))
    ring = max(3, int(round(ring_px * scale)))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(width, x + w + pad), min(height, y + h + pad)
    crop = frame[y0:y1, x0:x1]
    if crop.shape[0] <= 2 * (pad + ring) or crop.shape[1] <= 2 * (pad + ring):
        return 0.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = np.ones(hsv.shape[:2], dtype=bool)
    inset = pad + ring
    mask[inset:-inset, inset:-inset] = False
    hue, sat, val = hsv[:, :, 0][mask], hsv[:, :, 1][mask], hsv[:, :, 2][mask]
    glow = (hue >= 8) & (hue <= 30) & (sat >= 110) & (val >= 200)
    return float(glow.mean())


def measure_form_metric(frame: np.ndarray, region: Region, metric: str = "orange_ratio") -> float:
    """Measure the visual 'form glow' in a HUD region.

    orange_ratio: fraction of saturated orange/red pixels (0..1).
    brightness:   mean HSV value (0..1).
    """
    height, width = frame.shape[:2]
    x, y, w, h = region.pixels(width, height)
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    roi = frame[y : y + h, x : x + w]
    if roi.size == 0:
        return 0.0
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    if metric == "brightness":
        return float(hsv[:, :, 2].mean()) / 255.0
    hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    orange = (hue <= 35) & (sat >= 80) & (val >= 50)
    red = (hue >= 170) & (sat >= 80) & (val >= 50)
    return float(np.count_nonzero(orange | red)) / float(orange.size)


class Vision:
    def __init__(self, config: BotConfig):
        self.config = config
        self._templates: dict[str, np.ndarray | None] = {}
        self._smooth: dict[str, float] = {}
        self._tracks: list[_Track] = []
        self._low_health_since: float | None = None
        self._warned_templates: set[str] = set()
        self._form_state: bool | None = None
        self._form_candidate: bool | None = None
        self._form_streak = 0
        for skill in config.skills:
            self._templates[skill.name] = self._load_template(skill.template)
        self._buff_template = self._load_template(config.form.buff_template)
        self._buff_scaled: tuple[int, np.ndarray] | None = None
        self._buff_state: bool | None = None
        self._buff_candidate: bool | None = None
        self._buff_streak = 0
        self._buff_absent_since: float | None = None
        self._death_template = self._load_template(config.death.template) if config.death.enabled else None
        self._hud_template = self._load_template(config.death.hud_template) if config.death.enabled else None
        self._hud_scaled: tuple[int, np.ndarray] | None = None
        self._kill_prev: np.ndarray | None = None
        self._grey_since: float | None = None
        self._minimap = (
            MinimapTracker(config.nav)
            if config.nav.enabled and "minimap" in config.regions
            else None
        )
        if self._minimap is not None:
            self._minimap.star_threshold = config.combat.star_threshold
        self._routes: RouteLocator | None = None
        if config.routes.enabled and self._minimap is not None:
            maps = load_routes(config.project_root / config.routes.folder)
            if maps:
                self._routes = RouteLocator(maps, config.nav)
                log.info("Routes loaded: %s", ", ".join(m.name for m in maps))
            else:
                log.info("Routes enabled but none recorded in %s", config.routes.folder)
        self.detector = Detector.create(config.detector, config.project_root)

    def _load_template(self, relative: str | None) -> np.ndarray | None:
        if not relative:
            return None
        path = (self.config.project_root / relative).resolve()
        if not path.is_file():
            log.info("Template missing (fallback cooldown will be used): %s", path)
            return None
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None:
            log.warning("Template could not be decoded: %s", path)
            return None
        return image

    def reset(self) -> None:
        self._smooth.clear()
        self._tracks.clear()
        self._low_health_since = None
        self._form_state = None
        self._form_candidate = None
        self._form_streak = 0
        self._buff_state = None
        self._buff_candidate = None
        self._buff_streak = 0
        self._buff_absent_since: float | None = None
        self._grey_since = None
        if self._minimap is not None:
            self._minimap.reset()

    def observe(self, frame: np.ndarray) -> Observation:
        if frame is None or frame.size == 0:
            raise ValueError("empty frame")
        height, width = frame.shape[:2]
        now = time.monotonic()

        health = self._globe("health", frame, self.config.regions["health_globe"], self.config.globe.health_hsv, self.config.globe.health_full_ratio)
        resource = self._globe("resource", frame, self.config.regions["resource_globe"], self.config.globe.resource_hsv, self.config.globe.resource_full_ratio)
        enemies = self._detect_enemies(frame, self.config.regions["enemy_roi"])
        skills = {
            skill.name: self._skill_ready(frame, self.config.regions["skill_bar"], skill)
            for skill in self.config.skills
        }
        form = self._detect_form(frame)
        buff = self._detect_buff(frame, now)
        screen = self._screen_state(frame)
        kill_signal = self._kill_signal(frame)
        loot_labels = self._loot_labels(frame) if self.config.combat.mode == "hunt" else []
        dead = self._update_death(health, screen, now)
        minimap = MINIMAP_INVALID
        if self._minimap is not None and not screen.black:
            minimap = self._minimap.update(frame, self.config.regions["minimap"])
        route = RouteFix(False)
        if self._routes is not None and minimap.valid:
            route = self._routes.update(self._crop(frame, self.config.regions["minimap"]), minimap.shift)
        detections: list[Detection] = []
        if self.detector is not None and not screen.black and not screen.no_hud:
            detections = self.detector.detect(frame)
        return Observation(
            frame=frame,
            health=health,
            resource=resource,
            enemies=enemies,
            skills=skills,
            dead=dead,
            timestamp=now,
            width=width,
            height=height,
            form=form,
            buff=buff,
            minimap=minimap,
            screen=screen,
            kill_signal=kill_signal,
            loot_labels=loot_labels,
            route=route,
            route_map=self._routes.active if self._routes is not None else None,
            detections=detections,
        )

    def _loot_labels(self, frame: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Orange/gold item-name labels: text strokes on a dark label box.

        Fire is the same orange, but sits on bright surroundings; label text
        sits on the dark translucent box D4 draws behind item names.
        """
        combat = self.config.combat
        if not combat.loot_enabled:
            return []
        # Stops before the quest tracker column on the right (white text on a
        # dark box, at x ~0.79) and above the HUD.
        region = Region(0.10, 0.10, 0.68, 0.72)
        roi = self._crop(frame, region)
        fh, fw = frame.shape[:2]
        x0, y0, _, _ = region.pixels(fw, fh)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        text = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in combat.label_hsv:
            text |= cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
        scale = fh / 1440.0
        joined = cv2.morphologyEx(text, cv2.MORPH_CLOSE, np.ones((max(3, int(7 * scale)), max(9, int(17 * scale))), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(joined)
        labels = []
        for i in range(1, n):
            x, y, w, h, _ = stats[i]
            if not (80 * scale <= w <= 600 * scale and 14 * scale <= h <= 40 * scale and w >= 3.5 * h):
                continue
            box_text = text[y : y + h, x : x + w] > 0
            fill = float(box_text.mean())
            if not 0.12 <= fill <= 0.6:
                continue
            background = hsv[y : y + h, x : x + w, 2][~box_text]
            if background.size == 0 or float(np.median(background)) > 120:
                continue  # bright surroundings: fire, not a label
            cx, cy = x0 + x + w / 2.0, y0 + y + h / 2.0
            if abs(cy - 0.555 * fh) <= 8 * scale and h <= 21 * scale and abs(cx - fw / 2.0) <= 330 * scale:
                # Zone-name banner ("ELDERWOOD GROVE") shown when entering a
                # sub-zone: boxed white words, centred at y 799, 19px tall
                # (live path1: clicked as loot 8x). Item labels were 20-35px.
                continue
            labels.append((x0 + int(x), y0 + int(y), int(w), int(h)))
        return labels

    def _kill_signal(self, frame: np.ndarray) -> bool:
        """Kills drop cinders that are picked up at once, so the counter changing = kills."""
        if self.config.combat.mode != "pulse":
            return False
        crop = self._crop(frame, self.config.combat.kill_region)
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
        previous, self._kill_prev = self._kill_prev, gray
        if previous is None or previous.shape != gray.shape:
            return False
        return float(np.mean(np.abs(gray - previous))) >= self.config.combat.kill_diff

    def _crop(self, frame: np.ndarray, region: Region) -> np.ndarray:
        height, width = frame.shape[:2]
        x, y, w, h = region.pixels(width, height)
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = max(1, min(w, width - x))
        h = max(1, min(h, height - y))
        return frame[y : y + h, x : x + w]

    def _globe(
        self,
        name: str,
        frame: np.ndarray,
        region: Region,
        ranges: list[HsvRange],
        full_ratio: float,
    ) -> GlobeReading:
        roi = self._crop(frame, region)
        if roi.size == 0:
            return GlobeReading(level=0.0, mask_ratio=0.0, confidence=0.0)

        h, w = roi.shape[:2]
        mx = int(w * 0.08)
        my = int(h * 0.08)
        core = roi[my : h - my, mx : w - mx] if w > 2 * mx and h > 2 * my else roi
        hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for low, high in ranges:
            mask = cv2.bitwise_or(
                mask,
                cv2.inRange(
                    hsv,
                    np.array(low, dtype=np.uint8),
                    np.array(high, dtype=np.uint8),
                ),
            )
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        ratio = float(cv2.countNonZero(mask)) / float(mask.size)

        span = max(0.001, full_ratio - self.config.globe.empty_ratio)
        level = float(np.clip((ratio - self.config.globe.empty_ratio) / span, 0.0, 1.0))
        alpha = self.config.globe.smoothing
        previous = self._smooth.get(name)
        smoothed = level if previous is None else previous * (1.0 - alpha) + level * alpha
        self._smooth[name] = smoothed
        confidence = float(np.clip(ratio / 0.05, 0.0, 1.0))
        return GlobeReading(level=float(np.clip(smoothed, 0.0, 1.0)), mask_ratio=ratio, confidence=confidence)

    def _detect_enemies(self, frame: np.ndarray, region: Region) -> list[Enemy]:
        roi = self._crop(frame, region)
        if roi.size == 0:
            return []
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # Warm (red/orange, wrapping hue 0/180) ranges share one mask; other
        # colours (teal bars) get their own, so a teal bar is not merged with
        # the orange glow of the burning monster under it.
        groups: dict[str, np.ndarray] = {}
        for low, high in self.config.enemy.hsv:
            key = "warm" if low[0] <= 30 or high[0] >= 165 else f"{low[0]}-{high[0]}"
            part = cv2.inRange(hsv, np.array(low, dtype=np.uint8), np.array(high, dtype=np.uint8))
            groups[key] = part if key not in groups else cv2.bitwise_or(groups[key], part)
        frame_h, frame_w = frame.shape[:2]
        x0, y0, _, _ = region.pixels(frame_w, frame_h)
        contours = []
        for mask in groups.values():
            for excluded in self.config.enemy.exclude:
                ex, ey, ew, eh = excluded.pixels(frame_w, frame_h)
                mask[max(0, ey - y0) : max(0, ey - y0 + eh), max(0, ex - x0) : max(0, ex - x0 + ew)] = 0
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 5), np.uint8))
            found, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            contours.extend(found)

        detections: list[tuple[tuple[int, int, int, int], float]] = []
        cfg = self.config.enemy
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if not (cfg.min_width <= w <= cfg.max_width):
                continue
            if not (cfg.min_height <= h <= cfg.max_height):
                continue
            aspect = w / max(1, h)
            if not (cfg.min_aspect <= aspect <= cfg.max_aspect):
                continue
            fill = float(cv2.contourArea(contour)) / float(max(1, w * h))
            if fill < cfg.min_fill:
                continue
            score = float(np.clip(0.55 * fill + 0.45 * min(1.0, w / 80.0), 0.0, 1.0))
            detections.append(((x, y, w, h), score))

        self._update_tracks(detections)
        enemies: list[Enemy] = []
        for track in self._tracks:
            if track.hits < cfg.persistence_frames or track.misses > 1:
                continue
            x, y, w, h = track.bbox
            bbox = (x + x0, y + y0, w, h)
            enemies.append(Enemy(bbox=bbox, score=track.score, center=_center(bbox)))
        return enemies

    def _update_tracks(self, detections: list[tuple[tuple[int, int, int, int], float]]) -> None:
        cfg = self.config.enemy
        used: set[int] = set()
        for bbox, score in detections:
            center = _center(bbox)
            best_index = -1
            best_distance = 1e9
            for index, track in enumerate(self._tracks):
                if index in used:
                    continue
                distance = _distance(center, _center(track.bbox))
                radius = max(cfg.track_radius_px, bbox[2] * 0.8)
                if distance <= radius and distance < best_distance:
                    best_index = index
                    best_distance = distance
            if best_index >= 0:
                track = self._tracks[best_index]
                track.bbox = bbox
                track.score = score
                track.hits += 1
                track.misses = 0
                used.add(best_index)
            else:
                self._tracks.append(_Track(bbox=bbox, hits=1, misses=0, score=score))
                used.add(len(self._tracks) - 1)

        for index, track in enumerate(self._tracks):
            if index not in used:
                track.misses += 1
        self._tracks = [track for track in self._tracks if track.misses <= 3]

    def _skill_ready(self, frame: np.ndarray, region: Region, skill) -> SkillReading:  # type: ignore[no-untyped-def]
        template = self._templates.get(skill.name)
        if template is None:
            return SkillReading(ready=None, score=None)
        roi = self._crop(frame, region)
        th, tw = template.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return SkillReading(ready=None, score=None)
        result = cv2.matchTemplate(roi, template, cv2.TM_SQDIFF_NORMED)
        score = float(result.min())
        return SkillReading(ready=score <= skill.threshold, score=score)

    def _form_region(self) -> Region:
        if self.config.form.roi is not None:
            return self.config.form.roi
        bar = self.config.regions["skill_bar"]
        # The rightmost sixth of the six-slot skill bar. The last skill icon is
        # the stable "glow" reference for the Warlock demon form.
        return Region(bar.x + bar.w * 5.0 / 6.0, bar.y, bar.w / 6.0, bar.h)

    def form_metric(self, frame: np.ndarray) -> float:
        return measure_form_metric(frame, self._form_region(), self.config.form.metric)

    def _detect_form(self, frame: np.ndarray) -> FormReading:
        if not self.config.form.enabled:
            return FormReading(None, 0.0, 0.0, 0.0)
        raw = self.form_metric(frame)
        threshold = self.config.form.demon_threshold
        hysteresis = self.config.form.hysteresis
        high = raw >= threshold + hysteresis
        low = raw <= threshold - hysteresis
        candidate: bool | None
        if self.config.form.higher_is_demon:
            candidate = True if high else False if low else None
        else:
            candidate = True if low else False if high else None

        if candidate is None:
            self._form_candidate = None
            self._form_streak = 0
        else:
            if candidate == self._form_candidate:
                self._form_streak += 1
            else:
                self._form_candidate = candidate
                self._form_streak = 1
            if self._form_streak >= self.config.form.confirm_frames:
                self._form_state = candidate

        confidence = 0.0
        if candidate is not None:
            confidence = min(1.0, self._form_streak / self.config.form.confirm_frames)
        return FormReading(
            demon=self._form_state,
            metric=raw,
            threshold=threshold,
            confidence=confidence,
        )

    def buff_score(self, frame: np.ndarray) -> float | None:
        """Best match of the demon-form buff icon inside the buff bar, or None."""
        if self._buff_template is None or "buff_bar" not in self.config.regions:
            return None
        height = frame.shape[0]
        if self._buff_scaled is None or self._buff_scaled[0] != height:
            template = self._buff_template
            scale = height / float(max(1, self.config.form.buff_template_height))
            if abs(scale - 1.0) > 0.01:
                size = (
                    max(4, int(round(template.shape[1] * scale))),
                    max(4, int(round(template.shape[0] * scale))),
                )
                template = cv2.resize(template, size, interpolation=cv2.INTER_AREA)
            self._buff_scaled = (height, template)
        template = self._buff_scaled[1]
        roi = self._crop(frame, self.config.regions["buff_bar"])
        th, tw = template.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return None
        result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(result.max()))

    def glow_scores(self, frame: np.ndarray) -> list[float]:
        form = self.config.form
        return [measure_glow(frame, slot, form.glow_ring_px) for slot in form.glow_slots]

    def form_score(self, frame: np.ndarray) -> float | None:
        """Score of the configured demon indicator (higher = demon), or None."""
        if self.config.form.mode == "glow":
            scores = sorted(self.glow_scores(frame), reverse=True)
            if not scores:
                return None
            # The k-th best slot: demon needs glow_min_slots burning frames.
            k = min(len(scores), self.config.form.glow_min_slots) - 1
            return scores[k]
        return self.buff_score(frame)

    def _form_threshold(self) -> float:
        form = self.config.form
        return form.glow_threshold if form.mode == "glow" else form.buff_threshold

    def _detect_buff(self, frame: np.ndarray, now: float) -> BuffReading:
        score = self.form_score(frame)
        if score is None:
            return BuffReading(None, 0.0)
        raw = score >= self._form_threshold()
        if raw == self._buff_candidate:
            self._buff_streak += 1
        else:
            self._buff_candidate = raw
            self._buff_streak = 1
        if raw:
            self._buff_absent_since = None
            if self._buff_streak >= self.config.form.confirm_frames:
                self._buff_state = True
        else:
            # D4 blinks a buff icon that is about to expire. Only call it gone
            # after it has stayed gone for a while (or at startup, quickly).
            if self._buff_absent_since is None:
                self._buff_absent_since = now
            needed = self.config.form.buff_off_confirm_s if self._buff_state else 0.0
            if self._buff_streak >= self.config.form.confirm_frames and now - self._buff_absent_since >= needed:
                self._buff_state = False
        return BuffReading(present=self._buff_state, score=score, raw=raw)

    def _screen_state(self, frame: np.ndarray) -> ScreenReading:
        if not self.config.death.enabled:
            return ScreenReading()
        play = self._crop(frame, Region(0.10, 0.12, 0.70, 0.62))
        small = cv2.resize(play, (192, 108), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        saturation = float(hsv[:, :, 1].mean())
        brightness = float(hsv[:, :, 2].mean())
        black = brightness <= self.config.death.black_max_value
        grey = not black and saturation <= self.config.death.max_saturation
        # D4's real death screen is darkened but still coloured; what reliably
        # changes is that the whole HUD disappears. The skill key labels are
        # fixed HUD art, so "label not found" = no HUD (death, menu, cutscene).
        hud_score = self.hud_score(frame)
        no_hud = not black and hud_score is not None and hud_score < self.config.death.hud_threshold
        template_score = None
        if self._death_template is not None:
            roi = self._crop(frame, Region(0.20, 0.20, 0.60, 0.70))
            th, tw = self._death_template.shape[:2]
            if roi.shape[0] >= th and roi.shape[1] >= tw:
                result = cv2.matchTemplate(roi, self._death_template, cv2.TM_CCOEFF_NORMED)
                template_score = float(np.nan_to_num(result.max()))
                if template_score >= self.config.death.template_threshold:
                    grey = True
        return ScreenReading(
            saturation=saturation,
            brightness=brightness,
            grey=grey,
            black=black,
            template_score=template_score,
            hud_score=hud_score,
            no_hud=no_hud,
        )

    def hud_score(self, frame: np.ndarray) -> float | None:
        if self._hud_template is None:
            return None
        height = frame.shape[0]
        if self._hud_scaled is None or self._hud_scaled[0] != height:
            template = self._hud_template
            scale = height / 1440.0
            if abs(scale - 1.0) > 0.01:
                size = (max(4, int(round(template.shape[1] * scale))), max(4, int(round(template.shape[0] * scale))))
                template = cv2.resize(template, size, interpolation=cv2.INTER_AREA)
            self._hud_scaled = (height, template)
        template = self._hud_scaled[1]
        roi = self._crop(frame, self.config.death.hud_region)
        th, tw = template.shape[:2]
        if roi.shape[0] < th or roi.shape[1] < tw:
            return None
        result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(result.max()))

    def _update_death(self, health: GlobeReading, screen: ScreenReading, now: float) -> bool:
        screen_dead = False
        if screen.grey or screen.no_hud:
            if self._grey_since is None:
                self._grey_since = now
            screen_dead = now - self._grey_since >= self.config.death.confirm_s
        else:
            self._grey_since = None

        low = health.level <= 0.02 and health.confidence >= 0.20
        if not low:
            self._low_health_since = None
            return screen_dead
        if self._low_health_since is None:
            self._low_health_since = now
        return screen_dead or (now - self._low_health_since) >= self.config.globe.death_confirm_s

    def template_status(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for skill in self.config.skills:
            path = skill.template or ""
            out[skill.name] = "loaded" if self._templates.get(skill.name) is not None else f"missing ({path})"
        return out


def fit_for_display(frame: np.ndarray, max_width: int = 1600, max_height: int = 900) -> tuple[np.ndarray, float]:
    height, width = frame.shape[:2]
    scale = min(max_width / max(1, width), max_height / max(1, height), 1.0)
    if scale >= 1.0:
        return frame, 1.0
    resized = cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    return resized, scale
