# YOLO, LLMs, and other AI tools: what actually fits

## The short answer

- **YOLO (a real-time object detector):** yes, it can work, especially for enemy bodies, elites, loot beams, and event objects. It is not magic and it is not the first thing to build.
- **A chat LLM / vision-language model (GPT, Claude, Gemini, local VLM):** wrong tool for the control loop. Too slow, too expensive, too inconsistent at 20 decisions per second. Useful offline for labeling help, log analysis, and writing code.
- **Classic OpenCV:** the best first tool for health/resource globes, skill cooldowns, enemy **health bars**, and UI anchors.
- **A small classifier:** good if you only need “is this icon ready?” or “is this a loot beam?” — cheaper than a detector.
- **Reinforcement learning:** not recommended. You would need a fast simulator or a huge amount of risky live play, and the reward is hard to define.

## Why health bars beat enemy bodies

An enemy health bar is a small horizontal red/orange rectangle with a dark border. A few HSV thresholds plus contour shape filtering can find it at 20–60 fps on any CPU. No labels, no GPU, no training.

A YOLO model sees the whole 3D scene: mob bodies at different distances, effects, terrain, other players, and the Helltide color palette. It can absolutely learn this, but you must teach it with thousands of labeled examples from **your** camera distance, resolution, HUD scale, and lighting.

## When YOLO is worth it

Add a detector when you need a class that color/shape cannot separate:

| Target | OpenCV first? | YOLO useful? |
|---|---|---|
| Health/resource globe | Yes, HSV fill | No |
| Skill cooldown | Yes, template match | No |
| Demon-form state | Yes, calibrated HUD glow | No |
| Enemy health bar | Yes, thin red bar | Sometimes, for clutter |
| Enemy body / elite | No | **Yes** |
| Loot beam / item label | Partial (color) | **Yes** |
| Event altar / objective | No | **Yes** |
| Minimap player arrow | Yes, color/shape | Could be, but classic CV is easier |
| Terrain / walls | No | Segmentation model, much harder |

A practical hybrid:

```
OpenCV: globes, cooldowns, death, minimap
YOLO:   enemies/elites/loot/events (only when needed)
Brain:  fuse both into one Observation
```

## The part nobody mentions: labeling

For a useful D4 detector you need roughly:

- **1,000–3,000 frames** from real gameplay (not screenshots from a guide).
- Frames sampled every 0.5–2 seconds, including combat, travel, town, death, and Helltide.
- Bounding boxes for 3–6 classes. One class (`enemy`) is enough to start.
- A labeling tool: Label Studio, CVAT, or Roboflow.
- A train/val split by **session**, not by frame, so near-duplicate frames do not leak into validation.

At 5–15 seconds per box and several boxes per frame, 1,500 frames is a real weekend of labeling. That is the actual cost. Training itself is an hour on a modest GPU.

## Compute and latency

- YOLO nano/small at 640×640: often 30–60+ fps on an RTX 3060/4060; ~5–15 fps on CPU.
- At 960×960, small enemies are easier to detect but latency rises.
- Add capture (2–5 ms), pre/post-processing (3–10 ms), and the decision loop.
- Keep the control loop at 15–20 Hz; a detector at 10 Hz is fine if you track boxes between inferences.

Do not run a 7B vision-language model per frame. A single VLM call can take hundreds of milliseconds to seconds and cost money; it also gives different answers for the same frame.

## Integration plan for this repo

The code already separates perception (`vision.py`) from decisions (`brain.py`). To add YOLO:

1. Add `ultralytics` and `onnxruntime` (or `torch`) as optional dependencies.
2. Create `src/d4bot/detector.py`:

```python
class Detector:
    def __init__(self, model_path: str, conf: float = 0.35):
        from ultralytics import YOLO
        self.model = YOLO(model_path)
        self.conf = conf

    def detect(self, frame):
        result = self.model.predict(frame, conf=self.conf, verbose=False)[0]
        boxes = []
        for box in result.boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            boxes.append({
                "class": result.names[int(box.cls[0])],
                "conf": float(box.conf[0]),
                "bbox": (int(x1), int(y1), int(x2 - x1), int(y2 - y1)),
            })
        return boxes
```

3. Convert detections to the same `Enemy` dataclass (or add a `kind` field).
4. Fuse with the health-bar detections: prefer bars for targeting, bodies for “there is something over there.”
5. Add a `--detector` flag and a confidence threshold; keep OpenCV as the fallback.
6. Log every detection with frame/timestamp so you can replay mistakes.

## Training recipe (when you get there)

1. Record 20–30 minutes of Helltide at your final resolution/HUD.
2. Extract 1 frame/second; deduplicate near-identical frames.
3. Label 500 frames first, train a nano model, inspect failures.
4. Add hard negatives: red barrels, blood effects, meteors, town NPCs, other players, UI.
5. Label 1,500–3,000 frames total; retrain.
6. Validate on a **different session** and a **different Helltide zone**.
7. Measure:
   - recall of enemies you actually attack,
   - false positives per minute,
   - detection latency,
   - performance at night/day and in different biomes.
8. Export ONNX for deployment if Ultralytics overhead matters.

## The honest recommendation

Build the OpenCV version first. It gives you a working combat assist in an evening, and it tells you whether the hard part is perception or movement. If enemy bars are good enough, you may never need YOLO. If they are not, you will have a labeled dataset and a baseline to beat — which is exactly the position you want before training.

“Randomly attack and run” is a behavior, not a perception strategy. YOLO will not fix bad movement; it only tells you what is on screen.
