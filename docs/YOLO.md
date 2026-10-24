# YOLO detector

An optional object detector that looks at the **game view itself** instead of the
minimap or the HUD. It is built into the bot and off by default.

## What it does for this bot

Today the bot knows where monsters are only from **Subo's minimap dots**. That works,
but it is coarse and leaves gaps:

| Today (colour rules) | With a trained detector |
|---|---|
| Aim = a guess: the dot's minimap direction times ~7.5 screen px per minimap px | `detector.aim: true` aims at the monster's actual body on screen |
| Monsters are only known as minimap dots | Monsters seen on screen too (hunting still starts from Subo's dots; using boxes alone is a later step) |
| Elites = small red minimap dots | `elite` boxes on the monster itself |
| Loot = minimap stars + item-name text colour (false hits on banners, name plates) | `loot` boxes on the item on the ground |
| Chests, the green spawners, events: invisible | `chest` / `spawner` classes, ready for the spawner idea in HANDOFF |

What it does **not** do: walls/terrain (still the minimap), deciding where to go (still
the brain), or anything without labelled examples. It only knows what you teach it.

## The loop

```
.\setup.cmd -Yolo                 once: Ultralytics + torch (CUDA if an NVIDIA GPU) + onnxruntime-directml
.\run.cmd -Collect helltide       play normally; saves ~1 frame/s (skips duplicates, menus, loading). F8 stops. No input sent.
.\run.cmd -Label                  draw boxes (see below)
.\run.cmd -Train                  builds the dataset, trains, exports models\d4.onnx
config.yaml: detector: enabled: true
.\run.cmd -Doctor                 "detector: d4.onnx via ...; sees monster 0.87, ..."
.\run.cmd -MaxSeconds 60 -Snapshots data\yolo_check   dry run: boxes drawn on data\yolo_check\overlay_*.jpg
config.yaml: detector: aim: true  only when the boxes look right
```

Repeat: collect more (especially where it was wrong), `-Label` (frames now open with the
model's guesses already drawn - fix them instead of drawing from scratch), `-Train`.
Each training keeps the previous model as `models/d4_<date>.onnx`.

Frames from a bot run also work: copy a `-Snapshots` folder's `raw_*.jpg` into
`data\yolo\raw\<name>\`.

## Labelling

`.\run.cmd -Label` opens the first frame without a label file.

- **Left-drag**: box with the current class. **Right-click** a box: delete it.
- **1-5**: class - `monster`, `elite`, `loot`, `chest`, `spawner` (shown at the top).
- **Space / Enter**: save, next frame. **b**: back. **u**: undo. **c**: clear. **s**: skip. **q**: quit.

Rules that make the model good:
- Box **every** instance of a class in a frame, or none of that class at all. A monster
  left unboxed teaches the model "that is not a monster".
- Frames with nothing in them (town, empty ground, red Helltide scenery, fire, the
  tiger/Subo) are saved with no boxes. They are what stops false alarms. Keep ~20%.
- Box the monster's body, not its shadow or the effect around it.
- Do not box the player, pets or Subo.

## How much

- **Start: 300-500 labelled frames** from 3+ sessions -> first model. Expect it to be
  rough; its job is to speed up labelling.
- **Useful: 1,000-2,000 frames**, several Helltide zones, day/night, crowded and quiet.
- Rare classes (chest, spawner) need ~100+ boxes each before they mean anything.
  Collect near them on purpose.

Labelling time is the real cost: roughly a frame every 10-20 s once the model pre-labels.

## Checking it

`-Train` prints precision/recall per class on **validation sessions** - whole sessions it
never trained on, so the score predicts a new run. Aim for recall > 0.8 on `monster`
before turning on `aim`. Then watch a dry run's overlays: boxes on scenery = add those
frames as empty examples; missed monsters = label more like them.

## Speed

`input_size: 640`, YOLO nano. The bot runs it every 2nd frame (`every_n_frames`).

- With `onnxruntime-directml` (installed by `-Yolo`): runs on any DirectX 12 GPU, much faster than the CPU (not measured on your PC yet).
- Without it: OpenCV on the CPU, ~50-150 ms per run - usable but slows the loop.
  Doctor shows which backend loaded.

Training: about an hour for 1,000 frames on an NVIDIA GPU; CPU works for a few hundred
frames but is slow (hours).

## Files

```
src/d4bot/detector.py    load models/d4.onnx (OpenCV DNN or onnxruntime), letterbox, decode, NMS
src/d4bot/dataset.py     frame dedupe, YOLO label files, session-based train/val split
tools/yolo_label.py      the labelling window
tools/yolo_dataset.py    build data/yolo/dataset + data.yaml (run by -Train)
tools/yolo_train.py      Ultralytics training + ONNX export -> models/d4.onnx, d4.classes.txt
data/yolo/raw/<session>/ collected frames + their .txt labels (not in git)
runs/                    training runs (not in git)
```

Ultralytics is AGPL-3.0 licensed - fine for personal use; it is only needed to train,
not to play.

## Why not a chat AI / VLM in the loop

Too slow (hundreds of ms to seconds per frame), costs per call, and gives different
answers for the same frame. Fine offline (reading logs, writing code); not for 10-20
decisions per second.
