"""Minimap navigation, demon-buff form gate, and death-screen detection."""

from __future__ import annotations

import math
import random
from pathlib import Path

import cv2
import numpy as np
import pytest

import d4bot.brain as brain_module
import d4bot.vision as vision_module
from d4bot.brain import CombatBrain
from d4bot.config import (
    BotConfig,
    CaptureConfig,
    DeathConfig,
    EnemyConfig,
    FormConfig,
    GlobeConfig,
    InputConfig,
    NavConfig,
    Region,
    RoamConfig,
    SkillConfig,
)
from d4bot.minimap import MinimapTracker, ray_clearance, walkable_mask
from d4bot.vision import BuffReading, Vision

W, H = 2560, 1440
MINIMAP = Region(0.8348, 0.0417, 0.1543, 0.1993)
OPEN = (150, 170, 192)     # beige parchment: hue ~15, sat ~55, val ~190
BLOCKED = (20, 30, 118)    # Helltide red: sat ~210, val ~118 (real: 106-129)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(brain_module.time, "monotonic", c)
    monkeypatch.setattr(vision_module.time, "monotonic", c)
    return c


def _config(tmp_path: Path, **kwargs) -> BotConfig:  # type: ignore[no-untyped-def]
    defaults = dict(
        capture=CaptureConfig(),
        input=InputConfig(potion_enabled=False),
        roam=RoamConfig(),
        regions={
            "health_globe": Region(0.305, 0.815, 0.105, 0.140),
            "resource_globe": Region(0.695, 0.815, 0.095, 0.140),
            "skill_bar": Region(0.383, 0.895, 0.222, 0.070),
            "enemy_roi": Region(0.100, 0.080, 0.600, 0.620),
            "center": Region(0.400, 0.350, 0.200, 0.250),
            "minimap": MINIMAP,
            "buff_bar": Region(0.39, 0.80, 0.31, 0.065),
        },
        globe=GlobeConfig(),
        enemy=EnemyConfig(),
        skills=(SkillConfig(name="skill_6", key="mouse_right", priority=0, min_interval_s=0.1, fallback_cooldown_s=0.0),),
        mode="roam",
        project_root=tmp_path,
        config_path=tmp_path / "config.yaml",
        nav=NavConfig(noise=0.02),
    )
    defaults.update(kwargs)
    return BotConfig(**defaults)


def _maze() -> tuple[np.ndarray, np.ndarray]:
    """A world with corridors, rooms and dead ends, surrounded by blocked ground."""
    world = np.zeros((1400, 1600, 3), dtype=np.uint8)
    world[:] = BLOCKED
    corridors = [
        ((300, 300), (1300, 300)),
        ((300, 300), (300, 1100)),
        ((300, 1100), (1300, 1100)),
        ((1300, 300), (1300, 1100)),
        ((800, 300), (800, 700)),     # dead end into the middle
        ((300, 700), (600, 700)),     # dead end
    ]
    for a, b in corridors:
        cv2.line(world, a, b, OPEN, 70)
    cv2.circle(world, (800, 700), 90, OPEN, -1)
    walls = np.all(world == BLOCKED, axis=2)
    # Parchment grain + blocked-area hatching, like the real minimap. Without
    # texture, motion along a uniform corridor is unobservable.
    rng = np.random.default_rng(1)
    grain = cv2.GaussianBlur(rng.normal(0, 60, world.shape[:2]).astype(np.float32), (0, 0), 1.5)
    world = np.clip(world.astype(np.float32) + grain[:, :, None], 0, 255).astype(np.uint8)
    blocked = (world[:, :, 2] < 175) & (world[:, :, 0] < 70)
    for y in range(6, world.shape[0], 14):
        row = blocked[y]
        world[y, row] = (110, 120, 150)
    return world, walls


