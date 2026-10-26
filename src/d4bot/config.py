"""Configuration loading and validation."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]

DEFAULT_REGIONS: dict[str, list[float]] = {
    "health_globe": [0.030, 0.715, 0.115, 0.235],
    "resource_globe": [0.855, 0.715, 0.115, 0.235],
    "skill_bar": [0.365, 0.835, 0.270, 0.135],
    "enemy_roi": [0.180, 0.100, 0.640, 0.700],
    "center": [0.400, 0.360, 0.200, 0.240],
    # Measured on the 2560x1440 client (HUD Medium).
    "minimap": [0.8348, 0.0417, 0.1543, 0.1993],
    "buff_bar": [0.3900, 0.8000, 0.3100, 0.0650],
}

DEFAULT_HEALTH_HSV: list[HsvRange] = [
    ((0, 110, 70), (10, 255, 255)),
    ((170, 110, 70), (180, 255, 255)),
]

DEFAULT_RESOURCE_HSV: list[HsvRange] = [
    ((90, 70, 70), (135, 255, 255)),
    ((20, 80, 80), (38, 255, 255)),
    ((135, 55, 55), (170, 255, 255)),
    ((0, 100, 60), (12, 255, 255)),
    ((170, 100, 60), (180, 255, 255)),
]

DEFAULT_WALKABLE_HSV: list[HsvRange] = [
    ((0, 0, 140), (30, 82, 255)),
    ((165, 0, 140), (180, 82, 255)),
]

# Subo (mercenary) marks monsters on the minimap: yellow/orange dots, red
# dots + a red skull for elites. Measured on a 2560x1440 Helltide minimap.
DEFAULT_MONSTER_DOT_HSV: list[HsvRange] = [
    ((14, 150, 170), (32, 255, 255)),
]
DEFAULT_ELITE_DOT_HSV: list[HsvRange] = [
    ((0, 120, 185), (8, 255, 255)),
    ((170, 120, 185), (180, 255, 255)),
]

DEFAULT_ENEMY_HSV: list[HsvRange] = [
    ((0, 110, 70), (10, 255, 255)),
    ((170, 110, 70), (180, 255, 255)),
    ((10, 120, 90), (25, 255, 255)),
]


def _as_hsv_ranges(value: Any, default: list[HsvRange]) -> list[HsvRange]:
    if value is None:
        return list(default)
    if not isinstance(value, list) or not value:
        raise ValueError("HSV ranges must be a non-empty list")
    out: list[HsvRange] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError(f"each HSV range must be [low, high], got {item!r}")
        low, high = item
        if len(low) != 3 or len(high) != 3:
            raise ValueError(f"HSV values must have 3 components, got {item!r}")
        lo = tuple(int(v) for v in low)
        hi = tuple(int(v) for v in high)
        for v in (*lo, *hi):
            if not 0 <= v <= 255:
                raise ValueError(f"HSV component out of 0..255: {v}")
        if any(a > b for a, b in zip(lo, hi)):
            raise ValueError(f"HSV low must be <= high: {item!r}")
        out.append((lo, hi))  # type: ignore[arg-type]
    return out


@dataclass(frozen=True)
class Region:
    """Normalized region inside the captured frame (0..1)."""

    x: float
    y: float
    w: float
    h: float

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.w, self.h)
        if not all(math.isfinite(v) for v in values):
            raise ValueError(f"region values must be finite: {values!r}")
        if self.w <= 0 or self.h <= 0:
            raise ValueError(f"region width/height must be positive: {values!r}")
        if not (0.0 <= self.x < 1.0 and 0.0 <= self.y < 1.0):
            raise ValueError(f"region origin must be in [0, 1): {values!r}")
        if self.x + self.w > 1.001 or self.y + self.h > 1.001:
            raise ValueError(f"region extends outside frame: {values!r}")

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        left = int(round(self.x * width))
        top = int(round(self.y * height))
        right = int(round((self.x + self.w) * width))
        bottom = int(round((self.y + self.h) * height))
        return left, top, max(1, right - left), max(1, bottom - top)

    def center_pixels(self, width: int, height: int) -> tuple[int, int]:
        x, y, w, h = self.pixels(width, height)
        return x + w // 2, y + h // 2

    def as_list(self) -> list[float]:
        return [round(self.x, 4), round(self.y, 4), round(self.w, 4), round(self.h, 4)]


@dataclass(frozen=True)
class CaptureConfig:
    window_title: str = "Diablo IV"
    mode: str = "window"
    monitor: int = 1
    fps: float = 20.0


@dataclass(frozen=True)
class InputConfig:
    require_foreground: bool = True
    emergency_stop_key: str = "f8"
    potion_enabled: bool = True
    potion_mode: str = "health"
    potion_key: str = "q"
    mount_key: str = ""             # e.g. "z": pressed once on landing in a waypoint town; off (user: stay on foot)
    potion_threshold: float = 0.55
    potion_interval_s: float = 1.2
    potion_timer_s: float = 8.0
    action_gap_s: float = 0.035
    aim_jitter_px: int = 4


@dataclass(frozen=True)
class RoamConfig:
    leg_interval_s: float = 4.0
    turn_jitter_deg: float = 35.0
    click_distance_px: int = 650
    min_click_interval_s: float = 1.0
    play_area_margin: float = 0.20
    click_y_min: float = 0.30
    click_y_max: float = 0.60
    stuck_diff_threshold: float = 1.5
    stuck_confirm_s: float = 2.0


@dataclass(frozen=True)
class CombatConfig:
    """How roam mode fights when it cannot see enemy health bars.

    hunt: walk toward the nearest pack of monster dots on the minimap (Subo
    marks them); stop and attack toward a pack once it is within
    fight_radius_px of the player on the minimap.
    pulse: walk walk_s, stop and spam the attack ahead for fight_s, and keep
    fighting while kills happen (the Helltide cinder counter changes), up to
    fight_max_s. Kill signal is noisy (see HANDOFF); prefer hunt.
    bars: fight only when an enemy health bar is detected.
    """

    mode: str = "bars"
    fight_radius_px: float = 28.0     # minimap px from the player
    elite_fight_radius_px: float = 40.0
    linger_s: float = 1.0             # keep attacking after the dots leave the radius
    # Engage at fight_radius_px, but walk in until attack_range_px before
    # attacking. Set attack_range_px to the character's reach (minimap px;
    # ~7.5 screen px each at 1440p). >= fight_radius_px = attack on engage.
    attack_range_px: float = 14.0
    approach_timeout_s: float = 2.5   # cannot get closer (wall): attack from here
    approach_click_s: float = 0.4
    max_fight_s: float = 10.0         # a "pack" that survives this long is a static map icon
    ignore_s: float = 60.0            # ... so ignore that spot for this long
    hunt_weight: float = 1.2          # how strongly roam steers toward the nearest pack
    screen_per_minimap_px: float = 9.0  # aim distance scale (1440p)
    # Loot: a yellow STAR on the minimap is a legendary/unique drop (user, run 8).
    loot_enabled: bool = True
    loot_range_px: float = 140.0      # go for stars within this minimap distance
    loot_near_px: float = 35.0        # within this, look for its label on screen
    loot_weight: float = 2.5          # steering pull toward a star (packs use hunt_weight)
    loot_timeout_s: float = 8.0       # give up on a star not picked up in this long
    loot_ignore_s: float = 120.0
    loot_click_interval_s: float = 0.6
    star_threshold: float = 0.70      # template score: stars 0.87-0.91, best non-star 0.45
    # Item-name text colours. The user's loot filter decides what drops show
    # a label, so every label on screen is worth picking up.
    label_hsv: list[HsvRange] = field(default_factory=lambda: [
        ((15, 140, 185), (27, 255, 255)),   # legendary orange / unique gold
        ((0, 0, 185), (180, 60, 255)),      # white (common, materials)
        ((100, 90, 170), (125, 255, 255)),  # blue (magic)
        ((26, 90, 185), (35, 255, 255)),    # yellow (rare)
        ((0, 150, 170), (5, 255, 255)),     # red (live: "Amulet" missed; 0 false labels in 132 fight frames)
        ((174, 150, 170), (180, 255, 255)),
    ])
    # Colour the loot filter gives wanted gear; never seen in the game world,
    # so labels in it are trusted anywhere (bright green, like "Set Charm").
    label_unique_hsv: list[HsvRange] = field(default_factory=lambda: [((45, 150, 200), (66, 255, 255))])
    loot_label_radius_px: int = 650   # click labels within this screen distance (1440p); real label was 524px away
    aim_min_px: int = 120
    aim_max_px: int = 420
    walk_s: float = 2.5
    fight_s: float = 1.2
    fight_max_s: float = 8.0
    kill_extend_s: float = 1.5
    aim_distance_px: int = 220        # at 1440p, ahead of the player along the heading
    sweep_deg: float = 35.0           # aim swings this much left/right while fighting
    kill_region: Region = Region(0.7420, 0.1270, 0.0250, 0.0240)  # Helltide cinder counter
    kill_diff: float = 10.0           # mean pixel change in kill_region that counts as a kill


@dataclass(frozen=True)
class NavConfig:
    """Minimap-guided roaming: steer toward open ground, detect stuck, avoid revisits."""

    enabled: bool = True
    # Walkable minimap pixels (Helltide parchment). Everything else is blocked.
    walkable_hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_WALKABLE_HSV))
    monster_hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_MONSTER_DOT_HSV))
    elite_hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_ELITE_DOT_HSV))
    pack_join_px: int = 14            # dots closer than this are one pack
    min_dot_px: int = 4               # smaller blobs are noise
    max_elite_dot_px: int = 60        # bigger red shapes are map icons, not elites
    max_monster_dot_px: int = 44      # bigger yellow shapes are map icons, not monsters
    rays: int = 24
    ray_start_px: int = 14            # skip the player arrow
    min_clearance: float = 0.30       # fraction of the ray length that must be open
    click_interval_s: float = 0.8
    click_distance_px: int = 520      # at 1440p; scaled by frame height
    player_x: float = 0.50            # player position on screen (fraction)
    player_y: float = 0.48
    y_scale: float = 0.75             # isometric foreshortening of screen-vertical moves
    click_y_max: float = 0.78
    rotation_deg: float = 0.0         # minimap -> screen rotation; learned if learn_rotation
    learn_rotation: bool = True
    continuity_weight: float = 0.35
    revisit_weight: float = 0.45
    lookahead_px: float = 40.0
    visit_radius_px: float = 25.0
    memory_s: float = 90.0
    stuck_window_s: float = 1.6
    stuck_px: float = 3.0
    blocked_memory_s: float = 6.0
    blocked_weight: float = 0.8
    noise: float = 0.08
    min_response: float = 0.08        # phase-correlation confidence for a valid shift
    max_shift_px: float = 25.0        # bigger jumps = teleport / map change; reset
    # Path planning: walk around walls to packs/stars instead of straight at them.
    path_planning: bool = True
    path_cell_px: int = 4             # planning grid cell (minimap px)


@dataclass(frozen=True)
class DetectorConfig:
    """Optional YOLO detector (src/d4bot/detector.py, docs/YOLO.md). Off by default."""

    enabled: bool = False
    model: str = "models/d4.onnx"     # exported by tools/yolo_train.py
    classes: tuple[str, ...] = ("monster", "elite", "loot", "chest", "spawner")
    input_size: int = 640
    confidence: float = 0.40
    iou: float = 0.50
    every_n_frames: int = 2           # detector at ~half the loop rate; boxes reused in between
    backend: str = "auto"             # auto = onnxruntime (GPU) if installed, else OpenCV (CPU)
    roi: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 0.82)  # keep detections above the HUD
    # Aim at the nearest detected monster/elite while fighting a minimap pack
    # (instead of the direction estimated from the minimap dot).
    aim: bool = False
    aim_radius_px: int = 600          # at 1440p, from the player


@dataclass(frozen=True)
class RoutesConfig:
    """Recorded routes (run.cmd -Record NAME): follow a walked path, then roam on a leash."""

    enabled: bool = False
    folder: str = "routes"
    weight: float = 2.0               # steering pull toward the route (packs use hunt_weight)
    lookahead: int = 5                # path points ahead (~6 minimap px each)
    leash_px: float = 0.0             # > 0: at the route's end, roam within this of the end point; 0 = roam freely


@dataclass(frozen=True)
class DeathConfig:
    """Death / loading-screen detection.

    D4's death screen dims the world and removes the whole HUD ("YOU HAVE
    DIED" banner + "Revive at Checkpoint" button). The HUD marker (skill key
    label) disappearing is the main signal; grey-screen is a backup.
    """

    enabled: bool = True
    action: str = "stop"              # stop = end the run; pause = wait until colour returns
    max_saturation: float = 14.0      # play-area mean HSV saturation on the grey death screen
    black_max_value: float = 12.0     # mean brightness below this = loading/black screen
    confirm_s: float = 2.0
    hud_template: str | None = "templates/hud_key1.png"
    hud_region: Region = Region(0.3900, 0.9500, 0.1600, 0.0500)
    hud_threshold: float = 0.70       # HUD present 0.97-1.00, missing 0.34-0.41
    template: str | None = None       # optional crop of the revive UI
    template_threshold: float = 0.75
    save_screenshot: bool = True


@dataclass(frozen=True)
class GlobeConfig:
    health_hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_HEALTH_HSV))
    resource_hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_RESOURCE_HSV))
    health_full_ratio: float = 0.42
    resource_full_ratio: float = 0.42
    empty_ratio: float = 0.015
    smoothing: float = 0.35
    death_confirm_s: float = 3.0


@dataclass(frozen=True)
class FormConfig:
    """Detect a visual toggle form (e.g. Warlock demon form) from a HUD region."""

    enabled: bool = False
    mode: str = "visual"  # visual = color threshold; toggle_state = slot-2 cooldown machine;
                          # glow = burning frames around the skill icons (recommended);
                          # buff = horned-skull buff icon (NOT reliable: it is a stack buff)
    toggle_key: str = "2"
    toggle_skill: str = "skill_2"
    roi: Region | None = None  # None/auto = rightmost 1/6 of the skill bar
    metric: str = "orange_ratio"
    demon_threshold: float = 0.66
    ready_threshold: float = 0.25
    assume_human_at_start: bool = True
    higher_is_demon: bool = True
    hysteresis: float = 0.04
    confirm_frames: int = 3
    post_toggle_grace_s: float = 2.0
    retry_interval_s: float = 1.0
    fallback_cooldown_s: float = 11.0
    toggle_requires_ready: bool = True
    buff_template: str | None = None
    buff_threshold: float = 0.70
    buff_template_height: int = 1440  # client height the template was captured at
    toggle_confirm_s: float = 3.0     # buff must appear this soon after pressing the toggle
    max_failed_toggles: int = 2
    act_while_human: bool = False     # keep hunting in human form; retry the toggle every retry_interval_s
    buff_off_confirm_s: float = 1.0   # icon blinks before expiring; must be gone this long
    # glow mode: outer boxes of skill slots whose frames burn in demon form.
    glow_slots: tuple[Region, ...] = ()
    glow_ring_px: int = 7             # frame thickness at 1440p
    glow_threshold: float = 0.18      # burning 0.31-0.38, plain 0.00-0.07
    glow_min_slots: int = 1           # cooldown icons do not burn; one ready burning slot = demon


@dataclass(frozen=True)
class EnemyConfig:
    hsv: list[HsvRange] = field(default_factory=lambda: list(DEFAULT_ENEMY_HSV))
    min_width: int = 12
    max_width: int = 420
    min_height: int = 2
    max_height: int = 32
    min_aspect: float = 2.0
    max_aspect: float = 18.0
    min_fill: float = 0.45
    persistence_frames: int = 2
    track_radius_px: int = 28
    aim_offset_y: float = 0.0     # aim this far (fraction of frame height) below the bar
    # Frame regions where bar-coloured UI lives (Helltide timer/threat meter).
    exclude: tuple[Region, ...] = ()


@dataclass(frozen=True)
class SkillConfig:
    name: str
    key: str = "1"
    enabled: bool = True
    priority: int = 10
    requires_enemy: bool = True
    min_interval_s: float = 0.3
    fallback_cooldown_s: float = 2.0
    template: str | None = None
    threshold: float = 0.16
    utility: bool = False
    utility_interval_s: float = 15.0


@dataclass(frozen=True)
class BotConfig:
    capture: CaptureConfig
    input: InputConfig
    roam: RoamConfig
    regions: dict[str, Region]
    globe: GlobeConfig
    enemy: EnemyConfig
    skills: tuple[SkillConfig, ...]
    mode: str
    project_root: Path
    config_path: Path
    form: FormConfig = field(default_factory=FormConfig)
    nav: NavConfig = field(default_factory=lambda: NavConfig(enabled=False))
    combat: CombatConfig = field(default_factory=CombatConfig)
    routes: RoutesConfig = field(default_factory=RoutesConfig)
    death: DeathConfig = field(default_factory=lambda: DeathConfig(enabled=False))
    detector: DetectorConfig = field(default_factory=DetectorConfig)


def _section(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key) or {}
    if not isinstance(value, dict):
        raise ValueError(f"config section '{key}' must be a mapping")
    return value


def _region_from_value(value: Any, name: str) -> Region:
    if isinstance(value, dict):
        return Region(
            float(value["x"]), float(value["y"]), float(value["w"]), float(value["h"])
        )
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"region '{name}' must be [x, y, w, h]")
    return Region(*(float(v) for v in value))


def _default_skills() -> list[dict[str, Any]]:
    return [
        {
            "name": "skill_1",
            "key": "1",
            "priority": 40,
            "fallback_cooldown_s": 0.8,
        },
        {
            "name": "skill_2",
            "key": "2",
            "priority": 30,
            "fallback_cooldown_s": 5.0,
        },
        {
            "name": "skill_3",
            "key": "3",
            "priority": 20,
            "fallback_cooldown_s": 8.0,
        },
        {
            "name": "skill_4",
            "key": "4",
            "priority": 10,
            "fallback_cooldown_s": 12.0,
        },
    ]


def load_config(config_path: str | Path) -> BotConfig:
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")

    cap_raw = _section(raw, "capture")
    capture = CaptureConfig(
        window_title=str(cap_raw.get("window_title", "Diablo IV")),
        mode=str(cap_raw.get("mode", "window")).lower(),
        monitor=int(cap_raw.get("monitor", 1)),
        fps=float(cap_raw.get("fps", 20)),
    )
    if capture.mode not in {"window", "monitor"}:
        raise ValueError("capture.mode must be 'window' or 'monitor'")
    if not 1 <= capture.fps <= 120:
        raise ValueError("capture.fps must be between 1 and 120")

    inp_raw = _section(raw, "input")
    input_cfg = InputConfig(
        require_foreground=bool(inp_raw.get("require_foreground", True)),
        emergency_stop_key=str(inp_raw.get("emergency_stop_key", "f8")).lower(),
        potion_enabled=bool(inp_raw.get("potion_enabled", True)),
        potion_mode=str(inp_raw.get("potion_mode", "health")).lower(),
        potion_key=str(inp_raw.get("potion_key", "q")),
        mount_key=str(inp_raw.get("mount_key", "")).lower(),
        potion_threshold=float(inp_raw.get("potion_threshold", 0.55)),
        potion_interval_s=float(inp_raw.get("potion_interval_s", 1.2)),
        potion_timer_s=float(inp_raw.get("potion_timer_s", 8.0)),
        action_gap_s=float(inp_raw.get("action_gap_s", 0.035)),
        aim_jitter_px=int(inp_raw.get("aim_jitter_px", 4)),
    )
    if not 0 <= input_cfg.potion_threshold <= 1:
        raise ValueError("input.potion_threshold must be in 0..1")
    if input_cfg.potion_mode not in {"health", "timer"}:
        raise ValueError("input.potion_mode must be 'health' or 'timer'")

    roam_raw = _section(raw, "roam")
    roam = RoamConfig(
        leg_interval_s=float(roam_raw.get("leg_interval_s", 4.0)),
        turn_jitter_deg=float(roam_raw.get("turn_jitter_deg", 35.0)),
        click_distance_px=int(roam_raw.get("click_distance_px", 650)),
        min_click_interval_s=float(roam_raw.get("min_click_interval_s", 1.0)),
        play_area_margin=float(roam_raw.get("play_area_margin", 0.20)),
        click_y_min=float(roam_raw.get("click_y_min", 0.30)),
        click_y_max=float(roam_raw.get("click_y_max", 0.60)),
        stuck_diff_threshold=float(roam_raw.get("stuck_diff_threshold", 1.5)),
        stuck_confirm_s=float(roam_raw.get("stuck_confirm_s", 2.0)),
    )
    if not 0.05 <= roam.play_area_margin < 0.45:
        raise ValueError("roam.play_area_margin must be in 0.05..0.45")
    if not 0.05 <= roam.click_y_min < roam.click_y_max <= 0.85:
        raise ValueError("roam.click_y_min/click_y_max are out of range")

    regions_raw = _section(raw, "regions")
    regions: dict[str, Region] = {}
    for name, default in DEFAULT_REGIONS.items():
        regions[name] = _region_from_value(regions_raw.get(name, default), name)

    globe_raw = _section(raw, "globe")
    globe = GlobeConfig(
        health_hsv=_as_hsv_ranges(globe_raw.get("health_hsv"), DEFAULT_HEALTH_HSV),
        resource_hsv=_as_hsv_ranges(globe_raw.get("resource_hsv"), DEFAULT_RESOURCE_HSV),
        health_full_ratio=float(globe_raw.get("health_full_ratio", 0.42)),
        resource_full_ratio=float(globe_raw.get("resource_full_ratio", 0.42)),
        empty_ratio=float(globe_raw.get("empty_ratio", 0.015)),
        smoothing=float(globe_raw.get("smoothing", 0.35)),
        death_confirm_s=float(globe_raw.get("death_confirm_s", 3.0)),
    )
    if not (0 <= globe.empty_ratio < globe.health_full_ratio <= 1):
        raise ValueError("globe.empty_ratio must be < globe.health_full_ratio <= 1")
    if not (0 <= globe.empty_ratio < globe.resource_full_ratio <= 1):
        raise ValueError("globe.empty_ratio must be < globe.resource_full_ratio <= 1")
    if not 0 < globe.smoothing <= 1:
        raise ValueError("globe.smoothing must be in (0, 1]")

    enemy_raw = _section(raw, "enemy")
    enemy = EnemyConfig(
        hsv=_as_hsv_ranges(enemy_raw.get("hsv"), DEFAULT_ENEMY_HSV),
        min_width=int(enemy_raw.get("min_width", 12)),
        max_width=int(enemy_raw.get("max_width", 420)),
        min_height=int(enemy_raw.get("min_height", 2)),
        max_height=int(enemy_raw.get("max_height", 32)),
        min_aspect=float(enemy_raw.get("min_aspect", 2.0)),
        max_aspect=float(enemy_raw.get("max_aspect", 18.0)),
        min_fill=float(enemy_raw.get("min_fill", 0.45)),
        persistence_frames=int(enemy_raw.get("persistence_frames", 2)),
        track_radius_px=int(enemy_raw.get("track_radius_px", 28)),
        aim_offset_y=float(enemy_raw.get("aim_offset_y", 0.0)),
        exclude=tuple(
            _region_from_value(value, f"enemy.exclude[{i}]")
            for i, value in enumerate(enemy_raw.get("exclude") or [])
        ),
    )

    skills_raw = raw.get("skills") or _default_skills()
    if not isinstance(skills_raw, list) or not skills_raw:
        raise ValueError("skills must be a non-empty list")
    skills: list[SkillConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(skills_raw):
        if not isinstance(item, dict):
            raise ValueError(f"skills[{i}] must be a mapping")
        name = str(item.get("name", f"skill_{i + 1}"))
        if name in seen:
            raise ValueError(f"duplicate skill name: {name}")
        seen.add(name)
        template = item.get("template")
        skills.append(
            SkillConfig(
                name=name,
                key=str(item.get("key", str(i + 1))),
                enabled=bool(item.get("enabled", True)),
                priority=int(item.get("priority", 10)),
                requires_enemy=bool(item.get("requires_enemy", True)),
                min_interval_s=float(item.get("min_interval_s", 0.3)),
                fallback_cooldown_s=float(item.get("fallback_cooldown_s", 2.0)),
                template=str(template).replace("\\", "/") if template else None,
                threshold=float(item.get("threshold", 0.16)),
                utility=bool(item.get("utility", False)),
                utility_interval_s=float(item.get("utility_interval_s", 15.0)),
            )
        )

    form_raw = _section(raw, "form")
    form_roi_value = form_raw.get("roi")
    form_roi = None if form_roi_value in (None, "auto") else _region_from_value(form_roi_value, "form.roi")
    form = FormConfig(
        enabled=bool(form_raw.get("enabled", False)),
        mode=str(form_raw.get("mode", "visual")).lower(),
        toggle_key=str(form_raw.get("toggle_key", "2")),
        toggle_skill=str(form_raw.get("toggle_skill", "skill_2")),
        roi=form_roi,
        metric=str(form_raw.get("metric", "orange_ratio")).lower(),
        demon_threshold=float(form_raw.get("demon_threshold", 0.66)),
        ready_threshold=float(form_raw.get("ready_threshold", 0.25)),
        assume_human_at_start=bool(form_raw.get("assume_human_at_start", True)),
        higher_is_demon=bool(form_raw.get("higher_is_demon", True)),
        hysteresis=float(form_raw.get("hysteresis", 0.04)),
        confirm_frames=int(form_raw.get("confirm_frames", 3)),
        post_toggle_grace_s=float(form_raw.get("post_toggle_grace_s", 2.0)),
        retry_interval_s=float(form_raw.get("retry_interval_s", 1.0)),
        fallback_cooldown_s=float(form_raw.get("fallback_cooldown_s", 11.0)),
        toggle_requires_ready=bool(form_raw.get("toggle_requires_ready", True)),
        buff_template=str(form_raw["buff_template"]).replace("\\", "/") if form_raw.get("buff_template") else None,
        buff_threshold=float(form_raw.get("buff_threshold", 0.70)),
        buff_template_height=int(form_raw.get("buff_template_height", 1440)),
        toggle_confirm_s=float(form_raw.get("toggle_confirm_s", 3.0)),
        max_failed_toggles=int(form_raw.get("max_failed_toggles", 2)),
        act_while_human=bool(form_raw.get("act_while_human", False)),
        buff_off_confirm_s=float(form_raw.get("buff_off_confirm_s", 1.0)),
        glow_slots=tuple(
            _region_from_value(value, f"form.glow_slots[{i}]")
            for i, value in enumerate(form_raw.get("glow_slots") or [])
        ),
        glow_ring_px=int(form_raw.get("glow_ring_px", 7)),
        glow_threshold=float(form_raw.get("glow_threshold", 0.18)),
        glow_min_slots=int(form_raw.get("glow_min_slots", 1)),
    )
    if form.metric not in {"orange_ratio", "brightness"}:
        raise ValueError("form.metric must be 'orange_ratio' or 'brightness'")
    if form.mode not in {"visual", "toggle_state", "buff", "glow"}:
        raise ValueError("form.mode must be 'visual', 'toggle_state', 'buff' or 'glow'")
    if form.mode == "glow" and len(form.glow_slots) < form.glow_min_slots:
        raise ValueError("form.mode 'glow' needs at least glow_min_slots entries in form.glow_slots")
    if form.mode == "buff" and not form.buff_template:
        raise ValueError("form.mode 'buff' needs form.buff_template")
    if not 0 <= form.demon_threshold <= 1:
        raise ValueError("form.demon_threshold must be in 0..1")
    if not 0 <= form.ready_threshold <= 1:
        raise ValueError("form.ready_threshold must be in 0..1")
    if not 0 <= form.hysteresis <= 0.5:
        raise ValueError("form.hysteresis must be in 0..0.5")
    if form.confirm_frames < 1:
        raise ValueError("form.confirm_frames must be >= 1")

    nav_raw = _section(raw, "nav")
    nav_defaults = NavConfig()
    nav_kwargs: dict[str, Any] = {}
    for name, default in vars(nav_defaults).items():
        if name not in nav_raw:
            continue
        if name == "walkable_hsv":
            nav_kwargs[name] = _as_hsv_ranges(nav_raw[name], DEFAULT_WALKABLE_HSV)
        elif name == "monster_hsv":
            nav_kwargs[name] = _as_hsv_ranges(nav_raw[name], DEFAULT_MONSTER_DOT_HSV)
        elif name == "elite_hsv":
            nav_kwargs[name] = _as_hsv_ranges(nav_raw[name], DEFAULT_ELITE_DOT_HSV)
        else:
            nav_kwargs[name] = type(default)(nav_raw[name])
    nav = NavConfig(**nav_kwargs) if "nav" in raw else NavConfig(enabled=False)
    if nav.rays < 8:
        raise ValueError("nav.rays must be >= 8")
    if not 0.0 <= nav.min_clearance < 1.0:
        raise ValueError("nav.min_clearance must be in 0..1")

    routes_raw = _section(raw, "routes")
    routes = RoutesConfig(
        enabled=bool(routes_raw.get("enabled", False)),
        folder=str(routes_raw.get("folder", "routes")),
        weight=float(routes_raw.get("weight", 2.0)),
        lookahead=int(routes_raw.get("lookahead", 5)),
        leash_px=float(routes_raw.get("leash_px", 0.0)),
    )

    death_raw = _section(raw, "death")
    death_template = death_raw.get("template")
    death = DeathConfig(
        enabled=bool(death_raw.get("enabled", "death" in raw)),
        action=str(death_raw.get("action", "stop")).lower(),
        max_saturation=float(death_raw.get("max_saturation", 14.0)),
        black_max_value=float(death_raw.get("black_max_value", 12.0)),
        confirm_s=float(death_raw.get("confirm_s", 2.0)),
        hud_template=str(death_raw.get("hud_template", "templates/hud_key1.png")).replace("\\", "/") or None,
        hud_region=_region_from_value(death_raw.get("hud_region", [0.39, 0.95, 0.16, 0.05]), "death.hud_region"),
        hud_threshold=float(death_raw.get("hud_threshold", 0.70)),
        template=str(death_template).replace("\\", "/") if death_template else None,
        template_threshold=float(death_raw.get("template_threshold", 0.75)),
        save_screenshot=bool(death_raw.get("save_screenshot", True)),
    )
    if death.action not in {"stop", "pause"}:
        raise ValueError("death.action must be 'stop' or 'pause'")

    combat_raw = _section(raw, "combat")
    combat = CombatConfig(
        mode=str(combat_raw.get("mode", "bars")).lower(),
        walk_s=float(combat_raw.get("walk_s", 2.5)),
        fight_s=float(combat_raw.get("fight_s", 1.2)),
        fight_max_s=float(combat_raw.get("fight_max_s", 8.0)),
        kill_extend_s=float(combat_raw.get("kill_extend_s", 1.5)),
        aim_distance_px=int(combat_raw.get("aim_distance_px", 220)),
        sweep_deg=float(combat_raw.get("sweep_deg", 35.0)),
        kill_region=_region_from_value(
            combat_raw.get("kill_region", [0.7420, 0.1270, 0.0250, 0.0240]), "combat.kill_region"
        ),
        kill_diff=float(combat_raw.get("kill_diff", 10.0)),
        fight_radius_px=float(combat_raw.get("fight_radius_px", 28.0)),
        elite_fight_radius_px=float(combat_raw.get("elite_fight_radius_px", 40.0)),
        linger_s=float(combat_raw.get("linger_s", 1.0)),
        attack_range_px=float(combat_raw.get("attack_range_px", 14.0)),
        approach_timeout_s=float(combat_raw.get("approach_timeout_s", 2.5)),
        approach_click_s=float(combat_raw.get("approach_click_s", 0.4)),
        max_fight_s=float(combat_raw.get("max_fight_s", 10.0)),
        ignore_s=float(combat_raw.get("ignore_s", 60.0)),
        hunt_weight=float(combat_raw.get("hunt_weight", 1.2)),
        screen_per_minimap_px=float(combat_raw.get("screen_per_minimap_px", 9.0)),
        loot_enabled=bool(combat_raw.get("loot_enabled", True)),
        loot_range_px=float(combat_raw.get("loot_range_px", 140.0)),
        loot_near_px=float(combat_raw.get("loot_near_px", 35.0)),
        loot_weight=float(combat_raw.get("loot_weight", 2.5)),
        loot_timeout_s=float(combat_raw.get("loot_timeout_s", 8.0)),
        loot_ignore_s=float(combat_raw.get("loot_ignore_s", 120.0)),
        loot_click_interval_s=float(combat_raw.get("loot_click_interval_s", 0.6)),
        star_threshold=float(combat_raw.get("star_threshold", 0.70)),
        label_hsv=_as_hsv_ranges(combat_raw.get("label_hsv"), [
        ((15, 140, 185), (27, 255, 255)),   # legendary orange / unique gold
        ((0, 0, 185), (180, 60, 255)),      # white (common, materials)
        ((100, 90, 170), (125, 255, 255)),  # blue (magic)
        ((26, 90, 185), (35, 255, 255)),    # yellow (rare)
        ((0, 150, 170), (5, 255, 255)),     # red (live: "Amulet" missed; 0 false labels in 132 fight frames)
        ((174, 150, 170), (180, 255, 255)),
    ]),
        label_unique_hsv=_as_hsv_ranges(combat_raw.get("label_unique_hsv"), [((45, 150, 200), (66, 255, 255))]),
        loot_label_radius_px=int(combat_raw.get("loot_label_radius_px", 650)),
        aim_min_px=int(combat_raw.get("aim_min_px", 120)),
        aim_max_px=int(combat_raw.get("aim_max_px", 420)),
    )
    if combat.mode not in {"bars", "pulse", "hunt"}:
        raise ValueError("combat.mode must be 'bars', 'pulse' or 'hunt'")

    det_raw = _section(raw, "detector")
    det_defaults = DetectorConfig()
    roi = _region_from_value(det_raw.get("roi", list(det_defaults.roi)), "detector.roi")
    detector = DetectorConfig(
        enabled=bool(det_raw.get("enabled", False)),
        model=str(det_raw.get("model", det_defaults.model)).replace("\\", "/"),
        classes=tuple(str(c) for c in det_raw.get("classes", det_defaults.classes)),
        input_size=int(det_raw.get("input_size", det_defaults.input_size)),
        confidence=float(det_raw.get("confidence", det_defaults.confidence)),
        iou=float(det_raw.get("iou", det_defaults.iou)),
        every_n_frames=int(det_raw.get("every_n_frames", det_defaults.every_n_frames)),
        backend=str(det_raw.get("backend", det_defaults.backend)).lower(),
        roi=(roi.x, roi.y, roi.w, roi.h),
        aim=bool(det_raw.get("aim", False)),
        aim_radius_px=int(det_raw.get("aim_radius_px", det_defaults.aim_radius_px)),
    )
    if detector.backend not in {"auto", "opencv", "onnxruntime"}:
        raise ValueError("detector.backend must be 'auto', 'opencv' or 'onnxruntime'")
    if detector.input_size % 32 != 0:
        raise ValueError("detector.input_size must be a multiple of 32")

    mode = str(raw.get("mode", "assist")).lower()
    if mode not in {"assist", "roam"}:
        raise ValueError("mode must be 'assist' or 'roam'")

    return BotConfig(
        capture=capture,
        input=input_cfg,
        roam=roam,
        regions=regions,
        globe=globe,
        enemy=enemy,
        skills=tuple(skills),
        mode=mode,
        project_root=path.parent,
        config_path=path,
        form=form,
        nav=nav,
        death=death,
        routes=routes,
        combat=combat,
        detector=detector,
    )


def save_regions(config_path: str | Path, regions: dict[str, Region]) -> None:
    """Update only the regions block, preserving the rest of the YAML."""
    path = Path(config_path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    block = raw.setdefault("regions", {})
    for name, region in regions.items():
        block[name] = region.as_list()
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def save_form(
    config_path: str | Path,
    region: Region,
    human_metric: float,
    demon_metric: float,
    metric: str = "orange_ratio",
) -> float:
    """Persist a calibrated form ROI and threshold; returns the threshold."""
    threshold = (human_metric + demon_metric) / 2.0
    path = Path(config_path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("config root must be a mapping")
    block = raw.setdefault("form", {})
    block["enabled"] = True
    block["roi"] = region.as_list()
    block["metric"] = metric
    block["demon_threshold"] = round(threshold, 4)
    block["higher_is_demon"] = demon_metric >= human_metric
    block["human_metric"] = round(human_metric, 4)
    block["demon_metric"] = round(demon_metric, 4)
    path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return threshold
