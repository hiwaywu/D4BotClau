# HANDOFF — d4-visual-bot

**Status:** full Helltide cycle built: town + stash → Warplan teleport → follow recorded route → farm → repeat. Each part works live on its own; the full cycle has not yet completed a round live. Not safe to leave unattended.
**Last updated:** 2026-10-24
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
- **The teleport lands in the town next to the Helltide**, not in it (live: Kyovashad). The "Helltide:" timer does not show there, so arrival now = loading screen done + not in Ichorfall. The bot needs a **recorded route per town** to walk out into the Helltide: `.\run.cmd -Record <town>` from the waypoint, walk to a big open area, F8. Routes auto-match by minimap (no town names needed). Recorded so far: **kyovashad** (waypoint → east road → open area; no false match on other towns' frames). Landing with no matching route → cycle stops after 20s with "no recorded route for this town".
- `T` while standing in another town does nothing (live: Kyovashad) → the cycle skips the stash that round. `T` from the Helltide always goes to Ichorfall (user).
- User: steps were too fast → ~1–1.5s pauses between stash close, map open, Teleport click, Accept.
- Live cycle attempts so far: (1) app quit while waiting for focus; (2) user clicked away → misread as "no Helltide" (fixed: pause on focus loss); (3) started in Kyovashad, T did nothing (fixed: skip stash); (4) stash OK, teleport landed in Kyovashad but was judged a failure (fixed: arrival check). Next: a full live round.

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
5. **Optional YOLO**, see `docs/YOLO.md`. Probably unnecessary now that bars are detected correctly.

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
