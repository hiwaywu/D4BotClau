# d4-visual-bot

An **external, screen-vision** experiment for Diablo IV. It reads pixels, not game memory. No DLL injection, no packet manipulation, no anti-cheat bypass.

> **Ban warning:** Blizzard's terms prohibit bots and automated play. This can get your account permanently banned. Use an account you are willing to lose, never leave it unattended, and press **F8** to stop. The author/assistant is not responsible for account actions.

## What it does today

Two modes, both driven by the same perception layer:

| Mode | Movement | Combat | Use case |
|---|---|---|---|
| `assist` | **you** move | keeps demon form up, aims at the nearest monster health bar, presses skills/potion | Safest way to test detection; normal play with a combat helper |
| `roam` | bot steers by the **minimap**: toward open ground, away from Helltide edges/cliffs, turns when stuck, avoids re-walking recent ground | same combat loop | Helltide farming |

Also: demon form is read from the **demon buff icon** (no start flags needed), and the run **stops on the grey death screen** and pauses on black/loading screens.

It is still **not** a turnkey farmer. It does **not** pick up loot, open chests, do events, or dodge hazards, and it must be watched.

![Synthetic debug overlay](docs/demo_overlay.png)

*Synthetic preview (not gameplay): green = detected enemy health bars, red/orange = health/resource, yellow = enemy search area. The real overlay looks the same over the game.*

## Quick start

### 1. Install

Requires Windows 10/11 and Python 3.11+.

```powershell
cd d4-visual-bot
.\setup.cmd
```

If PowerShell says the script is not digitally signed, that is why the `.cmd` wrappers exist: they run the `.ps1` with `-ExecutionPolicy Bypass` for this folder only.

This creates a venv at `%LOCALAPPDATA%\d4-visual-bot\venv` (outside OneDrive) and installs OpenCV, mss, pydirectinput, PyYAML, and pytest.

### 2. Configure the game

- Set Diablo IV to **Windowed Fullscreen** (not exclusive fullscreen).
- Use a fixed resolution, e.g. **1920×1080**.
- Turn **HDR off** for the game/Windows while testing; HDR washes out color thresholds.
- Use the default HUD layout and a normal HUD scale.
- Put the game on your **primary monitor**.
- Do not run the bot elevated unless the game is also elevated.
- At 5120×1440 or other ultrawide resolutions, expect ~10–15 fps of vision processing on CPU. Calibrate regions at that exact resolution. If it feels sluggish, test at 1920×1080 first; the config is resolution-specific.

### 3. Check capture

Launch D4, stand in town, then:

```powershell
.\run.cmd -Doctor
```

You want to see the right window title, a non-black frame, and plausible health/resource numbers.

### 4. Calibrate the regions

```powershell
.\run.cmd -Calibrate regions
```

Draw tight boxes in this order:

1. **health_globe** — the colored liquid area, not the metal frame.
2. **resource_globe** — same for your class resource.
3. **skill_bar** — the whole row of six skill icons.
4. **enemy_roi** — the central play area where enemy health bars appear. Exclude the globes, minimap, chat, and bottom HUD.
5. **center** — the area around your character; targeting prefers enemies near this.

### 5. Calibrate the globes

Stand at **full health** with a **full resource globe**:

```powershell
.\run.cmd -Calibrate sample
```

It prints the measured red/blue area ratios. Put those values into `config.yaml`:

```yaml
globe:
  health_full_ratio: 0.42    # use the printed health ratio
  resource_full_ratio: 0.42  # use the printed resource ratio
```

Small errors are fine; the level is clamped to 0–1.

### 6. Demon form (Warlock)

This build uses `form.mode: buff`. In demon form the buff bar shows a white **horned-skull** icon with a stack count; in human form it is absent. The bot template-matches that icon (`templates/demon_buff.png`, captured at 2560×1440): demon frames score 0.89–1.00, human frames 0.37–0.47, threshold 0.70.

- Start in **either** form; the bot reads which one you are in.
- Buff gone → wait the 11s cooldown → press `2` → the buff must appear within 3s. If it does not (town, wrong slot), it retries once, then backs off 30s instead of spamming `2`.
- Helltide fire and red effects cannot fake it: it is a UI icon, not a colour.

If you change resolution/HUD scale, recapture the icon in demon form: `.\run.cmd -Calibrate buff`. The old `toggle_state` mode (needs `-StartHuman` / `-StartDemon`) is still available.

### 7. Capture skill icons (optional but recommended)

With the skill off cooldown and its icon fully visible:

```powershell
.\run.cmd -Calibrate skill -SkillName skill_1
```

Repeat for `skill_1`, `skill_2`, `skill_4`, and `skill_6` (the ones the bot uses). The crop is saved to
`templates/skills/<name>_ready.png` and the config is updated.

If you skip this, the bot uses `fallback_cooldown_s` from `config.yaml` instead of seeing the cooldown sweep.

### 8. Dry run (no input sent)

```powershell
.\run.cmd -Debug -MaxSeconds 30
```

