# HANDOFF — d4-visual-bot

**Status:** full Helltide cycle built: town + stash → Warplan teleport → follow recorded route → farm → repeat. First full live round worked (2026-10-24 01:09). Since then, built offline (cloud session, no game): path planning around walls, idle reset, and an optional YOLO detector pipeline - none of it run live yet. Not safe to leave unattended.
**Next session: start at section 0 below** (step 1 done on the new PC; long soak test moved to the very end by user decision).
**Last updated:** 2026-10-25
**Repo:** https://github.com/hiwaywu/D4BotClau (private). Push after every change; update this file in the same commit.
**Project folder:** `C:\AI STUFF\Claude\Diablo4\d4-visual-bot` (the old copy in `OneDrive\Documents\deepseek-harness\default-workspace\d4-visual-bot` is stale)

## Quick start (current)

```powershell
cd "C:\AI STUFF\Claude\Diablo4\d4-visual-bot"
.\run.cmd -Cycle -EnableInput -IAcceptBanRisk            # full loop, 2h default (-MaxHours 4)
.\run.cmd -Record <town>                                  # record a route from a Helltide waypoint town (no input)
.\run.cmd -Town -EnableInput -IAcceptBanRisk             # just: T -> Ichorfall -> stash everything
.\run.cmd -Helltide -EnableInput -IAcceptBanRisk         # just: map -> Warplan Teleport
.\run.cmd -EnableInput -IAcceptBanRisk -MaxSeconds 600   # just: farm where you stand
```
Needs: Warplan next activity = Helltide, Subo in the party, 2560x1440 windowed, game window on top. F8 stops everything. `data/` (screenshots) and `logs/` are not in git; tests that need captures skip without them.

> **Ban warning:** Blizzard prohibits botting. This project uses screen capture + synthetic input only (no memory reading, no injection, no anti-cheat bypass). It can still get the account banned. Watch it and keep F8 reachable.

---

## 0. NEXT SESSION AT THE HOME PC - START HERE

**For Claude:** the user said "I have no idea what to do; when I get to my home PC, tell me exactly what to do". Walk them through the steps below **one step at a time**: give the exact command, say what they should see, wait for them to report (paste the console output, `logs\session.log` lines, or screenshots), and fix problems before moving on. Do not dump the whole list at once. Commands run in PowerShell in the project folder. After each step, record the result in this file (section 3 style) and push.

All new work is merged into **`main`** (2026-10-24). The user now works locally on the home PC: commit and push to `main` there.

**User decision (2026-10-24): priority is getting it running and adding features, not long soak tests.** Do NOT require a 30+ minute test before moving on. Short runs to check each change are enough; the one long test (1h+ cycle) happens at the very end, after everything below is built (see "Roadmap" after Step 7).

### Step 1 - Get the new code (2 min)
**DONE 2026-10-24 on this PC:** fresh clone at `C:\AI\Claude\D4BotClau` (not `C:\AI STUFF\...`; use this folder in all commands below), already at latest `main`. `.\setup.cmd` -> "Setup complete." on Python 3.14.7 (numpy 2.5.4, opencv 4.14.0.94, mss 10.2.0). `pytest`: all pass, 15 skipped (no `data/` screenshots on this PC).

```powershell
cd "C:\AI STUFF\Claude\Diablo4\d4-visual-bot"
git status                       # should be clean; if not, ask the user before touching anything
git checkout main
git pull                         # brings in path planning, idle reset, YOLO pipeline, this checklist
.\setup.cmd                      # refresh dependencies (safe to rerun)
```
Expect: `git pull` lists new files (`CLAUDE.md`, `src/d4bot/detector.py`, `tools/yolo_*.py`, ...) and setup ends with "Setup complete." If `git pull` complains about local changes, stop and look at them with the user.

### Step 2 - Doctor (1 min, no input sent)
**DONE 2026-10-25 00:29:** window 'Diablo IV' 2560x1440, foreground True, HUD 0.99, minimap ok (1 pack, walkable 0.78), routes: 2 loaded (not located here), detector: off, glow -> HUMAN. No traceback. Skill templates missing = expected (glow mode + fallback cooldowns).

Start Diablo IV (2560x1440 windowed, HDR off), stand in a Helltide or town, game window on top. Then:
```powershell
.\run.cmd -Doctor
```
Expect: the right window title, `minimap: ...` readable, `routes: 2 loaded`, and a new line `detector: off (YOLO; see docs/YOLO.md)`. Any traceback = fix before going on.

### Step 3 - Path planning, 5-minute live farm (watched, F8 ready)
In a Helltide, Subo in the party:
```powershell
.\run.cmd -EnableInput -IAcceptBanRisk -MaxSeconds 300 -Snapshots data\path1
```
Click into the game after starting it (the timer starts when the game has focus). Watch it.
Expect: same hunting as before; when a pack is behind a wall/red edge it should walk around through an opening instead of pushing into the wall. Afterwards check:
- `logs\session.log`: the 30s `Stats` lines - `stuck=` should be as low or lower than the last runs (soak 14: 6 in 10 min).
- `data\path1\overlay_*.jpg`: green lines on the minimap = a planned route to a pack behind a wall.
If it walks into walls more than before: set `nav: path_planning: false` in `config.yaml` and compare, then tell Claude what you saw.

**Run 1 2026-10-25 00:31 (Kareka Floodplain, new Helltide, no recorded route):** 234s, 10 packs, 33 loot clicks, stuck 12 (worse than soak 14). Path planning did run (`goal 57px (route 83px)`, `goal 144px (route 198px)`). Three bugs found and fixed (section 3p). **Redo Step 3 once** with the fixes before Step 4.

**Run 2 2026-10-25 00:45 (Path of Desperation, with the 3p fixes):** 179s until F8: **stuck 0**, ~22 packs, 7 loot clicks, demon throughout, no false route, no banner clicks. Missed a red "Amulet" label (fixed, 3q). Near the end one capture came back 2560x954 (window probably dragged partly off screen for a moment) -> HUD read as missing twice. **Step 3 passed.** (A quick window-size check printed 1280x720: that was the check itself not being DPI-aware - Windows scaling is 200% here. Always call `enable_dpi_awareness()` first; the bot does. The game was 2560x1440 all along.)

