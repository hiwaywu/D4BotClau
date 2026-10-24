"""CLI entry point."""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

import cv2

from .brain import CombatBrain
from .calibrate import (
    REGION_ORDER,
    calibrate_buff,
    calibrate_death,
    calibrate_form,
    calibrate_regions,
    calibrate_skill,
    sample_globes,
)
from .capture import FramePacer, ScreenCapture, find_game_window, foreground_title, is_foreground
from .config import BotConfig, load_config
from .controller import InputController, emergency_stop_pressed
from .overlay import DebugOverlay
from .vision import Vision

log = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
BANNER = r"""
  ____  _  _     _   _   _ _____ ___  ____
 |  _ \| || |   / | | | | |_   _/ _ \| __ )
 | | | | || |_  | | | |_| | | || | | |  _ \
 | |_| |__   _| | | |  _  | | || |_| | |_) |
 |____/   |_|   |_| |_| |_| |_| \___/|____/
 visual combat / Helltide roam assistant
"""


def _default_config() -> Path:
    cwd = Path.cwd() / "config.yaml"
    if cwd.exists():
        return cwd
    return PACKAGE_ROOT / "config.yaml"


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_dir = PACKAGE_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_dir / "session.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _print_config(config: BotConfig) -> None:
    print(f"config: {config.config_path}")
    print(f"mode: {config.mode}   capture: {config.capture.mode} @ {config.capture.fps:g} fps")


def cmd_doctor(config_path: str) -> int:
    config = load_config(config_path)
    print("== d4-visual-bot doctor ==")
    print(f"python: {sys.version.split()[0]}")
    print(f"opencv: {cv2.__version__}")
    _print_config(config)
    print("\nDependencies:")
    for module in ("cv2", "numpy", "mss", "yaml", "pydirectinput"):
        print(f"  {module:<14} {'ok' if importlib.util.find_spec(module) else 'MISSING'}")
    print()

    capture = ScreenCapture(config.capture)
    try:
        frame = capture.grab()
    finally:
        capture.close()
    height, width = frame.image.shape[:2]
    print(f"capture frame: {width}x{height}")
    if frame.window is None:
        print(f"window: NOT FOUND (looked for {config.capture.window_title!r})")
    else:
        print(f"window: {frame.window.title!r}  client={frame.window.width}x{frame.window.height}")
        print(f"foreground: {is_foreground(frame.window.hwnd)}  (current: {foreground_title()!r})")
        if not is_foreground(frame.window.hwnd):
            print("  -> click the game window before enabling input")

    vision = Vision(config)
    obs = vision.observe(frame.image)
    print(
        f"health: {obs.health.level:.2f} (ratio {obs.health.mask_ratio:.3f})   "
        f"resource: {obs.resource.level:.2f} (ratio {obs.resource.mask_ratio:.3f})   "
        f"enemy bars: {len(obs.enemies)}"
    )
    print(
        f"form: metric={obs.form.metric:.3f} threshold={obs.form.threshold:.3f} "
        f"state={obs.form.demon} (one frame; confirmation takes a few frames)"
    )
    if config.form.mode == "glow":
        scores = vision.glow_scores(frame.image)
        score = vision.form_score(frame.image) or 0.0
        verdict = "DEMON" if score >= config.form.glow_threshold else "HUMAN"
        print(
            f"demon glow: slots={' '.join(f'{s:.2f}' for s in scores)}  "
            f"threshold={config.form.glow_threshold:.2f} -> {verdict}"
        )
    elif config.form.mode == "buff":
        score = vision.buff_score(frame.image)
        if score is None:
            print(f"demon buff: template missing ({config.form.buff_template})")
        else:
            verdict = "DEMON" if score >= config.form.buff_threshold else "HUMAN"
            print(f"demon buff: score={score:.2f} threshold={config.form.buff_threshold:.2f} -> {verdict}")
    if config.nav.enabled:
        mm = obs.minimap
        if mm.valid:
            best = int(mm.clearance.argmax())
            print(
                f"minimap: ok  monster packs={len(mm.packs)}"
                f"{' (nearest ' + format(mm.packs[0].distance, '.0f') + 'px)' if mm.packs else ''}  "
                f"walkable={mm.walkable_ratio:.2f}  "
                f"open rays={int((mm.clearance >= config.nav.min_clearance).sum())}/{len(mm.clearance)}  "
                f"best={mm.angles[best] * 57.2958:.0f}deg"
            )
        else:
            print(f"minimap: NOT READABLE (walkable={mm.walkable_ratio:.2f}); roam falls back to random")
    if config.routes.enabled:
        from .routes import load_routes

        maps = load_routes(config.project_root / config.routes.folder)
        if not maps:
            print(f"routes: none recorded yet in {config.routes.folder}/ (record with -Record NAME)")
        elif obs.route.valid:
            print(f"routes: {len(maps)} loaded; located in {obs.route.name!r} (match {obs.route.score:.2f})")
        else:
            print(f"routes: {len(maps)} loaded ({', '.join(m.name for m in maps)}); not located here")
    if not config.detector.enabled:
        print("detector: off (YOLO; see docs/YOLO.md)")
    elif vision.detector is None:
        print(f"detector: ENABLED but {config.detector.model} could not be loaded (train one with -Train)")
    else:
        found = ", ".join(f"{d.label} {d.confidence:.2f}" for d in obs.detections[:8]) or "nothing"
        print(f"detector: {vision.detector.model_path.name} via {vision.detector.backend}; sees {found}")
    if config.death.enabled:
        hud = obs.screen.hud_score
        print(
            f"screen: HUD={'n/a' if hud is None else f'{hud:.2f}'} (missing < {config.death.hud_threshold:g})  "
            f"saturation={obs.screen.saturation:.1f}  brightness={obs.screen.brightness:.1f}  "
            f"black={obs.screen.black}"
        )
    print("\nSkill templates:")
    for name, status in vision.template_status().items():
        print(f"  {name}: {status}")
    print("\nIf these numbers look wrong, calibrate regions or set globe full ratios using `calibrate sample`.")
    return 0