Watch the overlay:

- Green boxes = detected enemy health bars.
- Red/orange bars = health/resource levels.
- `FORM DEMON 0.98` / `FORM HUMAN 0.44` = demon-buff detector state and match score.
- Minimap: green rays = open directions, red = blocked, cyan arrow = chosen heading.
- `INPUT DRY-RUN` = no keys or clicks are being sent.
- `q` or `Esc` in the overlay quits.

Tune regions/thresholds until the boxes are stable.

On a single monitor the overlay window can cover the game, and the bot then captures its own overlay. Save the overlay to disk instead:

```powershell
.\run.cmd -MaxSeconds 30 -Snapshots data\dryrun
```

### 9. Enable combat assist

Only after the dry run looks right:

```powershell
.\run.cmd -Debug -EnableInput -IAcceptBanRisk
```

Both flags are required. The bot will only act while the D4 window is foreground. **F8** stops input immediately.

### 10. Try Helltide roam (experimental)

`config.yaml` ships with `mode: roam` and `nav.enabled: true`. Run with a short time limit first:

```powershell
.\run.cmd -Debug -EnableInput -IAcceptBanRisk -MaxSeconds 60
```

How minimap navigation works:

- The minimap is player-centred. Beige parchment = walkable, Helltide red = blocked. 24 rays from the centre measure how far each direction stays open.
- Every 0.8s it clicks toward the best direction: open, close to the current heading, and not toward ground walked in the last 90s.
- The minimap's own motion (phase correlation) is the player's motion. If it moves < 3px in 1.6s while walking, it is **stuck**: that heading is penalised and it turns.
- It compares the direction it clicked with the direction the minimap moved and corrects any screen↔minimap rotation by itself (`rot` on the overlay; should stay near 0).
- If the minimap is unreadable (other zones, big map open), it falls back to the old random legs.

Tune in `nav:` (`click_distance_px`, `stuck_px`, `min_clearance`, `revisit_weight`).

## Build-specific config

Edit the `skills` list in `config.yaml` to match your build:

```yaml
skills:
  - name: skill_1
    key: "1"
    enabled: true
    priority: 40              # higher = tried first
    requires_enemy: true
    min_interval_s: 0.30      # hard spam guard
    fallback_cooldown_s: 0.8  # used when no template exists
    template: "templates/skills/skill_1_ready.png"
    threshold: 0.16           # lower = stricter match (TM_SQDIFF_NORMED)
```

Notes:

- Keys `1`–`4` are the D4 skill bar. `mouse_right` is supported, but skills bound to the mouse can move/aim differently; test in `assist` first.
- `priority` controls which ready skill fires first.
- `requires_enemy: false` is useful for buffs that should be kept up while roaming.
- `threshold` is a **difference** score: `0.0` is a perfect match. If a ready icon is missed, raise it slightly (e.g. `0.22`); if cooldown icons are treated as ready, lower it (e.g. `0.10`).

## Warlock demon form (this build)

