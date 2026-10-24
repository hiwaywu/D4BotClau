"""Interactive calibration tools."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import cv2
import yaml

from .capture import ScreenCapture
from .config import BotConfig, Region, load_config, save_form, save_regions
from .vision import Vision, fit_for_display, measure_form_metric

log = logging.getLogger(__name__)

REGION_ORDER = ("health_globe", "resource_globe", "skill_bar", "enemy_roi", "center", "minimap", "buff_bar")


def _frame(config: BotConfig):  # type: ignore[no-untyped-def]
    capture = ScreenCapture(config.capture)
    try:
        frame = capture.grab()
    finally:
        capture.close()
    if frame.window is None:
        print(f"WARNING: window {config.capture.window_title!r} not found; using monitor capture.")
    else:
        print(f"Captured {frame.window.title!r}: {frame.image.shape[1]}x{frame.image.shape[0]}")
    return frame


def _select_roi(frame, label: str) -> tuple[int, int, int, int]:  # type: ignore[no-untyped-def]
    display, scale = fit_for_display(frame, 1500, 850)
    print(f"\n[{label}] Drag a tight box, then press ENTER. Press c to cancel.")
    time.sleep(0.4)
    x, y, w, h = cv2.selectROI(label, display, showCrosshair=True, fromCenter=False)
    cv2.destroyWindow(label)
    if w < 3 or h < 3:
        raise SystemExit(f"cancelled while selecting {label}")
    return (
        int(round(x / scale)),
        int(round(y / scale)),
        int(round(w / scale)),
        int(round(h / scale)),
    )


def _to_region(roi: tuple[int, int, int, int], width: int, height: int) -> Region:
    x, y, w, h = roi
    x = max(0, min(x, width - 1))
    y = max(0, min(y, height - 1))
    w = max(1, min(w, width - x))
    h = max(1, min(h, height - y))
    return Region(x / width, y / height, w / width, h / height)


def calibrate_regions(config_path: str | Path, only: str | None = None) -> None:
    config = load_config(config_path)
    frame = _frame(config)
    height, width = frame.image.shape[:2]
    if only is not None and only not in REGION_ORDER:
        raise SystemExit(f"unknown region {only!r}; choose one of: {', '.join(REGION_ORDER)}")
    names = (only,) if only else REGION_ORDER
    print(
        "\nDraw the requested region(s). Keep each box tight; the HUD element should "
        "fill most of the box, with as little background as possible."
    )
    selected: dict[str, Region] = {}
    for name in names:
        roi = _select_roi(frame.image, f"d4bot: {name}")
        selected[name] = _to_region(roi, width, height)
        print(f"  {name}: {selected[name].as_list()}")
    save_regions(config_path, selected)
    print(f"\nSaved regions to {config_path}")
    print("Next: .\\run.cmd -Debug -MaxSeconds 10   (dry run, no input)")


def calibrate_skill(config_path: str | Path, skill_name: str) -> None:
    config = load_config(config_path)
    frame = _frame(config)
    matching = [skill for skill in config.skills if skill.name == skill_name]
    if not matching:
        names = ", ".join(skill.name for skill in config.skills)
        raise SystemExit(f"unknown skill {skill_name!r}; configured skills: {names}")
    print(
        f"\nMake sure {skill_name} is OFF COOLDOWN and its icon is fully visible. "
        "Drag a tight box around the icon."
    )
    x, y, w, h = _select_roi(frame.image, f"d4bot: {skill_name} icon")
    crop = frame.image[y : y + h, x : x + w]
    if crop.size == 0:
        raise SystemExit("empty crop; nothing saved")

    out_dir = config.project_root / "templates" / "skills"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{skill_name}_ready.png"
    if not cv2.imwrite(str(out_path), crop):
        raise SystemExit(f"failed to write {out_path}")

    relative = out_path.relative_to(config.project_root).as_posix()
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8")) or {}
    for item in raw.get("skills", []):
        if isinstance(item, dict) and item.get("name") == skill_name:
            item["template"] = relative
            break
    config.config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    print(f"\nSaved {out_path} ({w}x{h})")
    print(f"Updated {relative} in {config.config_path.name}")
    print("Tip: capture at the same resolution you will run the bot at.")


def calibrate_form(config_path: str | Path) -> None:
    """Two-shot calibration: human metric, then demon metric, midpoint threshold."""
    config = load_config(config_path)
    frame = _frame(config)
    if config.form.roi is not None:
        region = config.form.roi
        print(f"\nUsing configured form ROI: {region.as_list()} (no drawing needed).")
    else:
        print(
            "\nSelect a region that visibly changes between human and demon form.\n"
            "Best default: the LAST (rightmost) skill icon, with a strong orange glow.\n"
            "Alternative: a tight box around your character's torso if the bar glow is unreliable."
        )
        x, y, w, h = _select_roi(frame.image, "d4bot: form state region")
        region = _to_region((x, y, w, h), frame.image.shape[1], frame.image.shape[0])
    metric = config.form.metric
    print(f"Using metric: {metric}")

    try:
        input("\nHUMAN FORM: stand still in human form, then press Enter here...")
        time.sleep(0.6)
        human_frame = _frame(config)
        human = measure_form_metric(human_frame.image, region, metric)
        print(f"  human metric: {human:.3f}")

        input("\nDEMON FORM: press your toggle in game, wait until the transformation is complete, then press Enter here...")
        time.sleep(0.6)
        demon_frame = _frame(config)
        demon = measure_form_metric(demon_frame.image, region, metric)
        print(f"  demon metric: {demon:.3f}")
    except EOFError:
        raise SystemExit("cancelled (no console input)")

    if demon > human:
        print("\nDirection: higher metric = demon form.")
    else:
        print("\nDirection: lower metric = demon form (the detector will invert the comparison).")
    if abs(demon - human) < 0.05:
        print(
            "WARNING: the two forms are very close. Recalibrate with a brighter icon/box "
            "or use metric: brightness; otherwise the form state may flicker."
        )

    threshold = save_form(config_path, region, human, demon, metric)
    print(f"\nSaved form ROI and threshold {threshold:.3f} to {config_path}")
    print("Now run: .\\run.cmd -Debug   (dry run) and watch state = DEMON / HUMAN / FORM?")
    print("Only enable input after the state changes reliably when you transform.")


def sample_globes(config_path: str | Path) -> None:
    config = load_config(config_path)
    frame = _frame(config)
    vision = Vision(config)
    obs = vision.observe(frame.image)
    print("\nCurrent readings:")
    print(f"  health:   level={obs.health.level:.3f} ratio={obs.health.mask_ratio:.4f} confidence={obs.health.confidence:.2f}")
    print(f"  resource: level={obs.resource.level:.3f} ratio={obs.resource.mask_ratio:.4f} confidence={obs.resource.confidence:.2f}")
    print(f"  enemy bars: {len(obs.enemies)}")
    print(
        f"  form: demon={obs.form.demon} metric={obs.form.metric:.3f} "
        f"threshold={obs.form.threshold:.3f}"
    )
    for skill in config.skills:
        reading = obs.skills[skill.name]
        print(f"  {skill.name}: ready={reading.ready} score={reading.score}")
    print("\nIf you are at FULL health, set globe.health_full_ratio close to the health ratio above.")
    print("If your resource globe is full, set globe.resource_full_ratio close to the resource ratio above.")


def _save_template_setting(config: BotConfig, section: str, key: str, relative: str, extra: dict | None = None) -> None:
    raw = yaml.safe_load(config.config_path.read_text(encoding="utf-8")) or {}
    block = raw.setdefault(section, {})
    block[key] = relative
    block.update(extra or {})
    config.config_path.write_text(
        yaml.safe_dump(raw, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def calibrate_buff(config_path: str | Path) -> None:
    """Capture the demon-form buff icon. Box the TOP of the icon, above the stack number."""
    config = load_config(config_path)
    frame = _frame(config)
    print(
        "\nBe in DEMON form. Drag a box around the top part of the horned-skull buff icon "
        "(inside the green border, above the stack number)."
    )
    x, y, w, h = _select_roi(frame.image, "d4bot: demon buff icon")
    crop = frame.image[y : y + h, x : x + w]
    if crop.size == 0:
        raise SystemExit("empty crop; nothing saved")
    out_path = config.project_root / "templates" / "demon_buff.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), crop):
        raise SystemExit(f"failed to write {out_path}")
    relative = out_path.relative_to(config.project_root).as_posix()
    _save_template_setting(
        config, "form", "buff_template", relative, {"buff_template_height": int(frame.image.shape[0])}
    )
    print(f"\nSaved {out_path} ({w}x{h}) and set form.buff_template.")
    print("Check it: .\\run.cmd -Doctor  (demon buff score should be ~0.9+ in demon, <0.6 in human)")


def calibrate_death(config_path: str | Path) -> None:
    """Capture part of the death/revive UI as a template."""
    config = load_config(config_path)
    frame = _frame(config)
    print("\nYou should be DEAD with the revive buttons on screen. Box the revive panel/text.")
    x, y, w, h = _select_roi(frame.image, "d4bot: death screen")
    crop = frame.image[y : y + h, x : x + w]
    if crop.size == 0:
        raise SystemExit("empty crop; nothing saved")
    out_path = config.project_root / "templates" / "death.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(out_path), crop):
        raise SystemExit(f"failed to write {out_path}")
    relative = out_path.relative_to(config.project_root).as_posix()
    _save_template_setting(config, "death", "template", relative, {"enabled": True})
    print(f"\nSaved {out_path} ({w}x{h}) and set death.template.")
