from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

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
    load_config,
)
from d4bot.vision import Vision


def _config(tmp_path: Path, skills: tuple[SkillConfig, ...], **kwargs) -> BotConfig:
    defaults = dict(
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
        globe=GlobeConfig(
            health_full_ratio=0.32,
            resource_full_ratio=0.32,
            empty_ratio=0.01,
            smoothing=1.0,
        ),
        enemy=EnemyConfig(persistence_frames=1, track_radius_px=20),
        skills=skills,
        mode="assist",
        project_root=tmp_path,
        config_path=tmp_path / "config.yaml",
    )
    defaults.update(kwargs)
    return BotConfig(**defaults)


def _synthetic_frame(tmp_path: Path, with_icon: bool = True) -> tuple[np.ndarray, tuple[SkillConfig, ...]]:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (18, 18, 22)

    cv2.ellipse(frame, (102, 554), (51, 82), 0, 0, 360, (0, 0, 235), -1)
    cv2.ellipse(frame, (1178, 554), (51, 82), 0, 0, 360, (235, 90, 10), -1)

    cv2.rectangle(frame, (480, 210), (560, 216), (0, 0, 235), -1)
    cv2.rectangle(frame, (700, 300), (790, 307), (0, 40, 235), -1)
    cv2.rectangle(frame, (340, 400), (390, 405), (0, 90, 235), -1)

    skill_bar = (int(0.35 * 1280), int(0.80 * 720), int(0.30 * 1280), int(0.15 * 720))
    x, y, w, h = skill_bar
    cv2.rectangle(frame, (x, y), (x + w, y + h), (25, 25, 30), -1)
    if with_icon:
        ix, iy = x + 30, y + 30
        cv2.rectangle(frame, (ix, iy), (ix + 40, iy + 40), (30, 190, 240), -1)
        cv2.circle(frame, (ix + 20, iy + 20), 10, (240, 240, 240), -1)
        template = frame[iy : iy + 40, ix : ix + 40].copy()
        template_path = tmp_path / "templates" / "skills" / "skill_1_ready.png"
        template_path.parent.mkdir(parents=True, exist_ok=True)
        assert cv2.imwrite(str(template_path), template)

    skills = (
        SkillConfig(
            name="skill_1",
            key="1",
            priority=40,
            fallback_cooldown_s=0.0,
            template="templates/skills/skill_1_ready.png",
            threshold=0.10,
        ),
    )
    return frame, skills


def test_vision_finds_globes_enemies_and_ready_skill(tmp_path: Path) -> None:
    frame, skills = _synthetic_frame(tmp_path)
    vision = Vision(_config(tmp_path, skills))
    obs = vision.observe(frame)

    assert obs.health.level > 0.80, obs.health
    assert obs.resource.level > 0.80, obs.resource
    assert len(obs.enemies) >= 2, obs.enemies
    # Boxes must be frame-relative, not enemy-ROI-relative, or aiming is offset.
    assert all(enemy.bbox[0] >= int(0.20 * 1280) for enemy in obs.enemies), obs.enemies
    assert obs.skills["skill_1"].ready is True
    assert obs.skills["skill_1"].score is not None
    assert obs.skills["skill_1"].score < 0.10


def test_vision_reports_missing_template_as_unknown(tmp_path: Path) -> None:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    skills = (SkillConfig(name="skill_1", template="templates/skills/nope.png"),)
    vision = Vision(_config(tmp_path, skills))
    obs = vision.observe(frame)
    assert obs.skills["skill_1"].ready is None


def test_form_detector_requires_confirmation(tmp_path: Path) -> None:
    region = Region(0.80, 0.80, 0.10, 0.10)
    config = _config(
        tmp_path,
        (),
        form=FormConfig(
            enabled=True,
            roi=region,
            demon_threshold=0.50,
            hysteresis=0.02,
            confirm_frames=2,
        ),
    )
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = region.pixels(1280, 720)
    frame[y : y + h, x : x + w] = (0, 160, 255)  # orange
    vision = Vision(config)

    first = vision.observe(frame)
    assert first.form.demon is None
    second = vision.observe(frame)
    assert second.form.demon is True
    assert second.form.metric > 0.50

    frame[y : y + h, x : x + w] = (35, 35, 35)  # dim / human
    vision.observe(frame)
    human = vision.observe(frame)
    assert human.form.demon is False


def test_form_detector_can_invert_direction(tmp_path: Path) -> None:
    region = Region(0.80, 0.80, 0.10, 0.10)
    config = _config(
        tmp_path,
        (),
        form=FormConfig(
            enabled=True,
            roi=region,
            demon_threshold=0.50,
            higher_is_demon=False,
            hysteresis=0.02,
            confirm_frames=2,
        ),
    )
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    x, y, w, h = region.pixels(1280, 720)
    frame[y : y + h, x : x + w] = (0, 160, 255)  # bright = human in this variant
    vision = Vision(config)

    vision.observe(frame)
    bright = vision.observe(frame)
    assert bright.form.demon is False

    frame[y : y + h, x : x + w] = (35, 35, 35)  # dim = demon
    vision.observe(frame)
    dim = vision.observe(frame)
    assert dim.form.demon is True


def test_default_config_loads() -> None:
    config_path = Path(__file__).resolve().parents[1] / "config.yaml"
    config = load_config(config_path)
    assert config.mode in {"assist", "roam"}
    assert len(config.skills) == 6
    assert config.regions["health_globe"].w > 0
    assert config.form.enabled is True
    assert config.form.toggle_key == "2"
    assert config.form.toggle_skill == "skill_2"