**Step 4 cycle 1, 2026-10-25 01:04:** stash OK (1 item, 20s) -> Warplan teleport OK (11s) -> landed in **Kyovashad** -> route NOT located -> roamed west, user F8 after 9s. Cause: the 0.8 acquire threshold from 3p; the real Kyovashad waypoint match scores only 0.59. Fixed differently (3r). Next: rerun Step 4.

**Step 4 cycle 2, 2026-10-25 01:17:** kyovashad located (0.65) and followed (#17 -> #35), but each round ended after 21s with "Helltide ended (timer gone 20s)" - the Helltide timer is not shown in Kyovashad town (outside the Helltide; marowen's waypoint is inside it, so this never showed before). It also clicked NPC name plates in town as loot. Both fixed (3s). **User decision:** keep it simple - record **one long route per Helltide town** (waypoint -> open area -> a circuit back to where the circuit began; 5-6 towns), the bot follows it and fights on the way until the Helltide ends, then town + stash + Warplan again. Next: record those routes (Step 4b), starting with the town the Warplan sends you to now.

### Step 4 - Use the full cycle (as long as you like, watched)
This is the normal way to run it. No minimum length - stop with F8 whenever.
```powershell
.\run.cmd -Cycle -EnableInput -IAcceptBanRisk -MaxHours 1
```
Expect, in `logs\session.log`: town + stash -> teleport -> route located -> `Cycle 1: farming`. If it runs long enough: `farm ended: stash trip (30 min)` -> town -> teleport -> `Cycle 2: farming`. If nothing gets fought for 4 minutes: `idle reset (no fights or loot for 4 min)` -> town -> teleport -> route (change with `-IdleResetMin 6`, `0` = off). Note anything odd and tell Claude; fix it, then carry on.
If it stops with "no recorded route for this town": record that town now (Step 4b), then rerun.

### Step 4b - Record a route for a new Helltide town (when it comes up, 3 min, no input)
Stand on the town's waypoint, then:
```powershell
.\run.cmd -Record <townname>      # e.g. -Record zarbinzet
```
Click into the game, walk from the waypoint to a big open area of the Helltide, press F8 to save. Then commit + push `routes\<townname>\`.

### Step 5 - YOLO, part 1: install + collect frames (one-time install ~10 min, then just play)
Can start any time (even before Step 3); it is just playing with a recorder on. Nothing here sends input.
```powershell
.\setup.cmd -Yolo                       # training tools; CUDA torch if an NVIDIA GPU is found; onnxruntime-directml
.\run.cmd -Collect helltide             # then just play normally; F8 when done
```
Play 20-30 minutes per session: Helltide fights, elites, loot on the ground, chests, the green spawner things, some town. Do **3 or more sessions** on different days/zones (each makes its own folder in `data\yolo\raw\`). The console prints how many frames were saved each minute (~1 per second of changing view). Goal: 500+ frames total.

### Step 6 - YOLO, part 2: label (the long part - spread it over evenings)
```powershell
.\run.cmd -Label
```
Drag a box around each monster (key 1), elite (2), dropped item (3), chest (4), spawner (5). Right-click deletes a box. Space = save + next, q = quit (it resumes where you stopped). Box **every** monster in a frame; frames with nothing in them are saved with no boxes (they are important). Do NOT box yourself, the tiger or Subo. Goal for a first model: 300+ labelled frames. Full rules: `docs/YOLO.md`.

### Step 7 - YOLO, part 3: train + check (~1 h on an NVIDIA GPU)
```powershell
.\run.cmd -Train
```
Expect at the end: `Validation mAP50 ...` and a precision/recall line per class, then `Installed ...\models\d4.onnx`. Paste those lines to Claude. Then in `config.yaml` set `detector: enabled: true` and:
```powershell
.\run.cmd -Doctor                                    # detector: d4.onnx via onnxruntime (DmlExecutionProvider); sees monster 0.87 ...
.\run.cmd -MaxSeconds 60 -Snapshots data\yolo1       # dry run, no input: check boxes on data\yolo1\overlay_*.jpg
```
Only if the boxes sit on real monsters (and not on fire/scenery/tiger): set `detector: aim: true` and do a 5-minute live run like Step 3. Then: collect more where it was wrong, `-Label` again (frames now come pre-boxed by the model; just fix them), `-Train` again.

### Roadmap after the steps (build in this order, short live check after each)
1. **Auto-revive after death** - today a death stops the run. Needs the death screen ("Revive at Checkpoint" button) -> click it -> back to town or the Helltide -> continue the cycle. Ask the user for a screenshot of the death screen (`.\run.cmd -Snap death`, F9) if `data\` has none on this PC.
2. **Potions by real health** - today a blind 8s timer. Needs low-health screenshots (the globe is purple/cosmetic on this character).
3. **Green spawner icons** (user idea, HANDOFF "Next ideas") - walk to one, interact, fight around it.
4. **The app** (user wants this at the end): a window with Start/Stop, live status (state, packs, loot, run time) and a settings screen that edits `config.yaml` - attack distance (`combat.attack_range_px`, `fight_radius_px`), skill keys/timers, potions, loot on/off, idle reset, path planning, YOLO on/off. Plus **class profiles**: one saved settings set per class/character (Warlock = demon form on; other classes = form gate off, their own skills and attack range). Simple Windows launcher instead of `run.cmd`.
5. **Final long test** - only now: a 1-2 hour `-Cycle` covering stash trips, a Helltide ending + waiting for the next one, idle resets, a death + revive. Fix what it finds.

### Ongoing
- After every step: tell Claude the result; Claude updates this file and pushes.
- F8 stops everything. Never leave it running unattended.

---

## 1. What it does

Windows Python bot for a Warlock (demon form) in Helltide:

1. Captures the D4 client with `mss` (2560×1440 windowed, HDR off).
2. **Form:** demon = the skill icon frames burn orange (slots 1/2/4, at least 2 of 3). Detects the starting form by itself.
3. Keeps demon form up: frames stop burning → 11s cooldown → press `2` → confirm they burn again (retry once, then back off 30s).
4. **Combat:** finds monster health bars (teal, ~80×6px), aims ~50px below the nearest, spams right-click. `1`/`4` on a 15s timer, `Q` potion on an 8s timer in combat.
5. **Roam:** steers by the minimap: open (beige) ground, away from red Helltide edges and cliffs. Detects stuck from minimap motion, avoids ground walked in the last 90s, and self-corrects any minimap/screen rotation.
6. **Death:** HUD missing → no input; missing 2s → death: run stops, `data/death_*.png` saved. Black/loading screens pause input.
7. Survives single-frame errors (stops after 20 in a row). Logs state changes and 30s stats to `logs/session.log`.

## 2. Run it

```powershell
cd "C:\AI STUFF\Claude\Diablo4\d4-visual-bot"
.\run.cmd -Doctor                                              # capture, form glow, minimap packs, HUD
.\run.cmd -MaxSeconds 30 -Snapshots data\dryrun                # dry run: no input, overlay saved to disk
.\run.cmd -EnableInput -IAcceptBanRisk -MaxSeconds 600 -Snapshots data\run1   # 10 min live
.\run.cmd -EnableInput -IAcceptBanRisk                         # until F8 / death / disconnect
```

Before a live run: in Helltide, Subo in the party, game window on top. The `-MaxSeconds` timer starts when the game first has focus (start the command, then click into the game). F8 stops at once. Prefer `-Snapshots` over `-Debug`: the overlay window can cover the game and the bot then captures its own overlay.
`-StartHuman` / `-StartDemon` are not needed (form is read from the burning skill frames).
`run.ps1` sets `PYTHONPATH` to this folder's `src`, because the shared venv's editable install still points at the old OneDrive copy. Rerunning `.\setup.cmd` here also fixes that.

## 3. Changes on 2026-10-23 (this session)

| Area | Before | Now |
|---|---|---|
| Enemy detection | Red/orange only. In every saved Helltide capture the main "enemy" was the **Helltide threat meter** UI (top right); others were fire/demon bodies. The real monster bars are **teal** and were missed. | Teal range added; each colour family detected separately (the teal bar was merging with the burning monster's orange glow); Helltide UI corner excluded; `min_width 40`, `max_height 8` (mercenary Subo's bar is 9px, labels ~21px); aim offset below the bar. |
| Form | Slot-2 cooldown state machine, needs correct `-StartHuman/-StartDemon` | Demon buff template (`templates/demon_buff.png`). Captures: demon 0.89–1.00, human 0.37–0.47, threshold 0.70. |
| Roam | Random legs + camera-diff stuck guess | Minimap ray clearance + phase-correlation motion + revisit memory + learned rotation (`src/d4bot/minimap.py`, `brain._nav_click`). Old random roam remains as fallback. |
| Death | Purple globe → false deaths; effectively disabled | Play-area saturation ≤ 14 for 1.5s (Helltide is 55–63, snow ~30). Optional revive-UI template. |
| Tools | — | `tools/check_captures.py` runs perception over `data/*.png`; `-Calibrate buff`, `-Calibrate death`, regions `minimap`, `buff_bar`. |
| Tests | 15 | 25. Includes a closed-loop sim: the bot explores a textured maze (≈95 of ~100 cells in 2 min), recovers when stuck, and learns a deliberate 25° rotation (→ 20°). |

Verified **offline only**: unit tests, the maze simulation, and the real screenshots in `data/`. Tracking on a real minimap capture with synthetic shifts: 18.7 of 20 px both axes.

## 3b. Live dry run (2026-10-23 12:45, Dahlgur Oasis Helltide, user playing, no input)

`.\run.cmd -MaxSeconds 30 -Snapshots data\dryrun` (snapshots save the overlay to disk instead of a window that could cover the game).

- 464 frames, 15.5 fps. Demon detected at start (buff 1.00); real demon→human at ~11s detected.
- Targets: every monster that had a health bar was boxed and targeted; nothing on the threat meter, labels, or UI. Monsters without a bar (undamaged) are ignored — the bot engages once auras/attacks put a bar on them.
- Minimap rays followed the real paths in every snapshot.
- **Found and fixed:** the demon buff number is a countdown and D4 blinks the icon before it expires → form flickered DEMON/HUMAN every ~0.5s. Now human requires the icon gone for `buff_off_confirm_s` (1.0s).
- **Found and fixed:** in dry runs the rotation learner used the human's movement (learned a bogus −26°). Learning and stuck detection now run only with input enabled.

## 3c. First run WITH input (2026-10-23 12:50, 60s) — two wrong assumptions found

- 517 actions, attacked real monster bars, never pressed `2`, no harm done. But only 2 roam clicks: half the run was spent "waiting out the demon cooldown".
- **The horned-skull buff is NOT demon form.** It is a stacking combat buff (likely "Infernal Hunger") that appears/disappears every few seconds while you stay demon. Earlier evidence was a coincidence (stacks decay when human). The user confirmed the real signal: **in demon form the skill icon frames burn orange**; human frames are plain. New `form.mode: glow` measures only the frame ring of slots 1, 2, 4 (icon art is orange in both forms); demon = ≥2 of 3 burning. Burning 0.31–0.49, plain 0.00–0.07. All 19 live-run snapshots → DEMON (buff method said HUMAN on 8). The dry run (3b) was also demon throughout; the "demon ended at 11s" reading there was the same buff mistake.
- **The real death screen is not grey.** User screenshot: darkened but coloured, "YOU HAVE DIED" banner, "Revive at Checkpoint" button, and the whole HUD gone. Death is now "HUD missing": the `1` skill-key label (`templates/hud_key1.png`) is fixed HUD art; present 0.97–1.00, missing 0.34–0.41. Missing → `NOHUD` (no input at once; also covers menus/cutscenes); missing 2s → `DEAD`, screenshot saved, run stops.
- An attempted live capture of the death screen grabbed the desktop because D4 was behind other windows; it was deleted unviewed. Capture only sees what is on top.

## 3d. Runs 2–3 with input: the "monsters" were the tiger; Subo's minimap dots fix targeting

- **Run 2 (12:58):** stood still 20s right-clicking one spot. It was the user's **summoned tiger** — the teal 6px bar is a *friendly* bar (tiger 6px, mercenary Subo 9px). The earlier "teal = monster bar" conclusion came from captures where that burning beast was the tiger. Teal removed from enemy colours. Regular monsters in this game show **no** health bars at all.
- **Run 3 (13:08, `combat.mode: pulse`, start human):** human detected (0.06) → pressed `2` once → demon confirmed (0.19) within 1s; stayed demon. But the cinder-counter "kill signal" fired 70× in 30s (the counter changed once, 1→2): its digits sit on a see-through background, so fire/terrain behind them changes the pixels. Result: attacked empty air and barely walked. Threat meter also too slow to use.
- **Fix — `combat.mode: hunt` (default):** the mercenary **Subo marks monsters on the minimap** (user's tip): yellow/orange dots = monsters, red dots + red skull = elites. `minimap.monster_packs` masks them (yellow H14–32 S≥150 V≥170; red S≥120 V≥185 — blocked Helltide red is V 106–129), groups dots within 14px into packs. Roam steers toward the nearest pack (elites weighted 2×); a pack within `fight_radius_px` 28 (elite 40) → stop, right-click toward it, linger 1s after it clears. A "pack" still there after 10s is treated as a static map icon and that spot is ignored 60s. On the user's Subo screenshot: 10 packs, 2 elite, skull at 22px → FIGHT aimed at it. Dots also count as walkable ground for the ray mask.
- Needs **Subo in the party**. `fight_radius_px` and `screen_per_minimap_px` are guesses; tune from the first hunt run. Overlay snapshots cannot be used to test dot detection (the overlay's red rays look like elite dots); use raw captures.

## 3e. Runs 4–7 with input (13:23–13:43): hunt mode works; four fixes

| Run | Result | Fix |
|---|---|---|
| 4 | Read demon as human after 2s, pressed `2` at 11s → knocked the user out of demon | **Skills on cooldown do not burn.** The bot's own `1`/`4` presses put 2 of the 3 glow slots on cooldown. Rule is now: demon = ANY of slots 1–4 burning (human frames 0.00–0.07 on every slot). Re-checked on every frame of every run: all correct. |
| 4 | Pressing `2` the instant focus returned did nothing | 1s settle after the game window regains focus. |
| 5 | Human→`2`→demon worked; 6 packs fought; "Cull Demons" event completed. 4 stuck events at the end walking into red ground toward packs on the far side | `line_is_clear`: packs whose straight line crosses blocked ground pull only 0.35× and cost 3× in goal choice; getting stuck while heading at a pack ignores it 20s. |
| 6 (first try) | Started with another (white) window on top: captured it, HUD "missing" → stopped as dead after 2s, no input sent | Foreground is checked before death; a covered window only pauses. The non-game capture was deleted unviewed. |
| 6 | 5 packs, stuck 2. Fought a "pack" 17px away for the full 10s | It was a **yellow star behind the player arrow** (the player's own marker). Dot blobs touching the near-white arrow are now dropped. |
| 7 | Best run: stuck 1, 67 moves, 5 packs, demon throughout | Packs sat 30–40 minimap px away and visibly on screen without being engaged → `fight_radius_px` 28→40 (elite 52); measured ~7.5 screen px per minimap px (aim scale 9→7.5). Not yet run with these values. |

Snapshots now also save `raw_*.jpg` (clean frames) next to `overlay_*.jpg`.

## 3f. Run 8 + loot (13:47)

- Run 8 (fight radius 40): 6 packs in the first 30s, much more fighting. Attacked a fixed point (746,1004) a few times: the red-bar detector hit the **orange-bordered item pickup icon**. Health-bar targeting is now OFF in hunt mode (monsters have no bars anyway).
- **The yellow star is a legendary/unique drop** (user). It was in runs 6-8 the whole time (the same item, never picked up). Stars are found by template match on the yellow mask (real 0.87-0.91, non-star <= 0.45), removed from monster dots, and reported as `MinimapReading.loot`.
- **Loot pickup:** monsters in range first; else a star within 140px pulls nav (2.5 weight; the pack goal cannot overwrite it); within 35px the bot clicks the nearest orange/gold item label (`Observation.loot_labels`: orange text on a dark label box; fire is the same orange but on bright background — 0 false labels in 76 fight frames). Not picked up in 8s (inventory full?) -> star ignored 120s. Overlay draws stars (white circles) and labels (white boxes). Not run live yet.

## 3g. Runs 9–11: all-label loot, human-form handling, 4-minute soaks (14:10–14:25)

- **All labels are loot** (user has a loot filter): white/blue/yellow/orange/gold text on D4's dark label box, within 650 screen px (a real label sat 524px away). Label search stops before the quest-tracker column (x > 0.78), which matched every frame. Anything not picked up in 5s is ignored 120s by world AND screen position (covers inventory-full, name plates like "Damned Soul", banners like "EVENT JOINED", floating "+1 Aberrant Cinder").
- Run timer now starts when the game first has focus (run 9 v1 was spent entirely unfocused).
- **Soak 10 (4 min):** ~29 packs, 8 loot clicks, stuck 3, no death. Demon dropped on its own in quiet areas; slot 2 was ready again well before 11s; pressing at 3s confirm was too tight; the bot idled in human form.
- **Fix — `form.act_while_human`:** while human (confirmed 1s), press `2` every 2s (on cooldown it does nothing; only a press in demon is harmful, and the any-burning-frame check has been right on every frame) and keep hunting/looting. Utility skills/potions still wait for demon.
- **Soak 11 (4 min):** started human → demon in 1s; **34 packs, 29 loot clicks, stuck 0**; one demon drop recovered in 6s. Four "dot did not clear in 10s": one checked was a gold **event objective icon** (Webbed Hatchery) next to the player — the guard ignored it and fighting continued.

- **Soak 12 (14:26):** stopped after 21s on "HUD gone" — the saved frame is a **NETWORK DISCONNECT** dialog, not a death (user's connection had been flaky). The bot stopped input correctly; nobody clicked Accept/reconnected. Before it, 18s in human form with slot 2 ready and `2` pressed every 2s without demon returning — most likely the connection was already dead; re-check on a healthy connection (same symptom once in run 4). Exit message now says "HUD gone (death, disconnect or menu)".

## 3h. Walk in before attacking + first 10-minute soak (15:10–15:20)

- User: attacking at ~300 screen px only suits long-range builds. New `combat.attack_range_px` (default 18 minimap px ≈ 135 screen px): a pack is engaged at `fight_radius_px` 40, but the bot walks toward it (clicks on the ground short of the pack, state `APPROACH`) and only attacks inside `attack_range_px`. Wall in the way → after `approach_timeout_s` 2.5s it attacks from where it is. Not set tighter than 18: dots closer than ~12px merge into the player arrow and get filtered. `linger_s` 1.5. For another character, set `attack_range_px` to its reach (≥ `fight_radius_px` = attack on engage).
- **Soak 14 (10 min, full length):** 84 packs, 43 loot clicks, stuck 6, demon the whole time, rotation within ±10°, no death/disconnect, 10.5 fps. 133 approaches → 112 fights, 21 packs already dead on arrival; fights start at median 16px (was ~38–40). Five "dot did not clear in 10s" at 12–21px (event icons / tough elites), each ignored and moved on.
- Soak 13 attempts were stopped by the user with F8 (deliberately, to report the attack-range issue).

## 3i. Recorded routes (built offline, not yet used live)

User: many Helltide spots are tight, so lead the bot into a big open area with a short recorded route, then let it roam. `src/d4bot/routes.py`:
- **Record** (`.\run.cmd -Record NAME`, no input sent): stand on the zone's waypoint, walk to a big open area, F8 saves `routes/NAME/` (`mosaic.png` stitched minimap, `covered.png`, `route.json` path). Frames are only added while the game is focused and the minimap readable. Dots, stars and the player arrow are painted out (`terrain_gray`) so only terrain is stitched/matched. Position = minimap motion, corrected each frame against the mosaic (sim: 4.5px drift over 1400px).
- **Play back** (`routes.enabled: true`): `RouteLocator` finds which recorded zone matches (global search once), then tracks locally; `RouteFollower` steers along the path (weight 2.0) and, at its end, roams within `leash_px` 140 (a route ending at its start loops instead). Fights, approach and loot still take priority.
- Off the recorded area it keeps the position by dead reckoning for ~1 min. Two sim-found bugs fixed: matching fell back to a global search and accepted look-alike terrain 800px away; and near the recording's edge matches snapped onto the edge. Now: no matching when the dead-reckoned position is off the recording, and no correction > 25px. Sim: position error median 3px / max 7px over 90s; after the route the bot stays within ~120px of the end (leash 80).
- Not yet used live: needs the user to record a zone. Doctor prints route status.

## 3j. Town routine (built offline, not yet run live)

`.\run.cmd -Town -EnableInput -IAcceptBanRisk` (`src/d4bot/town.py`), measured from the user's 24 screenshots (`.\run.cmd -Snap town`, F9 per step):
1. Not in Temis (zone-name template, town 0.64–1.00 / Helltide ≤0.44) → press `T`, wait up to 12.5s, retry once.
2. Walk right from the landing spot (click at 74%/44% of the screen) until the stash chest template (0.84–1.00 on screen, ≤0.67 elsewhere) is visible, click it, confirm the STASH header (1.00 open / ≤0.24 closed). The walk direction is an estimate from two screenshots — first thing to check live.
3. For each inventory tab (equipment, talismans, socketables, consumables, keys; active tab = red button): right-click every occupied cell (empty cells mean ~40 / std ≤7, items mean ≥79 or std ≥24). No progress → next stash tab; 3 tabs in a row taking nothing → item can't be stashed, counted in `left`.
4. **Before every right-click the stash header is re-checked** — a right-click with the stash closed would equip/use the item. Esc at the end only if the stash is open (Esc with nothing open opens the game menu).
Tests: `tests/test_town.py` drive a fake game built from the real screenshots (skipped if `data/town` is missing): full run, full stash tab, unmovable items, stash closing mid-run. Moves EVERYTHING, including the bookmarked equipment items the user had in the bag.

## 3k. Town routine WORKS live in Ichorfall (2026-10-23 23:34)

- Temis attempts 1–4 failed: a Helltide zone name scored 0.54 vs the Temis template (fixed: always press T, require a loading screen); the stash is off screen at the Temis portal landing, the chest template broke under different lighting, and walk clicks hit NPCs / went down the stairs. User switched the town to **Ichorfall**, where the stash is on screen at the landing spot (`TownConfig.town = "ichorfall"`, templates `ichorfall.png` / `chest_ichorfall.png`, Temis kept as `town="temis"`).
- **Live run:** T → loading → Ichorfall (10s) → stash found by the hover "Stash" label at (710,555) (the chest template missed: the landing spot was ~220px off from the screenshot) → **74 items moved** (equipment 3, talismans 15, socketables 12, consumables 18, keys 26), nothing left, 30s total. Step screenshots in `data/town_runs/20261023_233445/`.
- Also added (unused so far): `.\run.cmd -TeachStash` records the user's clicks from the landing to the stash (`templates/town/stash_path.json`); `-Town` replays them when present.

## 3l. Teleport to the Helltide via the Warplan (built offline, not yet run live)

User: the map's Warplan panel ("GREATER HELLTIDE HARVEST") has a Teleport button that goes to the next Warplan activity; the user set that to Helltide. `src/d4bot/warplan.py`: TAB (map) → click Teleport (template 0.99–1.00, elsewhere ≤0.31) → if "TELEPORT CONFIRMATION" appears, click Accept → loading screen → arrival confirmed by the "Helltide:" timer label next to the minimap (0.82–1.00 on all 465 Helltide frames, ≤0.34 elsewhere). Landing somewhere else → fails with "not in a Helltide (is the Warplan's next activity Helltide?)". Already in a Helltide → does nothing.
Commands: `.\run.cmd -Helltide -EnableInput -IAcceptBanRisk` (teleport only), add `-WithTown` to run town + stash first. Town and Helltide routines now share `_RoutineIO` (F8/focus gates, step screenshots in `data/town_runs/`). Tests: `tests/test_warplan.py` (fake game from `data/map/snap_*.png`).

- **Live (2026-10-24 00:05): works.** TAB → Teleport → no confirmation dialog → loading → landed at the Fool's Quarry waypoint, "Helltide:" timer found, 9s total.
- **No Helltide up** (user screenshot, not run by the bot): Teleport loads back to Ichorfall with "Sanctuary is at peace presently… please wait until the next Helltide invasion occurs". The routine would wait 30s for the timer and fail "not in a Helltide". Not tested live (missed the 5-minute gap). The farming loop should wait and retry instead.
- First live attempt (23:54) found a loading-art screen 5s after TAB (not black, so it wasn't recognised as loading). Loading now also = the emblem in the bottom-right corner (`templates/warplan/loading_emblem.png`, ≤0.55 on 1105 other frames); both routines wait out a loading screen before starting.

## 3m. Full cycle + routes per town (2026-10-24, in progress)

- `.\run.cmd -Cycle` (`cmd_cycle` in app.py): town + stash (`_run_town`) → Warplan teleport (`_run_helltide_teleport`) → `_farm(...)` with an `end_check` → repeat. Farming ends when the "Helltide:" timer has been gone 20s (HUD visible, alive) or after `--stash-every-min` (30 default). Death / disconnect / F8 end the cycle. No Helltide ("Sanctuary is at peace", back in Ichorfall) → retry every 60s for up to 20 min. Game loses focus → "Paused", waits for the user to click back in, retries the step.
- The farm loop moved from `cmd_run` into `_farm(config, input_enabled, ..., end_check)`; `cmd_run` just calls it.
- **The teleport lands in the town next to the Helltide**, not in it (live: Kyovashad). The "Helltide:" timer does not show there, so arrival now = loading screen done + not in Ichorfall. The bot needs a **recorded route per town** to walk out into the Helltide: `.\run.cmd -Record <town>` from the waypoint, walk to a big open area, F8. Routes auto-match by minimap (no town names needed). Recorded so far: **kyovashad** (waypoint → east road → open area; no false match on other towns' frames), **marowen** (waypoint already inside the Helltide → southeast road → big round open area; 96 points, 1148 frames). Landing with no matching route → cycle stops after 20s with "no recorded route for this town".
- `T` while standing in another town does nothing (live: Kyovashad) → the cycle skips the stash that round. `T` from the Helltide always goes to Ichorfall (user).
- User: steps were too fast → ~1–1.5s pauses between stash close, map open, Teleport click, Accept.
- Live cycle attempts so far: (1) app quit while waiting for focus; (2) user clicked away → misread as "no Helltide" (fixed: pause on focus loss); (3) started in Kyovashad, T did nothing (fixed: skip stash); (4) stash OK, teleport landed in Kyovashad but was judged a failure (fixed: arrival check). Next: a full live round.

- Live cycle 00:53: town + stash OK, teleport landed at a snowy waypoint (Desolate Highlands; the Helltide had moved) — judged before the HUD faded in, then the bot clicked the **waypoint's name label as loot**, which opened the map, and the hidden HUD read as death. Fixed: arrival waits for the HUD (`hud_key1` template) before judging; no loot clicks for 8s after landing (`brain.loot_hold_until`); in the cycle, "HUD gone" with the map open → Esc and keep farming.
- **No Helltide up, live 00:58:** clicking Teleport does nothing (no loading screen, chat "Sanctuary is at peace presently…"). The cycle now treats that, like landing back in Ichorfall, as "no Helltide": wait 60s, retry (up to 20 min).

- **First full live round (01:09):** T -> Ichorfall -> stash (bags already empty) -> Warplan teleport -> landed in the Helltide (9s) -> route **marowen located (0.99)** -> followed it -> farmed: 7 packs, 6 loot clicks, stuck 0 in 60s. Human for the first 12s after landing (demon cooldown), route walking continued meanwhile. User pressed F8 after 1 min. Not yet seen: the 5-min stash trip back and forth, a Helltide ending, a full hour.

- User: the bot got stuck attacking the **red sword icon** on the minimap (a map marker, not an enemy). Red blobs bigger than `nav.max_elite_dot_px` (60 px area; real elite dots are 11-24) are now ignored, plus any yellow blob touching one (the event icon's flame). This also drops the big red skull icon; only small red dots count as elites.

- **Live cycle 01:19 (after the sword fix):** stash -> teleport -> marowen route (0.99) -> 265s farming: **50 packs, 14 loot clicks, stuck 1**, demon drops recovered in 3-8s, a few uncleared dots dropped after 10s. User clicked away twice (paused correctly) then F8 at 4.5 min - just before the 5-min stash trip, which is still untested live.

- User: the bot got stuck on an **orange triangle objective icon** (with a small red dot attached). Measured 11k yellow blobs on live minimaps: monster dots 18-24 px; every blob over ~45 px was an icon (event-marker flames, the yellow tips of green-gem markers). Yellow blobs > `nav.max_monster_dot_px` (44) are now icons, and red dots touching them are dropped too. Test helper dots shrunk to real size (radius 3).

## 3n. Ideas taken from qqt_diablo (2026-10-24, built offline, not yet run live)

User asked what helps from https://github.com/qqtnn/qqt_diablo. It is a Lua plugin set for an **injected** loader (reads game memory, engine pathfinder, packets) - the opposite of this project's screen-only design and a far bigger ban risk, so no code was used, only ideas:
- **Path planning** (from its `piteer` explorer, which runs A* on the game's walkable grid). `minimap.PathField`: BFS over the minimap walkable mask on a 4px grid (`nav.path_planning`, `nav.path_cell_px`). Runs only when a pack or loot star is behind blocked ground; each gets `Pack.via` (furthest route point reachable in a straight line) and `Pack.path_px` (route length). Goal choice now costs the route length instead of "3x if blocked"; steering, approach clicks and loot walking head for the waypoint; stuck-at-pack check uses that direction. Overlay draws the route in green. Cost: ~0.2ms typical, ~6ms on a serpentine worst case. Sim (`test_hunt_walks_around_a_wall_to_reach_a_pack`): pack behind a wall with a gap to the side reached in 5.3s, stuck 0 (planning off: 18.1s, stuck 1). Packs only reachable via ground off the visible minimap still count as blocked.
- **Idle watchdog** (its Helltide script resets after a long time without casting). In `-Cycle`, no FIGHT/APPROACH/LOOT for `--idle-reset-min` (default 4, `-IdleResetMin` in run.ps1, 0 = off) ends the round like a stash trip: town -> teleport -> route.
- Not taken: its task-manager structure (one task per tick, `shouldExecute/Execute`) - worth doing before adding spawners / revive / potions, but a refactor of `CombatBrain.decide` needs live runs to trust. Melee "move slightly past the target" - not needed while approach works. Evade, health-based targeting, auto-repair API - need game memory.

## 3o. YOLO pipeline (2026-10-24, built offline, no real model yet)

Optional on-screen object detector, off by default (`detector:` in config.yaml). Full guide: `docs/YOLO.md`.
- **Collect:** `.\run.cmd -Collect <name>` - no input; ~1 frame/s while the game is in front with HUD visible, near-duplicates skipped -> `data/yolo/raw/<name>_<time>/`.
- **Label:** `.\run.cmd -Label` (`tools/yolo_label.py`) - drag boxes, keys 1-5 = monster/elite/loot/chest/spawner, Space = save+next; once a model exists, frames open pre-labelled by it.
- **Train:** `.\setup.cmd -Yolo` once (Ultralytics, CUDA torch if `nvidia-smi`, onnxruntime-directml), then `.\run.cmd -Train` - dataset split by **session** (`dataset.build_dataset`), YOLO11n, per-class precision/recall on held-out sessions, ONNX export (`simplify=False` so onnxruntime-directml is not clobbered) -> `models/d4.onnx` + `d4.classes.txt`; old model kept as `d4_<date>.onnx`.
- **Run:** `detector.py` loads the ONNX with onnxruntime (DirectML/CUDA/CPU) if installed, else OpenCV DNN; every 2nd frame; `Observation.detections`; overlay draws boxes; Doctor prints backend + what it sees. `detector.aim: true` aims FIGHT attacks at the detected monster/elite nearest the minimap estimate (within `aim_radius_px`). Nothing else in the brain uses detections yet.
- **Verified here:** synthetic dataset (3 sessions, 180 frames) -> `tools/yolo_train.py` on CPU -> val mAP50 0.995 -> exported ONNX loaded by `Detector` on OpenCV 4.14 and 5.0 and onnxruntime: boxes within 1-2px of truth, also on a 2560x1440 upscale. 30 ms per frame at 320 px on this server's CPU. Tests: `tests/test_detector.py` (decode/NMS/letterbox, labels, session split, dedupe, labeller, aim).
- **Not verified:** anything on real D4 frames; DirectML speed on the user's GPU; `setup.ps1 -Yolo` on Windows (CUDA wheel index cu128 -> cu126 -> CPU fallback).
- **Next:** user collects 300-500 frames over 3+ sessions and labels them; first model; check a dry run's overlays before `aim: true`.

## 3p. Step 3 live run on the new PC (2026-10-25 00:31, Kareka Floodplain): three fixes

New Helltide, no recorded route. 234s: 10 packs, 33 loot clicks, stuck 12, demon throughout, 10.8 fps. Ended early as "DEAD" (it was not).
- **False route match:** the global route search accepted kyovashad 0.45-0.62 and marowen 0.47 in this unrecorded zone and steered along a path that is not there. Real first fixes score 0.99. `RouteLocator.acquire_score` 0.8 for the first fix of a zone (local tracking still accepts 0.5).
- **Zone-name banner clicked as loot** (8x): the "ELDERWOOD GROVE" / "KAREKA FLOODPLAIN" banner shown when entering a sub-zone is boxed white words on dark, always centred at y 799 (0.555 H), 19px tall. Labels there with h <= 21 within 330px of centre are dropped; all banner hits in the run's 75 frames removed, every real item label (20-35px tall) kept.
- **Covered game -> false death:** another app's window came on top -> PAUSED, then DEAD 3s later (the HUD-gone timer kept running while covered). Now: after focus returns the HUD must stay missing `death.confirm_s` + 1s on its own; and `is_foreground` also requires that no other window covers most of the client area (`WindowFromPoint` at 5 points, >= 3 = covered; checked live: D4 behind another window -> covered=True). Test `test_covered_window_is_not_death_after_focus_returns`. Non-game captures deleted.
- Stuck 12 in 3.5 min: this zone is tight (narrow beige corridors between big red patches), and the false route pulled it around. Judge path planning again on the rerun.

## 3q. Red item labels; stairs note (2026-10-25)

- Run 2 missed a red "Amulet" label (user screenshot): `label_hsv` had no red. Added red H 0-5 / 174-180, S >= 150, V >= 170. Over 132 fire-heavy 1440p snapshots (path1 + path2): **0 extra labels** from red alone. Label size rule loosened 90px/4x -> 80px/3.5x (real "Amulet" text ~97px); still 20 labels on those frames, unchanged. Looser (55px/2.5x) added 16 junk hits (Helltide threat meter bars, "Subo" name plate, red ground cracks, floating "+Cinder") - don't go there. Test `test_red_label_is_loot_and_zone_banner_is_not`.
- User: in run 1 the bot had trouble going up a stair/ramp. Stuck spell 111-141s was in The Spiteful Rise (cliffs, ramps): on the minimap the ramps are thin broken beige strips between big red areas, and the false route (3p) was pulling it there. No fix yet (no clear capture of the stair); run 2 had stuck 0. If it recurs: `.
un.cmd -Snap stairs` + F9 while it is stuck, and note the time.

## 3r. Route search only right after landing (2026-10-25)

- Real Kyovashad waypoint fix 0.59 vs false mid-Helltide matches 0.45-0.62: score cannot separate them. Now `RouteLocator` searches globally only in the first `acquire_window_s` 30s after it is created (each farm round creates a new Vision = right after the teleport); after that only local tracking of an already found route. `acquire_score` back to 0.5. Plain farm (`cmd_run`, no -Cycle) runs with routes disabled: it starts inside the Helltide, where a recorded zone can only be a look-alike. Checked: the cycle-1 landing frame locates kyovashad (0.59) at the right spot (between the letter/heart icons and the stash on the mosaic). Test `test_route_is_only_searched_for_right_after_landing`.
- Note: the kyovashad recording does not start on the waypoint (path[0] is 183px SE of it; the waypoint is near path index 17). The follower picks the nearest path point, so it still works.

## 3s. Simple route play: fight on the way, loop the circuit (2026-10-25)

- Cycle "Helltide ended" only counts once the Helltide timer was seen this round; never seen in 4 min -> "Helltide ended (timer never seen in 4 min)" (new round).
- No loot clicks until the round's first pack fight when a route is active (towns have no monsters; Kyovashad NPC name plates were clicked). Packs on the route ARE fought (user: "you will die by the time you walk there").
- `routes.leash_px` 140 -> 0: after a one-way route the bot roams freely (user).
- Looped routes: `find_loop_start` (computed at load, no re-record needed) - if the route ends within 40px of an earlier point (>= 50 points back), the bot loops from that point instead of walking back to the town start. kyovashad and marowen stay one-way. Tests: `test_route_fights_packs_on_the_way_but_clicks_no_labels`, `test_long_route_loops_its_circuit_not_back_to_town`.
- How to record a long route: stand on the waypoint, `.un.cmd -Record <town>`, click into the game, walk out to the Helltide, walk the circuit you want farmed, and finish the circuit where it began (within ~40 minimap px), F8.
- Bag full: not detected; the cycle still stashes every 30 min (`--stash-every-min`).

### Where it stands (end of 2026-10-24 night session)
- Works live: town + stash (Ichorfall), Warplan teleport, route recognition + following (marowen 0.99), hunting/looting (4.5 min: 50 packs, 14 loot, stuck 1), focus pause, no-Helltide retry.
- Not yet seen live: the 5-min stash trip and return, a Helltide ending, a full hour; the icon fixes (sword, triangle) after the last run; path planning and the idle reset (3n). On the next run watch the overlay for green route lines and `goal ...px (route ...px)` in the log.
- Routes recorded: kyovashad, marowen. Other Helltide towns need `.\run.cmd -Record <town>` the first time they come up (the cycle stops with "no recorded route for this town").
- **Next ideas (user):** the green-leaf **spawner icons** on the minimap — touching one makes monsters spawn nonstop around it; the bot could walk to one, interact, and fight there (may be seasonal). Then: auto-revive after death; health-based potions.

## 4. Not verified / do not trust yet

- **Run with input.** Not done yet. Start with `-EnableInput -IAcceptBanRisk -MaxSeconds 60 -Snapshots data\live1`. Watch: it presses `2` only after the buff has been gone ~11s, it walks along paths, `rot` stays near 0, stuck count stays low.
- **Demon re-entry live.** The glow gate's press-2-after-11s path has not happened live yet (the user stayed demon). Watch the first real demon drop.
- **Death stop live.** HUD-missing detection is built from the user's death screenshot and tested synthetically; not yet seen firing on a real death. On the next death check that `data/death_*.png` was saved.
- **Minimap scale/speed.** `stuck_px 3.0` over `stuck_window_s 1.6` is a guess at walking speed in minimap pixels. Check `stuck=` in the 30s stats: high counts while moving = lower `stuck_px`.
- **Click → movement mapping.** Assumes the player is at (0.50, 0.48) of the screen and the minimap is camera-aligned. The rotation learner covers modest errors.
- **Red monster bars** (if some monsters use them) and **elite bars** (may be taller than 8px) are untested; only teal 6px bars were seen.
- **Non-Helltide zones:** the walkable colours are tuned for the Helltide parchment; elsewhere nav may misread and fall back to random.

## 5. Still missing

1. **Loot pickup.** No captures of item labels/beams exist. Gold and cinders appear to auto-collect (`+1 Aberrant Cinder` text in captures). Needs screenshots with loot on the ground before building.
2. **Health globe / potion by HP.** Purple cosmetic globe; still a blind 8s timer. Needs low-health captures.
3. **Chests / events / Helltide boss.** None.
4. **Revive + return.** On death the run just ends.
5. **YOLO detector:** pipeline built (3o); needs collected + labelled frames for a first model.

## 6. File map

```
config.yaml               tuning (nav:, death:, form: buff, enemy: teal/exclude)
src/d4bot/
  app.py                  CLI, run loop, death stop, stats, error resilience
  capture.py              mss + window/DPI
  vision.py               globes, enemy bars, skill icons, demon buff, screen state
  minimap.py              walkable mask, ray clearance, motion tracking
  brain.py                buff gate, toggle gate, combat, minimap nav, random roam
  controller.py           pydirectinput + F8/foreground gates
  calibrate.py            regions / skill / form / buff / death capture
  overlay.py              debug HUD incl. minimap rays + heading
templates/demon_buff.png  demon buff icon at 2560x1440
tools/check_captures.py   offline perception check over data/*.png
tests/                    25 tests (test_nav.py = nav sim, buff gate, death)
data/                     screenshots copied from the OneDrive folder (gitignored)
```

## 7. Safety

- Botting violates Blizzard's terms; ban possible. Do not run unattended.
- F8 stops input. Input only goes to the foreground D4 window.
- Start outside town (town disables skills; the toggle backs off rather than spamming).