def cmd_run(
    config_path: str,
    debug: bool,
    enable_input: bool,
    accept_risk: bool,
    max_seconds: float | None,
    start_form: str | None = None,
    snapshot_dir: str | Path | None = None,
    snapshot_every_s: float = 3.0,
) -> int:
    config = load_config(config_path)
    if start_form is not None and config.form.mode not in {"buff", "glow"}:
        config = replace(
            config,
            form=replace(config.form, assume_human_at_start=(start_form == "human")),
        )
    wants_input = enable_input
    if wants_input and not accept_risk:
        print(
            "REFUSING TO SEND INPUT.\n"
            "Botting violates Blizzard's terms and can permanently ban your account.\n"
            "If you still want to test it, run with BOTH:\n"
            "  -EnableInput -IAcceptBanRisk\n"
            "Without those flags this program runs read-only (dry-run)."
        )
        return 2

    input_enabled = wants_input and accept_risk
    print(BANNER)
    _print_config(config)
    print(f"input: {'ENABLED' if input_enabled else 'DRY-RUN (no keys or clicks will be sent)'}")
    if config.form.enabled and config.form.mode in {"buff", "glow"}:
        source = "burning skill frames" if config.form.mode == "glow" else "demon buff icon"
        print(f"form start: AUTO (reads the {source})")
        if start_form is not None:
            print(f"  note: --start {start_form} ignored; detected from the screen")
    elif config.form.enabled:
        print(f"form start: {'HUMAN (will press 2)' if config.form.assume_human_at_start else 'DEMON (will not press 2)'}")
    if config.mode == "roam":
        print(f"roam: {'minimap navigation' if config.nav.enabled else 'random legs'}")
    if config.death.enabled:
        print(
            f"death: HUD gone {config.death.confirm_s:g}s -> "
            f"{'stop the run' if config.death.action == 'stop' else 'pause'} (screenshot saved)"
        )
    print("Emergency stop: F8\n")

    _farm(config, input_enabled, debug, max_seconds, snapshot_dir, snapshot_every_s)
    return 0