def _frame(world: np.ndarray, pos: np.ndarray) -> np.ndarray:
    frame = np.full((H, W, 3), (60, 80, 100), dtype=np.uint8)
    x, y, w, h = MINIMAP.pixels(W, H)
    px, py = int(round(pos[0])), int(round(pos[1]))
    frame[y : y + h, x : x + w] = world[py - h // 2 : py - h // 2 + h, px - w // 2 : px - w // 2 + w]
    # Static player arrow in the middle, like the real minimap.
    cv2.circle(frame, (x + w // 2, y + h // 2), 7, (240, 240, 240), -1)
    cv2.circle(frame, (x + w // 2, y + h // 2), 7, (40, 40, 40), 2)
    return frame


def _walk(walls: np.ndarray, pos: np.ndarray, target: np.ndarray | None, speed: float) -> np.ndarray:
    if target is None:
        return pos
    delta = target - pos
    dist = float(np.hypot(*delta))
    if dist < 1.0:
        return pos
    step = pos + delta / dist * min(speed, dist)
    if walls[int(step[1]), int(step[0])]:
        return pos  # walked into a wall
    return step


def _simulate(tmp_path: Path, clock: Clock, true_rotation_deg: float, seconds: float, start=(310, 310)):
    config = _config(tmp_path)
    world, walls = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(7))
    pos = np.array(start, dtype=np.float64)
    target = None
    visited: set[tuple[int, int]] = set()
    phi = math.radians(true_rotation_deg)
    dt = 0.05
    for _ in range(int(seconds / dt)):
        clock.now += dt
        obs = vision.observe(_frame(world, pos))
        decision = brain.decide(obs, foreground=True)
        for action in decision.actions:
            if action.kind != "click":
                continue
            # Screen vector -> world direction: undo the isometric squash, then
            # apply the game's (unknown to the bot) minimap rotation.
            sx = action.x - W * config.nav.player_x
            sy = (action.y - H * config.nav.player_y) / config.nav.y_scale
            angle = math.atan2(sy, sx) - phi
            target = pos + 400.0 * np.array([math.cos(angle), math.sin(angle)])
        pos = _walk(walls, pos, target, speed=3.0)
        visited.add((int(pos[0] // 50), int(pos[1] // 50)))
    return brain, visited


def test_walkable_mask_ignores_hatching_and_player_arrow() -> None:
    config = NavConfig()
    crop = np.zeros((200, 260, 3), dtype=np.uint8)
    crop[:] = BLOCKED
    crop[:, 100:160] = OPEN
    for y in range(5, 200, 12):  # pale hatching lines over the blocked area
        cv2.line(crop, (0, y), (95, y), (170, 175, 185), 1)
    cv2.circle(crop, (130, 100), 8, (40, 40, 40), 2)
    mask = walkable_mask(crop, config)
    assert mask[100, 130] == 255
    assert mask[:, :90].mean() < 10
    angles, clearance = ray_clearance(mask, 24, config.ray_start_px)
    up = int(np.argmin(np.abs(np.cos(angles - (-math.pi / 2)) - 1)))
    left = int(np.argmin(np.abs(angles - math.pi)))
    assert clearance[up] > 0.9
    assert clearance[left] < 0.2


def test_minimap_tracker_measures_player_motion() -> None:
    config = NavConfig()
    world, walls = _maze()
    tracker = MinimapTracker(config)
    pos = np.array([600.0, 300.0])
    for _ in range(11):
        reading = tracker.update(_frame(world, pos), MINIMAP)
        pos += (2.0, 0.0)
    assert reading.valid
    # 10 steps of +2px; small under-estimation from the static border is fine.
    assert 15.0 <= reading.position[0] <= 21.0
    assert abs(reading.position[1]) < 2.0


def test_nav_explores_maze_without_getting_trapped(tmp_path: Path, clock: Clock) -> None:
    brain, visited = _simulate(tmp_path, clock, true_rotation_deg=0.0, seconds=60.0)
    # The corridor loop alone is ~60 cells of 50px; a pacing/trapped bot sees a handful.
    assert len(visited) >= 35, len(visited)


def test_nav_learns_minimap_rotation(tmp_path: Path, clock: Clock) -> None:
    brain, visited = _simulate(tmp_path, clock, true_rotation_deg=25.0, seconds=60.0)
    assert abs(math.degrees(brain.rotation) - 25.0) < 10.0, math.degrees(brain.rotation)
    assert len(visited) >= 25


def test_nav_turns_away_when_stuck(tmp_path: Path, clock: Clock) -> None:
    config = _config(tmp_path)
    world, walls = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(3))
    pos = np.array([800.0, 700.0])
    headings = []
    for _ in range(80):  # 4s of clicking while the character never moves
        clock.now += 0.05
        decision = brain.decide(vision.observe(_frame(world, pos)), foreground=True)
        if any(a.kind == "click" for a in decision.actions):
            headings.append(brain.heading)
    assert brain.stuck_count >= 1
    assert len({round(h, 2) for h in headings}) >= 2


def _buff_config(tmp_path: Path) -> BotConfig:
    return _config(
        tmp_path,
        mode="assist",
        form=FormConfig(
            enabled=True,
            mode="buff",
            buff_template="templates/demon_buff.png",
            ready_threshold=0.0,
            post_toggle_grace_s=0.5,
            retry_interval_s=0.5,
            fallback_cooldown_s=11.0,
            toggle_confirm_s=3.0,
        ),
    )


def _buff_obs(config: BotConfig, present: bool | None):  # type: ignore[no-untyped-def]
    vision = Vision(config)
    obs = vision.observe(np.full((H, W, 3), 90, dtype=np.uint8))
    obs.buff = BuffReading(present, 0.95 if present else 0.4)
    return obs


def test_buff_gate_detects_starting_form_without_flags(tmp_path: Path, clock: Clock) -> None:
    config = _buff_config(tmp_path)
    brain = CombatBrain(config)
    assert brain.decide(_buff_obs(config, None), True).state == "FORM?"

    demon = brain.decide(_buff_obs(config, True), True)
    assert ("press", "2") not in [(a.kind, a.key) for a in demon.actions]

    human_brain = CombatBrain(config)
    clock.now += 1.0
    human = human_brain.decide(_buff_obs(config, False), True)
    assert human.state == "TOGGLE"
    assert ("press", "2") in [(a.kind, a.key) for a in human.actions]


def test_buff_gate_waits_cooldown_and_confirms_toggle(tmp_path: Path, clock: Clock) -> None:
    config = _buff_config(tmp_path)
    brain = CombatBrain(config)
    brain.decide(_buff_obs(config, True), True)

    clock.now += 5.0  # demon ends
    assert brain.decide(_buff_obs(config, False), True).note.startswith("demon cooldown")
    clock.now += 11.5
    pressed = brain.decide(_buff_obs(config, False), True)
    assert ("press", "2") in [(a.kind, a.key) for a in pressed.actions]

    # No double press while waiting for the buff to appear.
    clock.now += 1.0
    waiting = brain.decide(_buff_obs(config, False), True)
    assert waiting.state == "TOGGLE" and not waiting.actions

    # Buff never appeared: counts a failure and tries again; second failure backs off.
    clock.now += 2.5
    retry = brain.decide(_buff_obs(config, False), True)
    assert ("press", "2") in [(a.kind, a.key) for a in retry.actions]
    clock.now += 3.5
    backoff = brain.decide(_buff_obs(config, False), True)
    assert "toggle failing" in backoff.note and not backoff.actions


def test_buff_template_matches_real_capture() -> None:
    root = Path(__file__).resolve().parents[1]
    template = cv2.imread(str(root / "templates" / "demon_buff.png"))
    assert template is not None
    config = _config(
        root,
        form=FormConfig(enabled=True, mode="buff", buff_template="templates/demon_buff.png", confirm_frames=1),
    )
    vision = Vision(config)
    # Paste the icon into a synthetic buff bar at a different slot than it was captured from.
    frame = np.full((H, W, 3), (70, 90, 110), dtype=np.uint8)
    x, y, _, _ = config.regions["buff_bar"].pixels(W, H)
    frame[y + 20 : y + 20 + template.shape[0], x + 300 : x + 300 + template.shape[1]] = template
    assert vision.observe(frame).buff.present is True
    empty = Vision(config).observe(np.full((H, W, 3), (70, 90, 110), dtype=np.uint8))
    assert empty.buff.present is False


def test_grey_screen_is_death_after_confirm(tmp_path: Path, clock: Clock) -> None:
    config = _config(tmp_path, mode="assist", death=DeathConfig(enabled=True, confirm_s=1.0))
    vision = Vision(config)
    rng = np.random.default_rng(0)
    colour = rng.integers(0, 255, size=(H // 8, W // 8, 3), dtype=np.uint8)
    colour = cv2.resize(colour, (W, H), interpolation=cv2.INTER_NEAREST)
    grey = cv2.cvtColor(cv2.cvtColor(colour, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR)

    assert not vision.observe(colour).dead
    assert not vision.observe(grey).dead          # first grey frame
    clock.now += 1.2
    obs = vision.observe(grey)
    assert obs.dead
    assert CombatBrain(config).decide(obs, True).state == "DEAD"

    clock.now += 0.1
    assert not vision.observe(colour).dead        # colour back = alive again


def test_covered_window_is_not_death_after_focus_returns(tmp_path: Path, clock: Clock) -> None:
    # Live path1: another app covered the game -> PAUSED, then DEAD 3s later.
    config = _config(tmp_path, mode="assist", death=DeathConfig(enabled=True, confirm_s=1.0))
    vision = Vision(config)
    brain = CombatBrain(config)
    grey = np.full((H, W, 3), 128, dtype=np.uint8)

    vision.observe(grey)
    clock.now += 1.5
    obs = vision.observe(grey)
    assert obs.dead
    assert brain.decide(obs, False).state == "PAUSED"
    clock.now += 0.1
    assert brain.decide(vision.observe(grey), True).state == "PAUSED"   # focus back: settle first
    clock.now += 2.1
    assert brain.decide(vision.observe(grey), True).state == "DEAD"     # still gone on its own


def test_black_screen_pauses_input(tmp_path: Path, clock: Clock) -> None:
    config = _config(tmp_path, death=DeathConfig(enabled=True))
    obs = Vision(config).observe(np.zeros((H, W, 3), dtype=np.uint8))
    decision = CombatBrain(config).decide(obs, True)
    assert decision.state == "LOADING"
    assert decision.actions == []


def test_blinking_buff_does_not_flip_to_human(tmp_path: Path, clock: Clock) -> None:
    root = Path(__file__).resolve().parents[1]
    template = cv2.imread(str(root / "templates" / "demon_buff.png"))
    config = _config(
        root,
        form=FormConfig(enabled=True, mode="buff", buff_template="templates/demon_buff.png", buff_off_confirm_s=1.0),
    )
    vision = Vision(config)
    on = np.full((H, W, 3), (70, 90, 110), dtype=np.uint8)
    x, y, _, _ = config.regions["buff_bar"].pixels(W, H)
    on[y + 20 : y + 20 + template.shape[0], x + 100 : x + 100 + template.shape[1]] = template
    off = np.full((H, W, 3), (70, 90, 110), dtype=np.uint8)

    for _ in range(4):
        clock.now += 0.05
        assert vision.observe(on).buff.present in (None, True)
    assert vision.observe(on).buff.present is True
    # Expiring icon blinks: ~0.4s off, ~0.4s on, repeatedly.
    for _ in range(5):
        for frame in [off] * 8 + [on] * 8:
            clock.now += 0.05
            assert vision.observe(frame).buff.present is True
    # Really gone: flips to human after buff_off_confirm_s.
    states = []
    for _ in range(30):
        clock.now += 0.05
        states.append(vision.observe(off).buff.present)
    assert states[0] is True and states[-1] is False


def test_dry_run_does_not_learn_from_the_human_player(tmp_path: Path, clock: Clock) -> None:
    config = _config(tmp_path)
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(5), live=False)
    pos = np.array([400.0, 300.0])
    for _ in range(200):
        clock.now += 0.05
        pos += (2.0, 0.6)  # the human walks somewhere the bot did not click
        brain.decide(vision.observe(_frame(world, pos)), foreground=True)
    assert brain.rotation == 0.0
    assert brain.stuck_count == 0


def _skill_bar_frame(burning: list[bool]) -> np.ndarray:
    """Synthetic skill bar: orange icon art in every slot, burning frame only where asked."""
    frame = np.full((H, W, 3), (60, 70, 80), dtype=np.uint8)
    for i, (x0, burn) in enumerate(zip((1032, 1116, 1284), burning)):
        cv2.rectangle(frame, (x0, 1305), (x0 + 76, 1380), (40, 60, 120), -1)          # dull red frame
        if burn:
            cv2.rectangle(frame, (x0 - 3, 1302), (x0 + 79, 1383), (40, 150, 255), 6)  # burning orange
        cv2.rectangle(frame, (x0 + 12, 1317), (x0 + 64, 1368), (60, 160, 240), -1)    # orange icon art
    return frame


def _glow_config(tmp_path: Path) -> BotConfig:
    return _config(
        tmp_path,
        mode="assist",
        form=FormConfig(
            enabled=True,
            mode="glow",
            glow_slots=(
                Region(0.4031, 0.9063, 0.0297, 0.0521),
                Region(0.4359, 0.9063, 0.0297, 0.0521),
                Region(0.5016, 0.9063, 0.0297, 0.0521),
            ),
            confirm_frames=1,
        ),
    )


def test_glow_reads_burning_skill_frames_not_icon_art(tmp_path: Path, clock: Clock) -> None:
    config = _glow_config(tmp_path)
    assert Vision(config).observe(_skill_bar_frame([True, True, True])).buff.present is True
    assert Vision(config).observe(_skill_bar_frame([False, False, False])).buff.present is False
    # Skills on cooldown do not burn: any one burning slot means demon (live run 4).
    assert Vision(config).observe(_skill_bar_frame([True, False, True])).buff.present is True
    assert Vision(config).observe(_skill_bar_frame([False, True, False])).buff.present is True


def test_missing_hud_pauses_then_counts_as_death(tmp_path: Path, clock: Clock) -> None:
    root = Path(__file__).resolve().parents[1]
    config = _config(root, mode="assist", death=DeathConfig(enabled=True, confirm_s=2.0))
    template = cv2.imread(str(root / "templates" / "hud_key1.png"))
    assert template is not None
    hud = np.full((H, W, 3), (70, 90, 110), dtype=np.uint8)
    hud[1395 : 1395 + template.shape[0], 1058 : 1058 + template.shape[1]] = template
    no_hud = np.full((H, W, 3), (50, 60, 75), dtype=np.uint8)
    cv2.putText(no_hud, "YOU HAVE DIED", (900, 200), cv2.FONT_HERSHEY_SIMPLEX, 3, (230, 230, 230), 5)

    vision = Vision(config)
    brain = CombatBrain(config)
    assert not vision.observe(hud).screen.no_hud
    clock.now += 0.1
    obs = vision.observe(no_hud)
    assert obs.screen.no_hud and not obs.dead
    assert brain.decide(obs, True).state == "NOHUD"
    clock.now += 2.1
    obs = vision.observe(no_hud)
    assert obs.dead
    assert brain.decide(obs, True).state == "DEAD"
    clock.now += 0.1
    assert not vision.observe(hud).dead


def test_loading_art_mid_farm_is_loading_not_death(tmp_path: Path, clock: Clock) -> None:
    root = Path(__file__).resolve().parents[1]
    config = _config(root, mode="assist", death=DeathConfig(enabled=True, confirm_s=2.0))
    emblem = cv2.imread(str(root / "templates" / "warplan" / "loading_emblem.png"))
    assert emblem is not None
    loading = np.full((H, W, 3), (40, 30, 90), dtype=np.uint8)
    cv2.putText(loading, "Overpower is a stacking buff", (700, 1340), cv2.FONT_HERSHEY_SIMPLEX, 2, (220, 220, 220), 3)
    loading[1260 : 1260 + emblem.shape[0], 2380 : 2380 + emblem.shape[1]] = emblem

    vision = Vision(config)
    brain = CombatBrain(config)
    for _ in range(40):
        clock.now += 0.1
        obs = vision.observe(loading)
        assert obs.black and not obs.screen.no_hud and not obs.dead
        assert brain.decide(obs, True).state == "LOADING"


def test_pulse_walks_fights_and_extends_on_kills(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="pulse", walk_s=2.0, fight_s=1.0, fight_max_s=5.0, kill_extend_s=1.5))
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(2))
    pos = np.array([600.0, 300.0])
    base = _frame(world, pos)
    x, y, w, h = config.combat.kill_region.pixels(W, H)
    kill = base.copy()   # counter changes at a kill and then stays changed
    kill[y : y + h, x : x + w] = 255 - kill[y : y + h, x : x + w]
    kill2 = base.copy()
    kill2[y : y + h, x : x + w] = 128

    states = []
    for i in range(160):  # 8s
        clock.now += 0.05
        frame = base if i < 50 else kill if i < 75 else kill2  # kills at 2.5s and 3.75s, both mid-fight
        decision = brain.decide(vision.observe(frame), foreground=True)
        states.append(decision.state)
        if decision.state == "PULSE":
            assert any(a.kind == "press" and a.key == "mouse_right" for a in decision.actions) or not decision.actions
    first_fight = states.index("PULSE")
    assert 38 <= first_fight <= 42                       # after ~2s of walking
    fight_len = states[first_fight:].index("ROAM")
    assert fight_len > 50                                # extended well past fight_s by kills
    assert brain.kill_signals == 2

    # A kill while walking starts a fight immediately.
    walking = states.index("ROAM", first_fight + fight_len)
    assert walking < len(states)


def _dot(frame: np.ndarray, dx: int, dy: int, colour=(20, 150, 235)) -> None:
    """Draw a Subo-style monster dot at an offset from the minimap centre."""
    x, y, w, h = MINIMAP.pixels(W, H)
    centre = (x + w // 2 + dx, y + h // 2 + dy)
    cv2.circle(frame, centre, 3, colour, -1)   # ~29 px, like real dots (18-24 px)
    cv2.circle(frame, centre, 5, (30, 30, 30), 1)


def test_minimap_finds_and_groups_monster_dots(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    for dx, dy in [(60, -40), (68, -34), (63, -48)]:      # yellow pack up-right
        _dot(frame, dx, dy)
    _dot(frame, -90, 50, colour=(40, 40, 230))            # red elite dot down-left
    packs = Vision(config).observe(frame).minimap.packs
    assert len(packs) == 2
    yellow = [p for p in packs if not p.elite][0]
    elite = [p for p in packs if p.elite][0]
    assert yellow.dots >= 2 and 65 < yellow.distance < 85   # touching dots merge into one blob
    assert -60 < math.degrees(yellow.angle) < -20
    assert 95 < elite.distance < 110


def test_hunt_walks_toward_far_pack_and_fights_near_pack(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt", fight_radius_px=28.0, attack_range_px=28.0, linger_s=0.5))
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(4))

    far = _frame(world, np.array([800.0, 700.0]))
    _dot(far, 0, -80)                                     # pack straight up, far away
    clicks = []
    for _ in range(40):
        clock.now += 0.05
        decision = brain.decide(vision.observe(far), True)
        assert decision.state == "ROAM"                   # walking, no attacks into empty air
        assert not any(a.kind == "press" and a.key == "mouse_right" for a in decision.actions)
        clicks += [a for a in decision.actions if a.kind == "click"]
    assert clicks and all(c.y < H * 0.48 for c in clicks)  # steering toward the pack (up)

    near = _frame(world, np.array([800.0, 700.0]))
    _dot(near, 20, 0)                                     # pack right next to the player
    clock.now += 0.05
    decision = brain.decide(vision.observe(near), True)
    assert decision.state == "FIGHT"
    aim = [a for a in decision.actions if a.kind == "aim"][0]
    assert aim.x > W * 0.5 and abs(aim.y - H * 0.48) < 40  # aiming right, toward the dot
    assert ("press", "mouse_right") in [(a.kind, a.key) for a in decision.actions]

    # Pack gone: keeps swinging for linger_s, then walks on.
    empty = _frame(world, np.array([800.0, 700.0]))
    clock.now += 0.2
    assert brain.decide(vision.observe(empty), True).state == "FIGHT"
    clock.now += 0.6
    assert brain.decide(vision.observe(empty), True).state == "ROAM"


def test_hunt_ignores_a_dot_that_never_dies(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt", max_fight_s=3.0, ignore_s=30.0, attack_range_px=40.0))
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(4))
    icon = _frame(world, np.array([800.0, 700.0]))
    _dot(icon, 18, 0)                                     # a static yellow map icon next to us
    states = []
    for _ in range(100):                                  # 5s
        clock.now += 0.05
        states.append(brain.decide(vision.observe(icon), True).state)
    assert states[0] == "FIGHT"
    assert states[-1] == "ROAM"                           # gave up after max_fight_s, walks on
    assert states.count("FIGHT") <= 3.0 / 0.05 + 2


def test_yellow_marker_behind_player_arrow_is_not_a_monster(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    x, y, w, h = MINIMAP.pixels(W, H)
    cx, cy = x + w // 2, y + h // 2
    star = np.array([[cx - 18, cy - 4], [cx - 9, cy - 9], [cx - 6, cy - 18], [cx - 3, cy - 8], [cx - 10, cy - 2]], np.int32)
    cv2.fillPoly(frame, [star], (30, 190, 245))           # yellow star touching the arrow
    cv2.circle(frame, (cx, cy), 7, (245, 245, 245), -1)   # arrow drawn on top, like the game
    _dot(frame, 60, 40)                                   # one real monster dot
    packs = Vision(config).observe(frame).minimap.packs
    assert len(packs) == 1 and packs[0].distance > 60


def _star(frame: np.ndarray, dx: int, dy: int) -> None:
    """Draw a yellow minimap loot star (legendary drop) at an offset from the centre."""
    from d4bot.minimap import STAR

    x, y, w, h = MINIMAP.pixels(W, H)
    cx, cy = x + w // 2 + dx, y + h // 2 + dy
    contours, _ = cv2.findContours(STAR, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    shifted = contours[0] + np.array([cx - STAR.shape[1] // 2, cy - STAR.shape[0] // 2])
    cv2.drawContours(frame, [shifted], -1, (30, 185, 245), -1)
    cv2.drawContours(frame, [shifted], -1, (25, 25, 25), 1)


def _label(frame: np.ndarray, x: int, y: int) -> None:
    """Orange item-name label on a dark box, like 'Volatile Primordial Dust'."""
    cv2.rectangle(frame, (x - 10, y - 18), (x + 300, y + 10), (40, 45, 50), -1)
    cv2.putText(frame, "Volatile Primordial Dust", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (40, 165, 235), 2)


def test_red_label_is_loot_and_zone_banner_is_not(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    # Red "Amulet" (live run 2: missed; ~97px wide at 1440p).
    cv2.rectangle(frame, (892, 392), (1020, 428), (25, 22, 20), -1)
    cv2.putText(frame, "Amulet", (900, 420), cv2.FONT_HERSHEY_TRIPLEX, 0.85, (40, 30, 225), 2, cv2.LINE_AA)
    # Zone-name banner, centred at y 799, 19px tall (live run 1: clicked 8x).
    cv2.rectangle(frame, (1110, 786), (1450, 812), (25, 22, 20), -1)
    cv2.putText(frame, "KAREKA FLOODPLAIN", (1120, 806), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (225, 225, 225), 2)
    labels = Vision(config).observe(frame).loot_labels
    assert any(abs(x - 900) < 20 and abs(y - 400) < 20 for x, y, _, _ in labels)
    assert not any(abs(y + h / 2 - 799) < 10 for _, y, _, h in labels)


def test_star_is_loot_not_a_monster_pack(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    _star(frame, 40, -30)
    _dot(frame, -60, 40)
    mm = Vision(config).observe(frame).minimap
    assert len(mm.loot) == 1 and abs(mm.loot[0][0] - 40) <= 2 and abs(mm.loot[0][1] + 30) <= 2
    assert len(mm.packs) == 1 and mm.packs[0].dx < 0      # the star is not a pack


def test_hunt_walks_to_loot_then_clicks_its_label(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(6))

    far = _frame(world, np.array([800.0, 700.0]))
    _star(far, 0, -90)                                    # loot straight up
    _dot(far, 90, 0)                                      # a pack to the right, same distance
    clicks = []
    for _ in range(30):
        clock.now += 0.05
        decision = brain.decide(vision.observe(far), True)
        clicks += [a for a in decision.actions if a.kind == "click"]
    assert clicks and all(c.y < H * 0.48 for c in clicks)  # loot beats the pack: walks up

    near = _frame(world, np.array([800.0, 700.0]))
    _star(near, -8, 20)
    _label(near, 760, 1035)
    clock.now += 0.05
    decision = brain.decide(vision.observe(near), True)
    assert decision.state == "LOOT"
    click = [a for a in decision.actions if a.kind == "click"][0]
    assert 760 <= click.x <= 1060 and 1010 <= click.y <= 1045


def test_human_form_keeps_hunting_and_retries_toggle(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt"),
        form=FormConfig(
            enabled=True, mode="glow", act_while_human=True, retry_interval_s=2.0,
            fallback_cooldown_s=1.0, confirm_frames=1, buff_off_confirm_s=0.0,
            glow_slots=(Region(0.4031, 0.9063, 0.0297, 0.0521),),
        ),
    )
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(8))
    frame = _frame(world, np.array([800.0, 700.0]))      # no burning frames = human
    _dot(frame, 20, 0)                                    # a pack right next to us
    presses, attacks = 0, 0
    for _ in range(100):                                  # 5s
        clock.now += 0.05
        decision = brain.decide(vision.observe(frame), True)
        presses += sum(1 for a in decision.actions if a.kind == "press" and a.key == "2")
        attacks += sum(1 for a in decision.actions if a.kind == "press" and a.key == "mouse_right")
    assert 2 <= presses <= 3                              # retried every ~2s, not spammed
    assert attacks > 10                                   # still fighting while human


def test_hunt_walks_in_before_attacking(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig

    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt", fight_radius_px=40.0, attack_range_px=14.0, approach_timeout_s=2.5),
    )
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(9))
    frame = _frame(world, np.array([800.0, 700.0]))
    _dot(frame, 32, 0)                                    # engaged (<40) but out of reach (>14)

    clock.now += 0.05
    decision = brain.decide(vision.observe(frame), True)
    assert decision.state == "APPROACH"
    assert not any(a.kind == "press" and a.key == "mouse_right" for a in decision.actions)
    click = [a for a in decision.actions if a.kind == "click"][0]
    assert W * 0.5 < click.x < W * 0.5 + 32 * 7.5        # toward the pack, short of it
    assert abs(click.y - H * 0.48) < 30

    # Pack never gets closer (wall): after approach_timeout_s it attacks from here.
    states = []
    for _ in range(60):
        clock.now += 0.05
        states.append(brain.decide(vision.observe(frame), True).state)
    assert states[0] == "APPROACH" and states[-1] == "FIGHT"

    # Once in reach it attacks straight away.
    brain2 = CombatBrain(config, rng=random.Random(9))
    close = _frame(world, np.array([800.0, 700.0]))
    _dot(close, 13, 0)
    clock.now += 0.05
    assert brain2.decide(Vision(config).observe(close), True).state == "FIGHT"


def _record_maze_route(folder: Path, points: list[tuple[float, float]], name: str = "maze") -> None:
    from d4bot.routes import RouteRecorder

    world, _ = _maze()
    x, y, w, h = MINIMAP.pixels(W, H)
    recorder = RouteRecorder(NavConfig())
    for p in points:
        recorder.add(_frame(world, np.array(p, dtype=np.float64))[y : y + h, x : x + w])
    recorder.save(folder, name)


def test_recording_that_starts_in_another_routes_zone_is_caught(tmp_path: Path) -> None:
    from d4bot.routes import RouteRecorder, load_routes, same_zone_as

    _record_maze_route(tmp_path, [(320 + i * 4.0, 300.0) for i in range(120)], name="zone_a")
    world, _ = _maze()
    x, y, w, h = MINIMAP.pixels(W, H)

    def start_of(world: np.ndarray, walk: list[tuple[float, float]]) -> np.ndarray:
        recorder = RouteRecorder(NavConfig())
        for p in walk:
            recorder.add(_frame(world, np.array(p, dtype=np.float64))[y : y + h, x : x + w])
        return recorder.start_patch()

    routes = load_routes(tmp_path)
    # Same waypoint, different walk, saved under another name -> it is zone_a.
    same = start_of(world, [(320.0, 300 + i * 4.0) for i in range(60)])
    assert same_zone_as(same, routes, "zone_b") == ("zone_a", pytest.approx(1.0, abs=0.15))
    # Re-recording zone_a under its own name is fine.
    assert same_zone_as(same, routes, "zone_a") is None
    # A different zone (mirrored world) is not mistaken for zone_a.
    other = np.ascontiguousarray(world[::-1, ::-1])
    assert same_zone_as(start_of(other, [(1280.0, 1100 - i * 4.0) for i in range(60)]), routes, "zone_b") is None


def test_route_recording_localizes_anywhere_on_it(tmp_path: Path) -> None:
    from d4bot.routes import RouteMap, terrain_gray

    walk = [(320 + i * 4.0, 300.0) for i in range(230)] + [(1300.0, 300 + i * 4.0) for i in range(100)]
    _record_maze_route(tmp_path, walk)
    route = RouteMap.load(tmp_path / "maze")
    offset = np.array(walk[0]) - route.path[0]
    assert np.hypot(*(route.path[-1] + offset - np.array(walk[-1]))) < 10.0   # little drift
    world, _ = _maze()
    x, y, w, h = MINIMAP.pixels(W, H)
    for p in [(600.0, 300.0), (1100.0, 300.0), (1300.0, 600.0)]:
        fix = route.locate(terrain_gray(_frame(world, np.array(p))[y : y + h, x : x + w], NavConfig()), None)
        assert fix.valid and np.hypot(fix.x + offset[0] - p[0], fix.y + offset[1] - p[1]) < 6.0


def test_route_is_only_searched_for_right_after_landing(tmp_path: Path, clock: Clock) -> None:
    # Live path1: mid-farm look-alike terrain matched a recorded town (0.45-0.62),
    # as high as the real Kyovashad landing (0.59), so only the timing separates them.
    from d4bot.routes import RouteLocator, load_routes

    walk = [(320 + i * 4.0, 300.0) for i in range(230)]
    _record_maze_route(tmp_path / "routes", walk)
    world, _ = _maze()
    x, y, w, h = MINIMAP.pixels(W, H)
    crop = _frame(world, np.array([600.0, 300.0]))[y : y + h, x : x + w]
    late = RouteLocator(load_routes(tmp_path / "routes"), NavConfig())
    clock.now += 31.0
    assert not any(late.update(crop, (0.0, 0.0)).valid for _ in range(6))
    landed = RouteLocator(load_routes(tmp_path / "routes"), NavConfig())
    assert any(landed.update(crop, (0.0, 0.0)).valid for _ in range(6))


def test_route_fights_packs_on_the_way_but_clicks_no_labels(tmp_path: Path, clock: Clock) -> None:
    # User: fight on the way (or die walking). Live cycle 2: walking out of
    # Kyovashad it clicked NPC name plates as loot.
    from d4bot.config import CombatConfig, RoutesConfig

    walk = [(320 + i * 4.0, 300.0) for i in range(230)]
    _record_maze_route(tmp_path / "routes", walk)
    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt"),
        routes=RoutesConfig(enabled=True, folder="routes"),
    )
    world, _ = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(3))
    states = []
    for _ in range(6):
        clock.now += 0.05
        frame = _frame(world, np.array([600.0, 300.0]))
        _label(frame, 760, 1035)                             # a name plate near the player
        states.append(brain.decide(vision.observe(frame), True).state)
    assert brain.route_mode == "route" and "LOOT" not in states

    clock.now += 0.05
    frame = _frame(world, np.array([600.0, 300.0]))
    _dot(frame, 13, 0)                                       # a pack right next to the route
    assert brain.decide(vision.observe(frame), True).state == "FIGHT"


def test_bot_follows_recorded_route_then_stays_on_leash(tmp_path: Path, clock: Clock) -> None:
    from d4bot.config import CombatConfig, RoutesConfig

    walk = [(320 + i * 4.0, 300.0) for i in range(230)] + [(1300.0, 300 + i * 4.0) for i in range(100)]
    _record_maze_route(tmp_path / "routes", walk)
    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt", loot_enabled=False),
        routes=RoutesConfig(enabled=True, folder="routes", leash_px=80.0),
    )
    world, walls = _maze()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(11))
    pos = np.array([320.0, 300.0])
    target = None
    end = np.array(walk[-1])
    closest, after_end = 1e9, []
    for _ in range(int(70 / 0.05)):
        clock.now += 0.05
        obs = vision.observe(_frame(world, pos))
        decision = brain.decide(obs, foreground=True)
        for action in decision.actions:
            if action.kind == "click":
                sx = action.x - W * config.nav.player_x
                sy = (action.y - H * config.nav.player_y) / config.nav.y_scale
                target = pos + 400.0 * np.array([sx, sy]) / max(1e-6, math.hypot(sx, sy))
        pos = _walk(walls, pos, target, speed=3.0)
        closest = min(closest, float(np.hypot(*(pos - end))))
        if brain.route_mode in {"roam", "leash"}:
            after_end.append(float(np.hypot(*(pos - end))))
    assert closest < 40.0, closest                         # walked the route to its end
    assert after_end and max(after_end[-200:]) < 80.0 + 120.0  # then stayed around the end


def test_big_red_map_icon_is_not_an_elite(tmp_path: Path, clock: Clock) -> None:
    """Live: the bot got stuck attacking the red sword marker on the minimap."""
    from d4bot.config import CombatConfig

    config = _config(tmp_path, combat=CombatConfig(mode="hunt"))
    world, _ = _maze()
    frame = _frame(world, np.array([800.0, 700.0]))
    x, y, w, h = MINIMAP.pixels(W, H)
    cx, cy = x + w // 2, y + h // 2
    cv2.rectangle(frame, (cx - 30, cy - 40), (cx - 22, cy - 10), (40, 40, 230), -1)   # sword blade
    cv2.rectangle(frame, (cx - 36, cy - 18), (cx - 16, cy - 14), (40, 40, 230), -1)   # crossguard
    _dot(frame, 60, 40, colour=(40, 40, 230))                                       # a real elite dot
    packs = Vision(config).observe(frame).minimap.packs
    assert len(packs) == 1 and packs[0].elite and packs[0].dx > 40


def _walled_world() -> tuple[np.ndarray, np.ndarray]:
    """Open ground split by a wall; the only way through is a gap to the east."""
    world = np.zeros((900, 1100, 3), dtype=np.uint8)
    world[:] = BLOCKED
    cv2.rectangle(world, (100, 100), (950, 750), OPEN, -1)
    cv2.rectangle(world, (100, 390), (430, 425), BLOCKED, -1)
    walls = np.all(world == BLOCKED, axis=2)
    rng = np.random.default_rng(2)
    grain = cv2.GaussianBlur(rng.normal(0, 60, world.shape[:2]).astype(np.float32), (0, 0), 1.5)
    world = np.clip(world.astype(np.float32) + grain[:, :, None], 0, 255).astype(np.uint8)
    return world, walls


def test_path_field_routes_around_a_wall() -> None:
    from d4bot.minimap import PathField

    mask = np.full((200, 260), 255, dtype=np.uint8)
    mask[60:80, :170] = 0                                 # wall above the player, gap on the right
    field = PathField(mask, cell=4)
    found = field.route(0.0, -70.0, start_px=14)          # target straight up, behind the wall
    assert found is not None
    (wx, wy), length = found
    assert wx > 30                                        # first heads right, toward the gap
    assert length > 120                                   # longer than the 70px straight line
    sealed = mask.copy()
    sealed[60:80, :] = 0                                  # no gap at all
    assert PathField(sealed, cell=4).route(0.0, -70.0, start_px=14) is None


def _hunt_behind_wall(tmp_path: Path, clock: Clock, planning: bool) -> tuple[list[str], int]:
    from d4bot.config import CombatConfig

    config = _config(
        tmp_path,
        combat=CombatConfig(mode="hunt", fight_radius_px=40.0, attack_range_px=40.0, loot_enabled=False),
        nav=NavConfig(noise=0.02, path_planning=planning),
    )
    world, walls = _walled_world()
    vision = Vision(config)
    brain = CombatBrain(config, rng=random.Random(5))
    pos = np.array([300.0, 480.0])
    pack = np.array([300.0, 345.0])                       # 135px north, behind the wall
    x, y, w, h = MINIMAP.pixels(W, H)
    target = None
    states = []
    dt = 0.05
    for _ in range(int(25.0 / dt)):
        clock.now += dt
        frame = _frame(world, pos)
        dx, dy = pack - pos
        if abs(dx) < w / 2 - 6 and abs(dy) < h / 2 - 6:
            _dot(frame, int(round(dx)), int(round(dy)))
        decision = brain.decide(vision.observe(frame), foreground=True)
        states.append(decision.state)
        if decision.state == "FIGHT":
            break
        for action in decision.actions:
            if action.kind == "click":
                sx = action.x - W * config.nav.player_x
                sy = (action.y - H * config.nav.player_y) / config.nav.y_scale
                angle = math.atan2(sy, sx)
                target = pos + 400.0 * np.array([math.cos(angle), math.sin(angle)])
        pos = _walk(walls, pos, target, speed=3.0)
    return states, brain.stuck_count


def test_hunt_walks_around_a_wall_to_reach_a_pack(tmp_path: Path, clock: Clock) -> None:
    """qqt_diablo-style path planning: go through the gap instead of into the wall."""
    states, stuck = _hunt_behind_wall(tmp_path, clock, planning=True)
    assert states[-1] == "FIGHT", (len(states), stuck)
    assert len(states) * 0.05 < 10.0                      # without planning: ~18s and a stuck event


def test_long_route_loops_its_circuit_not_back_to_town() -> None:
    # User: one long route - waypoint, out into the Helltide, then a circuit.
    from d4bot.routes import find_loop_start

    lead = [(i * 6.0, 0.0) for i in range(30)]                        # town -> open area
    circuit = [(180 + 100 * math.sin(t / 80 * 2 * math.pi), 100 - 100 * math.cos(t / 80 * 2 * math.pi))
               for t in range(81)]                                    # one lap, back to (180, 0)
    start = find_loop_start(np.array(lead + circuit))
    assert start is not None and 20 <= start <= 31                  # where the circuit begins
    assert find_loop_start(np.array(lead)) is None                    # one-way route
    there_and_back = lead + lead[::-1]                                # ends at the town start
    assert find_loop_start(np.array(there_and_back)) == 0
