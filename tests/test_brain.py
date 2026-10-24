from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path

import numpy as np

from d4bot.brain import CombatBrain
from d4bot.config import (
    BotConfig,
    CaptureConfig,
    EnemyConfig,
    FormConfig,
    GlobeConfig,
    InputConfig,
    Region,
    RoamConfig,
    SkillConfig,
)
from d4bot.vision import Enemy, FormReading, GlobeReading, Observation, SkillReading


def _config(tmp_path: Path, mode: str = "assist") -> BotConfig:
    return BotConfig(
        capture=CaptureConfig(),
        input=InputConfig(potion_threshold=0.70, potion_interval_s=1.0),
        roam=RoamConfig(leg_interval_s=0.0, min_click_interval_s=0.0, click_distance_px=300),
        regions={
            "health_globe": Region(0.02, 0.60, 0.12, 0.35),
            "resource_globe": Region(0.86, 0.60, 0.12, 0.35),
            "skill_bar": Region(0.35, 0.80, 0.30, 0.15),
            "enemy_roi": Region(0.20, 0.10, 0.60, 0.60),
            "center": Region(0.40, 0.35, 0.20, 0.20),
        },
        globe=GlobeConfig(),
        enemy=EnemyConfig(),
        skills=(
            SkillConfig(name="skill_1", key="1", priority=40, min_interval_s=10.0),
            SkillConfig(name="skill_2", key="2", priority=30, min_interval_s=10.0),
        ),
        mode=mode,
        project_root=tmp_path,
        config_path=tmp_path / "config.yaml",
    )


def _obs(
    frame: np.ndarray,
    health: float,
    enemies: list[Enemy],
    form: FormReading | None = None,
    skill_2_ready: bool = True,
) -> Observation:
    return Observation(
        frame=frame,
        health=GlobeReading(level=health, mask_ratio=0.4, confidence=1.0),
        resource=GlobeReading(level=0.8, mask_ratio=0.4, confidence=1.0),
        enemies=enemies,
        skills={
            "skill_1": SkillReading(ready=True, score=0.0),
            "skill_2": SkillReading(ready=skill_2_ready, score=0.0 if skill_2_ready else 1.0),
        },
        dead=False,
        timestamp=0.0,
        width=frame.shape[1],
        height=frame.shape[0],
        form=form or FormReading(None, 0.0, 0.0, 0.0),
    )