Build reference: [Blazing Scream Warlock](https://d4builds.gg/builds/blazing-scream-warlock-endgame/?var=0).

The config is pre-mapped to the bar in your screenshots:

| Slot | Key | Use | Bot behavior |
|---|---|---|---|
| 1 | `1` | cooldown/aura | auto every 15s |
| 2 | `2` | **demon toggle** | state machine only |
| 3 | `3` | tiger summon / mount toggle | **disabled**, press manually |
| 4 | `4` | cooldown/aura | auto every 15s |
| 5 | `mouse_left` | unused / seasonal | **disabled** |
| 6 | `mouse_right` | nonstop spam attack | auto when a target is visible |

`skill_2.enabled` is deliberately `false`: the normal rotation must never press it. The form gate owns key `2`.

How the gate behaves (`form.mode: buff`):

- **Buff visible** → demon; never presses `2`.
- **Buff disappears** → `HUMAN`, 11-second cooldown, then press `2` (only when the slot-2 icon looks ready).
- **Pressed `2` but no buff within 3s** → one retry, then a 30s back-off. No `2` spam.
- **Human**: does not attack or roam until demon is back.

If `skill_6` is a **channeled** skill, the current code taps right-click every ~0.12 s. Tell me and I’ll add hold/release support; tapping a channeled skill loses damage.

Potion settings:

The purple health globe is not reliably readable, so the bot uses a conservative combat timer instead of health percentage:

```yaml
input:
  potion_enabled: true
  potion_mode: timer
  potion_timer_s: 8.0
```

It presses `Q` at most every 8 seconds while enemies are present and demon form is active. Switch to `potion_mode: health` only after calibrating the globe.

## How it works

```
mss capture (BGR, 20 fps)
   ├─ HSV + contour shape → monster health bars (teal ~80×6px) → 2-frame persistence
   ├─ template match → demon buff icon → form (3-frame confirmation)
   ├─ template match → skill icons ready/on cooldown
   ├─ minimap: walkable mask → ray clearance; phase correlation → motion/position
   ├─ play-area saturation → grey death screen / black loading screen
   └─ HSV threshold → health/resource globe fill (not trusted: purple globe)
            ↓
   brain: potion / keep demon form / aim below nearest bar / press ready skill
          / minimap-steered roam with stuck recovery
            ↓
   pydirectinput (only if foreground + input enabled + risk accepted)
```

Enemy health bars are a much easier target than enemy bodies: they are small, horizontal, high-contrast rectangles. In the captured Helltide fights they are **teal** (hue ~83, ~80×6px at 1440p). The Helltide timer/threat-meter corner is excluded (its red bar used to be "attacked"), bars taller than 8px are ignored (the mercenary's bar is 9px, item labels ~21px), and the bot aims ~50px below the bar at the body.

## YOLO / “any AI tool”?

An optional YOLO detector is built in (off by default): `.\setup.cmd -Yolo` once, then `.\run.cmd -Collect helltide` while you play, `.\run.cmd -Label` to draw boxes, `.\run.cmd -Train` to train and install `models/d4.onnx`, and `detector: enabled: true` in `config.yaml`. An LLM/VLM is the wrong tool for a 20 Hz control loop. See [docs/YOLO.md](docs/YOLO.md) for what it does, how much labelling it takes, and how to check it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Black or 0×0 capture | Switch D4 to Windowed Fullscreen; make sure it is on the captured monitor |
| Window not found | Check `capture.window_title`; try `capture.mode: monitor` |
| Input does nothing | Click the game first; run bot and game at the same elevation; some games ignore synthetic input |
| Health bar stuck at 0/1 | Recalibrate the globe ROI, then set `health_full_ratio` from `Calibrate sample` |
| Too many fake enemies | Shrink `enemy_roi`; raise `min_fill` / `min_aspect`; increase `persistence_frames` |
| Skills never fire | Recapture templates at the **same resolution**; check `threshold`; confirm keys in D4 |
| `FORM ?` never resolves | Buff bar region or template wrong for your resolution: `-Calibrate buff` while in demon form |
| Says `FORM HUMAN` while demon | Check the buff score in `-Doctor`; recapture with `-Calibrate buff` |
| `toggle failing; retry in 30s` | Pressing `2` did not produce the buff: in town, slot 2 is not the demon skill, or the game is not focused |
| Run ends with `death detected` while alive | Check the saved `data/death_*.png`; lower `death.max_saturation` |
| Does not stop when dead | Capture the revive screen with `-Calibrate death` while dead |
| Roam keeps hugging a wall | Watch the minimap rays in `-Debug`; raise `nav.min_clearance`; lower `nav.stuck_px` |
| `rot` on the overlay drifts far from 0 | Minimap/screen mismatch; set `nav.learn_rotation: false` and `rotation_deg: 0` |
| Right-click spam does nothing | Confirm slot 6 is actually right mouse in D4; if it is channeled, it needs hold support |
| Overlay will not open | OpenCV GUI/display issue; run without `-Debug` or use a normal desktop session |
| Bot clicks the HUD while roaming | Increase `roam.play_area_margin`; shrink `enemy_roi` |
| Account banned | Botting is bannable. Do not use an account you care about. |

## Project layout

```
d4-visual-bot/
  config.yaml            # regions, keys, thresholds, mode
  setup.cmd / setup.ps1  # one-time venv + deps (.cmd bypasses execution policy)
  run.cmd / run.ps1      # doctor / calibrate / run
  tools/make_demo.py     # synthetic overlay preview
  tools/check_captures.py # run perception over saved screenshots (no game)
  tools/yolo_label.py    # draw YOLO boxes on collected frames (-Label)
  tools/yolo_dataset.py  # build the train/val split (by session)
  tools/yolo_train.py    # train + export models/d4.onnx (-Train)
  src/d4bot/
    capture.py           # mss + Windows window/DPI handling
    vision.py            # globes, enemy bars, skill templates, demon buff, death
    minimap.py           # walkable mask, ray clearance, motion tracking, path planning
    detector.py          # optional YOLO detector (ONNX via OpenCV / onnxruntime)
    dataset.py           # YOLO frame collection, label files, dataset split
    brain.py             # assist/roam decisions
    controller.py        # pydirectinput + foreground/F8 gates
    calibrate.py         # interactive ROI/template capture
    overlay.py           # debug view
    app.py               # CLI
  tests/                 # synthetic-frame tests (no game needed)
  templates/demon_buff.png # demon-form buff icon (2560x1440)
  templates/skills/      # your ready-state icons (gitignored)
  docs/YOLO.md           # optional YOLO detector: collect -> label -> train -> enable
  PLAN.md                # milestones and acceptance criteria
```

## Development

```powershell
$py = "$env:LOCALAPPDATA\d4-visual-bot\venv\Scripts\python.exe"
& $py -m pytest
```

The tests build synthetic frames (including a simulated maze the nav has to explore), so they run without D4. To check perception against real screenshots in `data/`:

```powershell
& $py tools\check_captures.py --overlay data\overlays
```
