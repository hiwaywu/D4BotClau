# Build plan: visual Diablo IV Helltide bot

## Goal

A Windows bot that plays through the screen only: capture the game, detect UI/enemies with OpenCV, and send mouse/keyboard input. First target is **Helltide farming** in the open world, because it tolerates messy movement better than a dungeon.

Non-goals for v1:

- Reading game memory, packets, or process handles.
- DLL injection, driver input, or anti-cheat evasion.
- Perfect pathfinding, event completion, loot filtering, or overnight AFK.
- Anything that guarantees account safety. **Botting is bannable.**

## Why Helltide is easier — and what is still hard

Easier:

- Enemies are dense; you do not need a precise route to find something to hit.
- You can disengage and reset by running away.
- A rough “walk, fight, walk” loop produces useful behavior quickly.
- Death is not a run-ending failure; the bot can stop and let you recover.

Still hard:

- It is **not static**: packs spawn/despawn, events move the fight, meteors and red effects fill the screen, terrain blocks movement, and other players change the scene.
- “Randomly attack and run” gets stuck on geometry and can pull more than it can kill.
- The red/orange Helltide palette produces false enemy detections.
- Public play means reports are possible; Blizzard can also flag inhuman input patterns.
- Loot and cinders are a separate perception problem; v1 ignores them.

## Architecture

```
config.yaml
   │
   ▼
ScreenCapture (mss, client-area crop, DPI awareness)
   │  BGR frame @ 20 fps
   ▼
Vision
   ├─ globe HSV fill  ──────────────► health / resource / death
   ├─ enemy-bar HSV + contours ─────► enemy boxes (2-frame persistence)
   ├─ template match ───────────────► skill ready / cooldown
   └─ orange-glow metric ───────────► human / demon form
   │
   ▼
CombatBrain
   ├─ assist: potion + aim nearest bar + highest-priority ready skill
   └─ roam:   same, plus click-to-move leg every N seconds / on stuck
   │
   ▼
InputController
   ├─ -EnableInput AND -IAcceptBanRisk
   ├─ game window foreground
   ├─ F8 emergency stop
   └─ pydirectinput (keys / cursor / click)
   │
   ▼
DebugOverlay (optional) + session log
```

Deliberate design choices:

- **No model in v1.** Color/thin-bar detection is fast, inspectable, and needs no labels.
- **Assist before roam.** Prove targeting and skill firing while you move manually, then add movement.
- **Input is opt-in twice.** Two CLI flags are required (`-EnableInput` and `-IAcceptBanRisk`); there is no config-only switch.
- **Foreground gate.** The bot cannot type into Discord or another game by accident.
- **F8 kill switch.** Polled every frame, not a global keyboard hook.

## Milestones

### M0 — Scaffold (done in this repo)

- Capture, perception, brain, controller, overlay, calibration, tests.
- Dry-run default; synthetic tests pass.
- **Acceptance:** `pytest` passes; `run.cmd -Doctor` runs on your machine.

### M1 — Calibration and read-only validation (you, ~1–2 hours)

- Run `Doctor`, `Calibrate regions`, `Calibrate sample`, `Calibrate form`.
- Capture skill templates for 1, 2, 4, and 6.
- Record a 5-minute `-Debug` session in town and in Helltide.
- **Acceptance:**
  - Health/resource levels move smoothly and hit ~1.0 when full.
  - Enemy boxes appear on real mobs and mostly not on red scenery.
  - Skill icons flip ready/cooldown correctly.
  - `FORM HUMAN` / `FORM DEMON` flips reliably when you press `2`, with no flicker.
  - No input is sent (`INPUT DRY-RUN`).

### M1.5 — Demon-form gate (Warlock, ~30 minutes)

