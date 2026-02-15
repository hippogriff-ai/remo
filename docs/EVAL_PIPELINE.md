# Eval Pipeline

Quality evaluation infrastructure for generation and edit outputs. Single-layer VLM (Claude Vision judge) architecture with local artifact detection. Eval never blocks the workflow.

## Quick Start

### 1. Set `EVAL_MODE` in your environment

```bash
# In .env or environment variables:
EVAL_MODE=off   # Default. No eval runs, zero overhead.
EVAL_MODE=on    # Runs artifact detection + Claude Vision judge. ~$0.02/eval.
```

### 2. Install eval dependencies (for artifact detection)

```bash
cd backend
pip install -e ".[eval]"
```

This installs `opencv-python-headless` for annotation artifact detection. If missing, artifact detection is skipped (returns clean).

### 3. Set API keys

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Required for VLM eval (Claude Sonnet judge)
```

That's it. The pipeline runs automatically on every `generate_designs` and `edit_design` activity call.

## How It Works

```
generate_designs() / edit_design()
  └── _maybe_run_eval() / _maybe_run_edit_eval()
        ├── if EVAL_MODE == "off" → return immediately
        ├── if EVAL_MODE == "on":
        │     ├── run_artifact_check() → ArtifactCheckResult
        │     │     └── OpenCV HoughCircles (red/blue/green annotation markers)
        │     └── evaluate_generation() / evaluate_edit() → VLM eval
        │           └── Claude Sonnet scores against rubric (100pts / 50pts)
        │           └── Diagnostic scores (instruction_adherence, spatial_accuracy)
        └── append_score() → eval_history.jsonl
```

### Generation eval

Runs on each generated option (typically 2). For each option:
1. **Artifact check**: OpenCV detects leaked annotation markers (red/blue/green circles)
2. **VLM eval**: Claude Sonnet scores against 100-point rubric + 2 diagnostic scores
3. **Score tracking**: Results appended to JSONL history

The VLM receives the original photo, generated image, design brief, generation prompt, and room context (LiDAR data) for comprehensive evaluation.

### Edit eval

Runs on each edit result. Same artifact check + VLM pattern, scored against a 50-point rubric.

### Error handling

Eval is wrapped in `try/except` at the top level. If anything fails (missing deps, API errors, timeouts), it logs a warning and returns. The activity always succeeds regardless of eval outcome.

## Score Tracking

Every eval run appends a JSONL record to `eval_history.jsonl`:

```json
{
  "timestamp": "2026-02-15T12:00:00+00:00",
  "scenario": "generation_option_0",
  "prompt_version": "v5",
  "vlm_eval": {
    "total": 87,
    "tag": "EXCELLENT",
    "photorealism": 13,
    "style_adherence": 14,
    "color_palette": 9,
    "room_preservation": 18,
    "furniture_scale": 9,
    "lighting": 9,
    "design_coherence": 9,
    "brief_compliance": 5,
    "keep_items": 4,
    "instruction_adherence": 8,
    "spatial_accuracy": 4
  },
  "artifact_check": {
    "has_artifacts": false,
    "artifact_count": 0
  },
  "model": "gemini-3-pro-image-preview"
}
```

### Regression detection

```python
from pathlib import Path
from app.utils.score_tracking import detect_regression

result = detect_regression(
    history_path=Path("eval_history.jsonl"),
    scenario="generation_option_0",
    latest_total=65,
    window=5,       # compare against last 5 runs
    threshold=10,   # 10-point drop = regression
)
# result = {"is_regression": True, "rolling_avg": 78.4, "delta": -13.4, "window_size": 5}
```

Regression detection reads from `vlm_eval.total`, falling back to `deep_eval.total` for backward compatibility with older records.

### Viewing history

```python
from pathlib import Path
from app.utils.score_tracking import load_history