def _farm(
    config: BotConfig,
    input_enabled: bool,
    debug: bool = False,
    max_seconds: float | None = None,
    snapshot_dir: str | Path | None = None,
    snapshot_every_s: float = 3.0,
    end_check=None,  # type: ignore[no-untyped-def]
    loot_hold_s: float = 0.0,
) -> str:
    """The farming loop. Returns why it ended.

    end_check(frame, obs, decision, now) -> reason or None is called about
    once a second; a reason ends the loop (used by the Helltide cycle).
    """
    capture = ScreenCapture(config.capture)
    vision = Vision(config)
    brain = CombatBrain(config, live=input_enabled)
    brain.loot_hold_until = time.monotonic() + loot_hold_s
    controller = InputController(config.input, enabled=input_enabled)
    overlay = DebugOverlay(config) if debug else None
    # Snapshots save the overlay to disk instead of a window, so nothing can
    # cover the game (the capture would otherwise see its own overlay).
    snapshots = DebugOverlay(config) if snapshot_dir is not None else None
    if snapshot_dir is not None:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        print(f"snapshots: every {snapshot_every_s:g}s -> {snapshot_dir}")
    last_snapshot = 0.0
    pacer = FramePacer(config.capture.fps)

    frames = 0
    fight_frames = 0
    actions_sent = 0
    potions = 0
    moves = 0
    errors = 0
    started = time.monotonic()
    timer_start: float | None = None
    last_stats = started
    last_state = ""
    death_saved = False
    exit_reason = "stopped"
    last_end_check = 0.0

    try:
        while True:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                exit_reason = "F8 emergency stop"
                break
            # The time limit starts when the game first has focus, so starting a
            # run and then clicking into the game does not eat the run.
            if max_seconds is not None:
                if timer_start is None and time.monotonic() - started >= 60.0:
                    exit_reason = "game window never got focus (60s)"
                    break
                if timer_start is not None and time.monotonic() - timer_start >= max_seconds:
                    exit_reason = f"max-seconds {max_seconds:g}"
                    break

            try:
                frame = capture.grab()
                obs = vision.observe(frame.image)
                foreground = bool(frame.window and is_foreground(frame.window.hwnd))
                if foreground and timer_start is None:
                    timer_start = time.monotonic()
                    log.info("Game window has focus; run timer started")
                decision = brain.decide(obs, foreground)
                sent = controller.execute(decision, frame.window, frame.region)
            except Exception:
                # One bad frame (window resized, capture hiccup) must not kill a
                # long run, but a persistent failure must not loop forever.
                errors += 1
                log.exception("Frame failed (%d consecutive)", errors)
                if errors >= 20:
                    exit_reason = "too many consecutive errors"
                    break
                time.sleep(0.25)
                continue
            errors = 0
            actions_sent += sent
            if decision.state in {"FIGHT", "PULSE"}:
                fight_frames += 1
            if any(action.kind == "press" and action.key == config.input.potion_key for action in decision.actions):
                potions += 1
            moves += sum(1 for action in decision.actions if action.kind == "click")
            frames += 1

            # FIGHT/TRACK alternate between right-clicks; log them as one state.
            shown_state = "FIGHT" if decision.state == "TRACK" else decision.state
            if shown_state != last_state:
                log.info("State %s -> %s  %s", last_state or "-", shown_state, decision.note)
                last_state = shown_state

            if decision.state == "DEAD":
                if config.death.save_screenshot and not death_saved:
                    _save_snapshot(frame.image, "death")
                    death_saved = True
                if config.death.action == "stop":
                    # Also fires on "NETWORK DISCONNECT" (soak 12) and menus: any of
                    # them means no more input should be sent.
                    exit_reason = "HUD gone 2s (death, disconnect or menu); see data/death_*.png"
                    break
            else:
                death_saved = False

            now = time.monotonic()
            if end_check is not None and now - last_end_check >= 1.0:
                last_end_check = now
                reason = end_check(frame.image, obs, decision, now)
                if reason:
                    exit_reason = reason
                    break
            if now - last_stats >= 30.0:
                log.info(
                    "Stats %.0fs: fight=%d actions=%d moves=%d potions=%d stuck=%d packs=%d loot_clicks=%d rot=%+.0fdeg",
                    now - started,
                    fight_frames,
                    actions_sent,
                    moves,
                    potions,
                    brain.stuck_count,
                    brain.packs_engaged,
                    brain.loot_clicks,
                    brain.rotation * 57.2958,
                )
                last_stats = now

            if snapshots is not None and now - last_snapshot >= snapshot_every_s:
                rendered = snapshots.render(obs, decision, input_enabled, brain)
                stamp = f"{now - started:05.1f}s_{decision.state}"
                cv2.imwrite(str(snapshot_dir / f"overlay_{stamp}.jpg"), rendered)
                # Raw frame too: the overlay's drawings confuse offline re-analysis.
                cv2.imwrite(str(snapshot_dir / f"raw_{stamp}.jpg"), obs.frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
                last_snapshot = now
            if overlay is not None:
                key = overlay.show(overlay.render(obs, decision, input_enabled, brain))
                if key in {"q", "\x1b"}:
                    exit_reason = "overlay quit"
                    break
            pacer.wait()
    except KeyboardInterrupt:
        exit_reason = "Ctrl+C"
    finally:
        controller.stop()
        capture.close()
        if overlay is not None:
            overlay.close()

    elapsed = max(0.001, time.monotonic() - started)
    summary = (
        f"{exit_reason}. {elapsed:.0f}s frames={frames} ({frames / elapsed:.1f}/s)  "
        f"fight={fight_frames}  actions={actions_sent}  moves={moves}  potions={potions}  "
        f"stuck={brain.stuck_count}"
    )
    log.info(summary)
    print("\n" + summary)
    return exit_reason




def _save_snapshot(image, label: str) -> None:  # type: ignore[no-untyped-def]
    out_dir = PACKAGE_ROOT / "data"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{label}_{time.strftime('%Y%m%d_%H%M%S')}.png"
        cv2.imwrite(str(path), image)
        log.info("Saved %s", path)
    except Exception:
        log.exception("Could not save %s snapshot", label)


def cmd_teach_stash(config_path: str) -> int:
    """Record the user's left clicks from the town-portal landing to the stash. No input sent."""
    import ctypes

    from .town import BASE_H, BASE_W, TownConfig, TownVision, save_stash_path

    config = load_config(config_path)
    capture = ScreenCapture(config.capture)
    town_config = TownConfig()
    vision = TownVision(config.project_root, town_config)
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    print("Teach the walk to the stash. Nothing is sent to the game.")
    print("  1. Click into the game and press T.")
    print("  2. After you land in town, LEFT-CLICK your way to the stash and click the chest.")
    print("  Recording stops when the stash opens. F8 cancels.\n")
    try:
        # Wait for the town portal: a loading screen, then the town.
        seen_loading = False
        while True:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                print("Cancelled.")
                return 1
            frame = capture.grab()
            if vision.loading(frame.image):
                if not seen_loading:
                    print("  loading...")
                seen_loading = True
            elif seen_loading and vision.in_town(frame.image):
                break
            time.sleep(0.05)
        print("  Landed in town. Recording your clicks now - walk to the stash.")
        clicks: list[dict] = []
        last_time = time.monotonic()
        was_down = True  # ignore a button already held
        while True:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                print("Cancelled; nothing saved.")
                return 1
            frame = capture.grab()
            if vision.stash_open(frame.image):
                break
            down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            if down and not was_down and frame.window and is_foreground(frame.window.hwnd):
                point = POINT()
                user32.GetCursorPos(ctypes.byref(point))
                x0, y0, w, h = frame.region
                x, y = point.x - x0, point.y - y0
                if 0 <= x < w and 0 <= y < h:
                    now = time.monotonic()
                    clicks.append({
                        "x": round(x * BASE_W / w, 1),
                        "y": round(y * BASE_H / h, 1),
                        "delay": round(now - last_time, 2),
                    })
                    last_time = now
                    print(f"  click {len(clicks)} at {x},{y}")
            was_down = down
            time.sleep(0.01)
    finally:
        capture.close()
    if not clicks:
        print("No clicks recorded; nothing saved.")
        return 1
    out = save_stash_path(config.project_root, town_config, clicks)
    print(f"\nStash open. Saved {len(clicks)} click(s) to {out}")
    return 0


class _RoutineIO:
    """Capture + input plumbing shared by the town and Helltide routines.

    Every action goes through the InputController (F8 and foreground gates);
    the routine is told to stop on F8 or when the game loses focus.
    """

    def __init__(self, config: BotConfig, label: str):
        from .brain import Decision

        self._decision = Decision
        self.config = config
        self.label = label
        self.capture = ScreenCapture(config.capture)
        self.controller = InputController(config.input, enabled=True)
        self.last: dict = {}
        self.debug_dir = PACKAGE_ROOT / "data" / "town_runs" / time.strftime("%Y%m%d_%H%M%S")
        self.debug_dir.mkdir(parents=True, exist_ok=True)

    def grab(self):  # type: ignore[no-untyped-def]
        frame = self.capture.grab()
        self.last["frame"] = frame
        return frame.image

    def send(self, actions) -> None:  # type: ignore[no-untyped-def]
        frame = self.last.get("frame") or self.capture.grab()
        self.controller.execute(self._decision(actions=list(actions), state=self.label), frame.window, frame.region)

    def stopped(self) -> bool:
        if emergency_stop_pressed(self.config.input.emergency_stop_key):
            if not self.controller.stopped:
                log.warning("%s: F8 pressed; stopping", self.label)
            self.controller.stopped = True
        frame = self.last.get("frame")
        unfocused = frame is not None and not (frame.window and is_foreground(frame.window.hwnd))
        if unfocused:
            log.warning("%s: stopping because the game window lost focus (foreground: %r)", self.label, foreground_title())
        return self.controller.stopped or unfocused

    def debug(self, image, label: str) -> None:  # type: ignore[no-untyped-def]
        cv2.imwrite(str(self.debug_dir / f"{label}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 90])

    def wait_for_focus(self, timeout: float = 30.0) -> bool:
        print("Click into the game now; waiting for it to have focus (up to 30s)...")
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            frame = self.capture.grab()
            if frame.window and is_foreground(frame.window.hwnd):
                time.sleep(1.0)  # keys right after focus can be dropped
                print(f"Step screenshots: {self.debug_dir}")
                return True
            time.sleep(0.2)
        print("The game window never got focus; nothing was sent.")
        return False

    def close(self) -> None:
        self.controller.stop()
        self.capture.close()


def _run_town(io: _RoutineIO) -> bool:
    from .town import TownConfig, TownRunner, TownVision, load_stash_path

    town_config = TownConfig()
    vision = TownVision(io.config.project_root, town_config)
    runner = TownRunner(vision, town_config, grab=io.grab, send=io.send, stopped=io.stopped, debug=io.debug)
    runner.path = load_stash_path(io.config.project_root, town_config)
    result = runner.run()
    status = "OK" if result.ok else f"FAILED: {result.reason}"
    log.info("Town routine %s  moved=%s  left=%s", status, result.moved, result.left)
    print(f"Town routine {status}")
    print(f"  moved: {result.moved}")
    if result.left:
        print(f"  could not stash: {result.left}")
    return result.ok


def _run_helltide_teleport(io: _RoutineIO, want_reason: bool = False):  # type: ignore[no-untyped-def]
    from .warplan import WarplanConfig, WarplanRunner, WarplanVision

    warplan_config = WarplanConfig()
    vision = WarplanVision(io.config.project_root, warplan_config)
    result = WarplanRunner(vision, warplan_config, grab=io.grab, send=io.send, stopped=io.stopped, debug=io.debug).run()
    status = "OK" if result.ok else f"FAILED: {result.reason}"
    log.info("Helltide teleport %s", status)
    print(f"Helltide teleport {status}")
    return result if want_reason else result.ok


def _routine_command(config_path: str, enable_input: bool, accept_risk: bool, steps, title: str) -> int:  # type: ignore[no-untyped-def]
    if not (enable_input and accept_risk):
        print("This routine sends input. Run with BOTH -EnableInput -IAcceptBanRisk.")
        return 2
    io = _RoutineIO(load_config(config_path), "TOWN")
    print(f"{title}  F8 stops.")
    try:
        if not io.wait_for_focus():
            return 1
        for step in steps:
            if not step(io):
                return 1
        return 0
    finally:
        io.close()


def cmd_town(config_path: str, enable_input: bool, accept_risk: bool) -> int:
    """Town portal, open the stash, move the whole inventory into it."""
    return _routine_command(config_path, enable_input, accept_risk, [_run_town], "Town routine: T -> town -> stash -> move inventory.")


def cmd_cycle(
    config_path: str,
    enable_input: bool,
    accept_risk: bool,
    max_hours: float,
    stash_every_min: float,
    idle_reset_min: float = 4.0,
) -> int:
    """Town + stash -> Warplan teleport -> farm until the Helltide ends -> repeat."""
    from .warplan import WarplanConfig, WarplanVision

    if not (enable_input and accept_risk):
        print("The cycle sends input. Run with BOTH -EnableInput -IAcceptBanRisk.")
        return 2
    config = load_config(config_path)
    helltide = WarplanVision(config.project_root, WarplanConfig())
    io = _RoutineIO(config, "CYCLE")
    print("Helltide cycle: town + stash -> teleport -> farm until it ends -> repeat. F8 stops.")
    started = time.monotonic()
    cycles = 0

    def f8() -> bool:
        return io.controller.stopped or emergency_stop_pressed(config.input.emergency_stop_key)

    def focused() -> bool:
        frame = io.capture.grab()
        return bool(frame.window and is_foreground(frame.window.hwnd))

    def pause_for_focus() -> bool:
        """The game lost focus mid-step: wait for the user to click back in."""
        if focused():
            return True
        print("Paused: the game window is not in front. Click into the game to continue (F8 stops).")
        log.info("Cycle: paused, waiting for the game window")
        while not focused():
            if f8():
                return False
            time.sleep(0.5)
        time.sleep(1.0)
        log.info("Cycle: game window back; resuming")
        return True

    def step(run, *args):  # type: ignore[no-untyped-def]
        """Run a routine; if it stopped only because focus was lost, pause and retry."""
        for _ in range(5):
            result = run(io, *args)
            ok = result if isinstance(result, bool) else result.ok
            if ok or f8() or focused():
                return result
            if not pause_for_focus():
                return result
        return result

    def wait(seconds: float) -> bool:
        """Sleep, but return False at once on F8."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if f8():
                return False
            time.sleep(0.2)
        return True

    try:
        if not io.wait_for_focus():
            return 1
        while time.monotonic() - started < max_hours * 3600:
            cycles += 1
            log.info("Cycle %d: town + stash", cycles)
            if not step(_run_town):
                if f8():
                    return 1
                # T does nothing while standing in another town (live: Kyovashad).
                # Skip the stash this round; the Warplan teleport works anywhere,
                # and next round T is pressed from the Helltide.
                log.warning("Cycle: town + stash failed; skipping the stash this round")
                print("Town + stash failed (in another town?); skipping the stash this round.")
            # Teleport; between Helltides the game says "Sanctuary is at peace"
            # and leaves us in town: wait and try again.
            waited = 0.0
            while True:
                if f8():
                    return 1
                result = step(_run_helltide_teleport, True)
                if result.ok:
                    break
                # No Helltide up: the Teleport either does nothing ("Sanctuary is at
                # peace", no loading screen; live 00:58) or lands back in Ichorfall.
                no_helltide = "not in a Helltide" in result.reason or "no loading screen" in result.reason
                if not no_helltide or waited >= 20 * 60:
                    log.warning("Cycle: teleport failed (%s); stopping", result.reason)
                    return 1
                log.info("Cycle: no Helltide right now; trying again in 60s")
                print("No Helltide right now; trying again in 60s...")
                if not wait(60.0):
                    return 1
                waited += 60.0

            gone_since: list[float | None] = [None]
            farm_start = time.monotonic()
            route_seen = [False]
            last_busy = [farm_start]

            def end_check(frame, obs, decision, now):  # type: ignore[no-untyped-def]
                if obs.black or obs.screen.no_hud or decision.state in {"DEAD", "PAUSED", "LOADING", "NOHUD"}:
                    gone_since[0] = None
                    return None
                if obs.route.valid:
                    if not route_seen[0]:
                        log.info("Cycle: following recorded route %r", obs.route.name)
                    route_seen[0] = True
                elif config.routes.enabled and not route_seen[0] and now - farm_start >= 20.0:
                    # Landed in a town with no recording: wandering around
                    # town finds nothing, so stop and ask for a route.
                    return "no recorded route for this town (record one with -Record <town>)"
                if helltide.seen(frame, "helltide_label"):
                    gone_since[0] = None
                elif gone_since[0] is None:
                    gone_since[0] = now
                elif now - gone_since[0] >= 20.0:
                    return "Helltide ended (timer gone 20s)"
                if stash_every_min > 0 and now - farm_start >= stash_every_min * 60:
                    return f"stash trip ({stash_every_min:g} min)"
                # Watchdog (idea from qqt_diablo's Helltide script, which resets
                # when it has not cast anything for a while): nothing fought or
                # looted for idle_reset_min = lost, stuck, or an empty corner.
                # Start the round over: town, teleport, route.
                if decision.state in {"FIGHT", "TRACK", "APPROACH", "LOOT", "PULSE"}:
                    last_busy[0] = now
                elif idle_reset_min > 0 and now - last_busy[0] >= idle_reset_min * 60:
                    return f"idle reset (no fights or loot for {idle_reset_min:g} min)"
                return None

            log.info("Cycle %d: farming", cycles)
            reason = _farm(config, input_enabled=True, end_check=end_check, loot_hold_s=8.0)
            log.info("Cycle %d: farm ended: %s", cycles, reason)
            for _ in range(3):
                if not reason.startswith("HUD gone"):
                    break
                frame = io.grab()
                if not helltide.seen(frame, "teleport_button"):
                    break
                # Live: a click on the waypoint opened the map. Not a death.
                log.info("Cycle: the map was open (not a death); closing it and farming on")
                io.send([__import__("d4bot.brain", fromlist=["Action"]).Action(kind="press", key="esc")])
                if not wait(1.5):
                    return 1
                reason = _farm(config, input_enabled=True, end_check=end_check, loot_hold_s=8.0)
                log.info("Cycle %d: farm ended: %s", cycles, reason)
            if not (reason.startswith("Helltide ended") or reason.startswith("stash trip") or reason.startswith("idle reset")):
                print(f"Cycle stopped: {reason}")
                return 0 if reason.startswith("F8") else 1
            if not wait(2.0):
                return 1
        print("Cycle: max hours reached")
        return 0
    finally:
        io.close()
        log.info("Cycle finished after %d cycle(s), %.0f min", cycles, (time.monotonic() - started) / 60)


def cmd_helltide(config_path: str, enable_input: bool, accept_risk: bool, with_town: bool) -> int:
    """Teleport to the Helltide with the Warplan button (optionally stash first)."""
    steps = [_run_town, _run_helltide_teleport] if with_town else [_run_helltide_teleport]
    title = "Town + stash, then Warplan teleport to the Helltide." if with_town else "Warplan teleport to the Helltide."
    return _routine_command(config_path, enable_input, accept_risk, steps, title)


def cmd_snap(config_path: str, folder: str, max_seconds: float) -> int:
    """Save a full-size game screenshot on every F9 press; F8 finishes. No input sent."""
    config = load_config(config_path)
    capture = ScreenCapture(config.capture)
    out = PACKAGE_ROOT / "data" / folder
    out.mkdir(parents=True, exist_ok=True)
    print(f"Screenshot helper: F9 = save a screenshot to {out}, F8 = finish.")
    print("Only the game window is captured, and only while it is in front.\n")
    started = time.monotonic()
    count = 0
    f9_down = False
    try:
        while time.monotonic() - started < max_seconds:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                break
            pressed = emergency_stop_pressed("f9")
            if pressed and not f9_down:
                frame = capture.grab()
                if frame.window and is_foreground(frame.window.hwnd):
                    count += 1
                    path = out / f"snap_{count:02d}.png"
                    cv2.imwrite(str(path), frame.image)
                    print(f"  saved {path.name}")
                else:
                    print("  game window not in front; nothing saved")
            f9_down = pressed
            time.sleep(0.03)
    except KeyboardInterrupt:
        pass
    finally:
        capture.close()
    print(f"\nDone: {count} screenshot(s) in {out}")
    return 0


def cmd_collect(config_path: str, name: str, every_s: float, max_minutes: float, min_change: float) -> int:
    """Save gameplay frames for YOLO training while you play. No input is sent.

    One frame every `every_s` seconds, only while the game is in front with
    its HUD visible, and only if the view changed since the last saved frame.
    """
    from .dataset import FrameDeduper

    config = load_config(config_path)
    capture = ScreenCapture(config.capture)
    vision = Vision(config)
    session = f"{name}_{time.strftime('%Y%m%d_%H%M%S')}"
    out = PACKAGE_ROOT / "data" / "yolo" / "raw" / session
    out.mkdir(parents=True, exist_ok=True)
    deduper = FrameDeduper(min_change)
    print(f"Collecting frames for YOLO into {out}")
    print("Play normally (Helltide, fights, loot, events, town). No input is sent. F8 finishes.\n")
    started = time.monotonic()
    last_save = 0.0
    last_report = started
    saved = skipped = 0
    try:
        while time.monotonic() - started < max_minutes * 60:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                break
            now = time.monotonic()
            if now - last_save < every_s:
                time.sleep(0.05)
                continue
            frame = capture.grab()
            last_save = now
            if not (frame.window and is_foreground(frame.window.hwnd)):
                continue  # covered/unfocused: the capture would not show the game
            image = frame.image
            hud = vision.hud_score(image)
            if float(image.mean()) < config.death.black_max_value or (
                hud is not None and hud < config.death.hud_threshold
            ):
                continue  # loading screen, map, menu: nothing to learn from
            if not deduper.keep(image):
                skipped += 1
                continue
            saved += 1
            cv2.imwrite(str(out / f"frame_{saved:05d}.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if now - last_report >= 60.0:
                last_report = now
                print(f"  {saved} frames saved ({skipped} near-duplicates skipped)")
    except KeyboardInterrupt:
        pass
    finally:
        capture.close()
    print(f"\nDone: {saved} frame(s) in {out}")
    print("Next: label them with  .\\run.cmd -Label")
    return 0


def cmd_record(config_path: str, name: str, max_seconds: float | None) -> int:
    """Record a route: no input is sent. Walk the route yourself; F8 saves it."""
    from .minimap import walkable_mask
    from .routes import RouteRecorder

    config = load_config(config_path)
    if not name.replace("_", "").replace("-", "").isalnum():
        print("Route name: letters, digits, - and _ only (e.g. kehjistan_1)")
        return 1
    capture = ScreenCapture(config.capture)
    recorder = RouteRecorder(config.nav)
    pacer = FramePacer(config.capture.fps)
    region = config.regions["minimap"]
    print(f"Recording route {name!r}. No input is sent.")
    print("Stand on the waypoint, click into the game, then walk your route.")
    print("Press F8 at the end to save (Ctrl+C cancels).\n")
    started = time.monotonic()
    last_report = started
    skipped = 0
    try:
        while True:
            if emergency_stop_pressed(config.input.emergency_stop_key):
                break
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                break
            frame = capture.grab()
            pacer.wait()
            if not (frame.window and is_foreground(frame.window.hwnd)):
                skipped += 1
                continue  # covered/unfocused: the capture would not show the game
            x, y, w, h = region.pixels(frame.image.shape[1], frame.image.shape[0])
            crop = frame.image[y : y + h, x : x + w]
            walkable = float((walkable_mask(crop, config.nav) > 0).mean())
            if not 0.04 <= walkable <= 0.97:
                skipped += 1
                continue  # big map / menu open
            recorder.add(crop)
            now = time.monotonic()
            if now - last_report >= 5.0:
                print(
                    f"  {now - started:5.0f}s  frames={recorder.frames}  path points={len(recorder.path)}"
                    f"  map matches={recorder.corrections}  skipped={skipped}"
                )
                last_report = now
    except KeyboardInterrupt:
        print("Cancelled; nothing saved.")
        return 1
    finally:
        capture.close()
    if recorder.frames < 20:
        print("Too little recorded (was the game focused?); nothing saved.")
        return 1
    out = recorder.save(PACKAGE_ROOT / config.routes.folder, name)
    print(f"\nSaved route to {out}  ({len(recorder.path)} points, {recorder.frames} frames)")
    print("Turn it on with routes: enabled: true in config.yaml.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d4bot",
        description="External screen-vision assistant for Diablo IV. Botting can get you banned.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="capture, observe, and optionally send input")
    run.add_argument("--config", default=str(_default_config()))
    run.add_argument("--debug", action="store_true", help="show the OpenCV debug overlay")
    run.add_argument("--enable-input", action="store_true", help="allow synthetic input")
    run.add_argument("--i-understand-ban-risk", action="store_true")
    run.add_argument("--max-seconds", type=float, default=None)
    run.add_argument("--snapshots", default=None, help="save overlay images to this folder (no window)")
    run.add_argument("--snapshot-every", type=float, default=3.0, help="seconds between snapshots")
    run.add_argument(
        "--start",
        choices=("human", "demon"),
        default=None,
        help="starting form for form.mode toggle_state (buff mode detects it)",
    )

    cycle = sub.add_parser("cycle", help="town + stash -> teleport -> farm until the Helltide ends -> repeat")
    cycle.add_argument("--config", default=str(_default_config()))
    cycle.add_argument("--enable-input", action="store_true")
    cycle.add_argument("--i-understand-ban-risk", action="store_true")
    cycle.add_argument("--max-hours", type=float, default=2.0)
    cycle.add_argument("--stash-every-min", type=float, default=30.0, help="town trip during a long Helltide (0 = never)")
    cycle.add_argument(
        "--idle-reset-min", type=float, default=4.0,
        help="restart the round (town -> teleport -> route) after this long with no fights or loot (0 = never)",
    )

    helltide = sub.add_parser("helltide", help="map -> Warplan Teleport -> Helltide (optionally town + stash first)")
    helltide.add_argument("--config", default=str(_default_config()))
    helltide.add_argument("--enable-input", action="store_true")
    helltide.add_argument("--i-understand-ban-risk", action="store_true")
    helltide.add_argument("--with-town", action="store_true", help="run the town + stash routine first")

    teach = sub.add_parser("teach-stash", help="record your clicks from the town portal to the stash")
    teach.add_argument("--config", default=str(_default_config()))

    town = sub.add_parser("town", help="town portal, open the stash, move the inventory into it")
    town.add_argument("--config", default=str(_default_config()))
    town.add_argument("--enable-input", action="store_true")
    town.add_argument("--i-understand-ban-risk", action="store_true")

    snap = sub.add_parser("snap", help="F9 saves a game screenshot, F8 finishes (no input sent)")
    snap.add_argument("folder")
    snap.add_argument("--config", default=str(_default_config()))
    snap.add_argument("--max-seconds", type=float, default=1800.0)

    collect = sub.add_parser("collect", help="save gameplay frames for YOLO training while you play (no input sent)")
    collect.add_argument("name")
    collect.add_argument("--config", default=str(_default_config()))
    collect.add_argument("--every-s", type=float, default=1.0)
    collect.add_argument("--max-minutes", type=float, default=240.0)
    collect.add_argument("--min-change", type=float, default=6.0, help="mean pixel change needed to keep a frame")

    record = sub.add_parser("record", help="record a route by walking it (no input sent)")
    record.add_argument("name")
    record.add_argument("--config", default=str(_default_config()))
    record.add_argument("--max-seconds", type=float, default=None)

    doctor = sub.add_parser("doctor", help="check window capture, regions, and templates")
    doctor.add_argument("--config", default=str(_default_config()))

    cal = sub.add_parser("calibrate", help="interactive ROI/template calibration")
    cal_sub = cal.add_subparsers(dest="what", required=True)
    regions = cal_sub.add_parser("regions")
    regions.add_argument("--config", default=str(_default_config()))
    regions.add_argument(
        "--only",
        choices=REGION_ORDER,
        default=None,
        help="calibrate only one region",
    )
    skill = cal_sub.add_parser("skill")
    skill.add_argument("name")
    skill.add_argument("--config", default=str(_default_config()))
    sample = cal_sub.add_parser("sample")
    sample.add_argument("--config", default=str(_default_config()))
    form = cal_sub.add_parser("form")
    form.add_argument("--config", default=str(_default_config()))
    buff = cal_sub.add_parser("buff", help="capture the demon-form buff icon (be in demon form)")
    buff.add_argument("--config", default=str(_default_config()))
    death = cal_sub.add_parser("death", help="capture the revive UI as a death template (be dead)")
    death.add_argument("--config", default=str(_default_config()))

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(getattr(args, "debug", False))

    try:
        if args.command == "cycle":
            return cmd_cycle(
                args.config, args.enable_input, args.i_understand_ban_risk,
                args.max_hours, args.stash_every_min, args.idle_reset_min,
            )
        if args.command == "helltide":
            return cmd_helltide(args.config, args.enable_input, args.i_understand_ban_risk, args.with_town)
        if args.command == "teach-stash":
            return cmd_teach_stash(args.config)
        if args.command == "town":
            return cmd_town(args.config, args.enable_input, args.i_understand_ban_risk)
        if args.command == "collect":
            return cmd_collect(args.config, args.name, args.every_s, args.max_minutes, args.min_change)
        if args.command == "snap":
            return cmd_snap(args.config, args.folder, args.max_seconds)
        if args.command == "record":
            return cmd_record(args.config, args.name, args.max_seconds)
        if args.command == "doctor":
            return cmd_doctor(args.config)
        if args.command == "run":
            return cmd_run(
                args.config,
                debug=args.debug,
                enable_input=args.enable_input,
                accept_risk=args.i_understand_ban_risk,
                max_seconds=args.max_seconds,
                start_form=args.start,
                snapshot_dir=args.snapshots,
                snapshot_every_s=args.snapshot_every,
            )
        if args.command == "calibrate":
            if args.what == "regions":
                calibrate_regions(args.config, only=args.only)
            elif args.what == "skill":
                calibrate_skill(args.config, args.name)
            elif args.what == "sample":
                sample_globes(args.config)
            elif args.what == "form":
                calibrate_form(args.config)
            elif args.what == "buff":
                calibrate_buff(args.config)
            elif args.what == "death":
                calibrate_death(args.config)
            return 0
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except ValueError as exc:
        print(f"CONFIG ERROR: {exc}")
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