def test_low_health_potions_and_attacks(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    enemy = Enemy(bbox=(600, 300, 80, 6), score=0.9, center=(640, 303))
    brain = CombatBrain(_config(tmp_path), rng=random.Random(1))
    decision = brain.decide(_obs(frame, 0.4, [enemy]), foreground=True)

    assert decision.state == "FIGHT"
    kinds = [(action.kind, action.key) for action in decision.actions]
    assert ("press", "q") in kinds
    assert ("press", "1") in kinds
    assert any(action.kind == "aim" for action in decision.actions)


def test_paused_when_not_foreground(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    brain = CombatBrain(_config(tmp_path), rng=random.Random(1))
    decision = brain.decide(_obs(frame, 1.0, []), foreground=False)
    assert decision.state == "PAUSED"
    assert decision.actions == []


def _form_config(tmp_path: Path) -> BotConfig:
    return BotConfig(
        capture=CaptureConfig(),
        input=InputConfig(),
        roam=RoamConfig(),
        regions={
            "health_globe": Region(0.02, 0.60, 0.12, 0.35),
            "resource_globe": Region(0.86, 0.60, 0.12, 0.35),
            "skill_bar": Region(0.35, 0.80, 0.30, 0.15),
            "enemy_roi": Region(0.20, 0.10, 0.60, 0.60),
            "center": Region(0.40, 0.35, 0.20, 0.20),
        },
        globe=GlobeConfig(),
        enemy=EnemyConfig(),
        skills=(
            SkillConfig(name="skill_1", key="1", priority=40),
            SkillConfig(name="skill_2", key="2", enabled=False, priority=0),
        ),
        mode="assist",
        project_root=tmp_path,
        config_path=tmp_path / "config.yaml",
        form=FormConfig(
            enabled=True,
            toggle_key="2",
            toggle_skill="skill_2",
            demon_threshold=0.60,
            post_toggle_grace_s=0.0,
            retry_interval_s=0.0,
        ),
    )


def test_form_gate_presses_toggle_only_in_human(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    enemy = Enemy(bbox=(600, 300, 80, 6), score=0.9, center=(640, 303))

    human = _obs(frame, 0.9, [], form=FormReading(False, 0.20, 0.60, 1.0))
    decision = CombatBrain(_form_config(tmp_path), rng=random.Random(1)).decide(human, foreground=True)
    assert decision.state == "TOGGLE"
    assert ("press", "2") in [(a.kind, a.key) for a in decision.actions]

    demon = _obs(frame, 0.9, [enemy], form=FormReading(True, 0.80, 0.60, 1.0))
    decision = CombatBrain(_form_config(tmp_path), rng=random.Random(1)).decide(demon, foreground=True)
    assert decision.state == "FIGHT"
    assert ("press", "2") not in [(a.kind, a.key) for a in decision.actions]

    unknown = _obs(frame, 0.9, [enemy], form=FormReading(None, 0.60, 0.60, 0.0))
    decision = CombatBrain(_form_config(tmp_path), rng=random.Random(1)).decide(unknown, foreground=True)
    assert decision.state == "FORM?"
    assert decision.actions == []


def test_form_gate_waits_for_toggle_cooldown(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    human = _obs(
        frame,
        0.9,
        [],
        form=FormReading(False, 0.20, 0.60, 1.0),
        skill_2_ready=False,
    )
    decision = CombatBrain(_form_config(tmp_path), rng=random.Random(1)).decide(human, foreground=True)
    assert decision.state == "HUMAN"
    assert decision.actions == []


def test_unknown_toggle_readiness_uses_fallback_cooldown(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    brain = CombatBrain(_form_config(tmp_path), rng=random.Random(1))
    first = _obs(
        frame,
        0.9,
        [],
        form=FormReading(False, 0.20, 0.60, 1.0),
        skill_2_ready=None,  # template missing
    )
    decision = brain.decide(first, foreground=True)
    assert decision.state == "TOGGLE"

    second = brain.decide(first, foreground=True)
    assert second.state == "HUMAN"
    assert ("press", "2") not in [(a.kind, a.key) for a in second.actions]


def test_cooldown_starts_when_demon_form_ends(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    brain = CombatBrain(_form_config(tmp_path), rng=random.Random(1))

    demon = _obs(frame, 0.9, [], form=FormReading(True, 0.80, 0.60, 1.0))
    brain.decide(demon, foreground=True)

    human = _obs(frame, 0.9, [], form=FormReading(False, 0.20, 0.60, 1.0))
    decision = brain.decide(human, foreground=True)
    assert decision.state == "HUMAN"
    assert ("press", "2") not in [(a.kind, a.key) for a in decision.actions]
    assert "cooldown" in decision.note


def test_toggle_state_assumes_human_and_detects_cooldown(tmp_path: Path) -> None:
    base = _form_config(tmp_path)
    config = replace(
        base,
        form=replace(
            base.form,
            mode="toggle_state",
            ready_threshold=0.25,
            assume_human_at_start=True,
            post_toggle_grace_s=0.0,
            retry_interval_s=0.0,
        ),
    )
    brain = CombatBrain(config, rng=random.Random(1))
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)

    ready = _obs(frame, 0.9, [], form=FormReading(True, 0.40, 0.25, 1.0))
    first = brain.decide(ready, foreground=True)
    assert first.state == "TOGGLE"
    assert ("press", "2") in [(a.kind, a.key) for a in first.actions]

    second = brain.decide(ready, foreground=True)
    assert ("press", "2") not in [(a.kind, a.key) for a in second.actions]

    dark = _obs(frame, 0.9, [], form=FormReading(False, 0.05, 0.25, 1.0))
    ended = brain.decide(dark, foreground=True)
    assert ended.state == "HUMAN"
    assert ("press", "2") not in [(a.kind, a.key) for a in ended.actions]
    assert "cooldown" in ended.note


def test_utility_and_attack_fire_in_same_tick(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    enemy = Enemy(bbox=(600, 300, 80, 6), score=0.9, center=(640, 303))
    config = replace(
        _form_config(tmp_path),
        skills=(
            SkillConfig(
                name="utility",
                key="1",
                enabled=True,
                utility=True,
                utility_interval_s=15.0,
                priority=0,
            ),
            SkillConfig(name="attack", key="mouse_right", enabled=True, priority=0),
        ),
    )
    brain = CombatBrain(config, rng=random.Random(1))
    obs = _obs(frame, 0.9, [enemy], form=FormReading(True, 0.80, 0.60, 1.0))
    decision = brain.decide(obs, foreground=True)
    keys = [(a.kind, a.key) for a in decision.actions]
    assert ("press", "1") in keys
    assert ("press", "mouse_right") in keys
    assert decision.state == "FIGHT"


def test_roam_bounces_off_right_edge(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    config = replace(
        _config(tmp_path, mode="roam"),
        roam=RoamConfig(
            leg_interval_s=0.0,
            min_click_interval_s=0.0,
            click_distance_px=1000,
        ),
    )
    brain = CombatBrain(config, rng=random.Random(4))
    obs = _obs(frame, 1.0, [])
    brain._heading = 0.0  # aim straight right, far enough to clamp
    first = brain._roam_click(obs, 0.0)
    assert first is not None
    # The edge bounce must turn the click back inward, not return x=2048.
    assert first.x is not None and first.x < int(1280 * 0.80) - 10


def test_roam_clicks_inside_play_area(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    brain = CombatBrain(_config(tmp_path, mode="roam"), rng=random.Random(2))
    decision = brain.decide(_obs(frame, 1.0, []), foreground=True)

    assert decision.state == "ROAM"
    clicks = [action for action in decision.actions if action.kind == "click"]
    assert len(clicks) == 1
    click = clicks[0]
    assert click.x is not None and click.y is not None
    assert 0.20 * 1280 <= click.x <= 0.80 * 1280
    assert 0.20 * 720 <= click.y <= 0.70 * 720