records = load_history(Path("eval_history.jsonl"), scenario="generation_option_0")
```

## Prompt Versioning

Generation, room preservation, and edit prompts are versioned for A/B testing.

### Current versions

Manifest at `backend/prompts/prompt_versions.json`:

```json
{
  "generation": {"active": "v5", "previous": "v2"},
  "room_preservation": {"active": "v4", "previous": "v5"},
  "edit": {"active": "v5", "previous": "v1"}
}
```

### How it works

`generate_designs` calls `load_versioned_prompt("generation")` which:
1. Reads the manifest to find active version (`v5`)
2. Loads `prompts/generation_v5.txt`
3. Falls back to `prompts/generation.txt` if versioned file missing

### Creating a new prompt version

1. Copy the current prompt: `cp prompts/generation_v5.txt prompts/generation_v6.txt`
2. Edit `generation_v6.txt` with your changes
3. Update `prompt_versions.json`:
   ```json
   {"generation": {"active": "v6", "previous": "v5"}}
   ```
4. Run with `EVAL_MODE=on` and compare scores between v5 and v6 in `eval_history.jsonl`

### Rollback

Set `"active"` back to the previous version in `prompt_versions.json`. No code changes needed.

## Artifact Detection

Local OpenCV-based detection of annotation markers that leak into generated/edited images.

| Marker | HSV Range | What it detects |
|--------|-----------|-----------------|
| Red circles | H:0-10/160-180, S>100, V>100 | Red annotation circles |
| Blue circles | H:100-130, S>100, V>100 | Blue annotation circles |
| Green circles | H:35-85, S>100, V>100 | Green annotation circles |

Detection uses HoughCircles with configurable min/max radius. When OpenCV or NumPy are unavailable, artifact detection is skipped (returns clean).

## VLM Eval Rubrics

### Generation (100 points, 9 criteria)

| Criterion | Max | What it scores |
|-----------|-----|----------------|
| Photorealism | 15 | Real photo vs obvious AI |
| Style Adherence | 15 | Matches requested style |
| Color Palette | 10 | Brief colors with proper proportions |
| Room Preservation | 20 | Walls/windows/doors unchanged |
| Furniture Scale | 10 | Proportional to room |
| Lighting | 10 | Realistic shadows, consistent sources |
| Design Coherence | 10 | Unified design vision |
| Brief Compliance | 5 | All constraints met |
| Keep Items | 5 | Preserved items still present |

Tags: EXCELLENT (85+), GOOD (70+), ACCEPTABLE (55+), WEAK (40+), FAIL (<40)

### Generation diagnostics (reported separately, not in 100-point total)

| Diagnostic | Max | What it scores |
|------------|-----|----------------|
| Instruction Adherence | 10 | Generation prompt directives followed |
| Spatial Accuracy | 5 | Furniture sizes and room proportions match room context |

### Edit (50 points, 5 criteria)

| Criterion | Max | What it scores |
|-----------|-----|----------------|
| Edit Fidelity | 15 | Annotated regions changed correctly |
| Preservation Fidelity | 15 | Unannotated regions unchanged |
| Artifact Cleanliness | 10 | No annotation markers in output |
| Seamless Blending | 5 | Edits blend naturally |
| Instruction Accuracy | 5 | Matches user's text instruction |

Tags: EXCELLENT (42+), GOOD (35+), ACCEPTABLE (27+), WEAK (20+), FAIL (<20)

## Standalone Usage

```python
from PIL import Image
from app.utils.image_eval import run_artifact_check

generated = Image.open("redesign.png")
result = run_artifact_check(generated)
print(f"Has artifacts: {result.has_artifacts}")
print(f"Artifact count: {result.artifact_count}")
```

```python
import asyncio
from app.activities.design_eval import evaluate_generation

result = asyncio.run(evaluate_generation(
    original_photo_url="https://...",
    generated_image_url="https://...",
    brief=my_design_brief,
    generation_prompt="...",
    room_context="...",
))
print(f"Score: {result.total}/100 ({result.tag})")
for c in result.criteria:
    print(f"  {c.name}: {c.score}/{c.max_score}")
print(f"Instruction adherence: {result.diagnostics.get('instruction_adherence')}")
print(f"Spatial accuracy: {result.diagnostics.get('spatial_accuracy')}")
```

## Cost

- Artifact detection: $0 (local OpenCV)
- VLM eval: ~$0.02/eval (Claude Sonnet, 2 images + rubric)
- Per project (4 options): ~$0.08
- Per A/B session (10 runs): ~$0.40

## File Map

| File | Purpose |
|------|---------|
| `app/utils/image_eval.py` | Artifact detection (OpenCV HoughCircles) |
| `app/activities/design_eval.py` | VLM eval layer (Claude Vision judge) |
| `app/utils/score_tracking.py` | JSONL score history + regression detection |
| `app/utils/prompt_versioning.py` | Versioned prompt loading |
| `app/config.py` | `eval_mode` setting |
| `prompts/prompt_versions.json` | Active prompt version manifest |
| `tests/eval/` | Eval test suite |