- Run `run.cmd -Calibrate form`: sample the last skill icon in human form, then in demon form.
- Dry-run and transform manually 10 times; also let a mob knock you out of demon form.
- **Acceptance:**
  - No double-toggle: it never presses `2` while already demon.
  - After a damage revert, it re-enters demon form as soon as slot 2 is ready.
  - `FORM ?` only appears briefly at startup or during a bad camera/UI moment.
  - If you manually leave demon form, it does not fight you for control.

### M2 — Combat assist (you, ~1 evening)

- Run `-Debug -EnableInput -IAcceptBanRisk` in a low-density area.
- Tune skill priority, cooldowns, potion threshold, and `enemy_roi`.
- **Acceptance:**
  - Demon form is maintained for >95% of combat time.
  - 10 minutes without a false potion at full health.
  - Skills fire when a target is present and stop when it dies.
  - Losing foreground pauses instantly; F8 stops instantly.
  - You can play normally with the helper on.

### M3 — Helltide roam (you, ~1–2 evenings)

- Set `mode: roam`; test 30-second runs, then 5-minute runs.
- Tune `leg_interval_s`, `turn_jitter_deg`, `click_distance_px`, `stuck_confirm_s`.
- **Acceptance:**
  - 5 minutes of continuous roaming without clicking the HUD.
  - Stuck recovery triggers when the view stops changing.
  - Death detection stops input within ~3 s.
  - You are comfortable watching it; it is not left unattended.

### M4 — Make it less dumb (optional, 1–2 weeks)

Pick one, not all:

1. **Minimap navigation** — detect the player arrow and walk toward a fixed point; still no full pathfinding.
2. **Loot pickup** — detect item labels/beams, move to them, click; needs OCR or a detector.
3. **Elite/event awareness** — prioritize elite bars or event circles; needs a second detector.
4. **YOLO enemy/object detector** — replace or supplement enemy-bar detection. See `docs/YOLO.md`.

### M5 — Run loop and recovery (optional)

- Restart after death, repair/reset, stash, sell, route between Helltide zones.
- This is where most “bot” complexity and most ban risk live. Do not start here.

## YOLO decision gate

Do **not** train YOLO until M1/M2 gives you numbers. Start it only if at least one is true:

- Enemy-bar detection misses more than ~30% of mobs you care about.
- False positives cause more than ~5 wasted actions per minute.
- You need to distinguish objects that have no reliable color/shape signature (elites, loot beams, event altars, hazards).

If you do train it:

- Collect 1,000–3,000 frames from **your** resolution/HUD/Helltide lighting.
- Label `enemy`, `elite`, `loot`, `player`, `health_bar` (choose a small set).
- Start with YOLO nano/small at 640 or 960 input.
- Measure precision/recall **per minute of gameplay**, not just mAP.
- Keep the OpenCV UI pipeline; use YOLO only for world objects.

## What I need from you

The build is now mapped; these are the remaining unknowns:

1. **Last slot key** — I mapped slot 6 to `mouse_right`. Is the spam skill actually right mouse, or a number key?
2. **Channeled or tapped** — is the spam skill a channel (hold) or a cast (tap)? If channeled, hold/release support is the next code change.
3. **Toggle cooldown** — confirm the ~10 s cooldown and whether slot 2 shows an `8` sweep after you leave demon form.
4. **Resolution** — 5120×1440 or 1920×1080? Calibrate at the exact one.
5. **Risk tolerance** — test account? how long unattended?

## Do this now

1. Run `.\setup.cmd` (already installed here, but safe to rerun).
2. Launch D4 in Windowed Fullscreen, HDR off.
3. Run `.\run.cmd -Doctor`; fix capture/window issues first.
4. Run `.\run.cmd -Calibrate regions`.
5. Run `.\run.cmd -Calibrate form` (human, then demon).
6. Run `.\run.cmd -Calibrate sample` and update the globe ratios.
7. Run `.\run.cmd -Debug -MaxSeconds 30`; confirm `FORM HUMAN` / `FORM DEMON` and enemy boxes.
8. Report back with what the overlay gets wrong (especially form flicker).

Do not enable input until the dry run looks boringly stable.
