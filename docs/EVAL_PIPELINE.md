# Eval Pipeline

Quality evaluation infrastructure for generation and edit outputs. Two layers: a fast local layer ($0, <100ms) and a deep Claude Vision judge layer (~$0.02/eval). Eval never blocks the workflow.

## Quick Start

### 1. Set `EVAL_MODE` in your environment

```bash
# In .env or environment variables:
EVAL_MODE=off    # Default. No eval runs, zero overhead.
EVAL_MODE=fast   # Runs CLIP/SSIM/artifact detection locally. $0 cost, <100ms.
EVAL_MODE=full   # Fast layer + Claude Vision judge on flagged results. ~$0.02/eval.
```

### 2. Install eval dependencies (for `fast` or `full` mode)

```bash
cd backend
pip install -e ".[eval]"
```

This installs `open-clip-torch`, `torch`, and `scikit-image`. These are gated behind `try/except` — if missing, the fast layer returns neutral scores (0.5) instead of crashing.

### 3. Set API keys (for `full` mode only)

```bash
ANTHROPIC_API_KEY=sk-ant-...   # Required for deep eval (Claude Sonnet judge)
```

That's it. The pipeline runs automatically on every `generate_designs` and `edit_design` activity call.

## How It Works

```
generate_designs() / edit_design()
  └── _maybe_run_eval() / _maybe_run_edit_eval()
        ├── if EVAL_MODE == "off" → return immediately
        ├── if EVAL_MODE == "fast" or "full":
        │     └── run_fast_eval() → FastEvalResult
        │           ├── CLIP text-image alignment (does image match the brief?)
        │           ├── CLIP image-image similarity (is it the same room?)
        │           ├── Edge-SSIM (are walls/windows/doors preserved?)
        │           └── Annotation artifact detection (edit mode only)
        ├── if EVAL_MODE == "full" AND (flagged OR 20% random sample):
        │     └── evaluate_generation() / evaluate_edit() → deep eval
        │           └── Claude Sonnet scores against rubric (100pts / 50pts)
        └── append_score() → eval_history.jsonl
```

### Generation eval

Runs on each generated option (typically 2). Fast layer scores all options; deep layer only fires when:
- Any fast metric is below threshold (flagged), OR
- 20% random sample (catches issues the fast layer misses)
- A `DesignBrief` is available (deep eval needs it for rubric scoring)

### Edit eval

Runs on each edit result. Same fast+deep pattern, plus annotation artifact detection (checks for leaked red/blue/green circle markers in the output).

### Error handling

Eval is wrapped in `try/except` at the top level. If anything fails (missing deps, API errors, timeouts), it logs a warning and returns. The activity always succeeds regardless of eval outcome.

## Score Tracking

Every eval run appends a JSONL record to `eval_history.jsonl`:

```json
{
  "timestamp": "2026-02-14T12:00:00+00:00",
  "scenario": "generation_option_0",
  "prompt_version": "v2",
  "fast_eval": {
    "clip_text_score": 0.28,
    "clip_image_score": 0.85,
    "edge_ssim_score": 0.42,
    "composite_score": 0.52,
    "has_artifacts": false,
    "needs_deep_eval": false
  },
  "deep_eval": {
    "total": 78,
    "tag": "GOOD"
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

### Viewing history

```python
from pathlib import Path
from app.utils.score_tracking import load_history

records = load_history(Path("eval_history.jsonl"), scenario="generation_option_0")
```

## Prompt Versioning

Generation and room preservation prompts are versioned for A/B testing.

### Current versions

Manifest at `backend/prompts/prompt_versions.json`:

```json
{
  "generation": {"active": "v2", "previous": "v1"},
  "room_preservation": {"active": "v2", "previous": "v1"},
  "edit": {"active": "v1"}
}
```

### How it works

`generate_designs` calls `load_versioned_prompt("generation")` which:
1. Reads the manifest to find active version (`v2`)
2. Loads `prompts/generation_v2.txt`
3. Falls back to `prompts/generation.txt` if versioned file missing

### Creating a new prompt version

1. Copy the current prompt: `cp prompts/generation_v2.txt prompts/generation_v3.txt`
2. Edit `generation_v3.txt` with your changes
3. Update `prompt_versions.json`:
   ```json
   {"generation": {"active": "v3", "previous": "v2"}}
   ```
4. Run with `EVAL_MODE=full` and compare scores between v2 and v3 in `eval_history.jsonl`

### Rollback

Set `"active"` back to the previous version in `prompt_versions.json`. No code changes needed.

## Fast Eval Metrics

| Metric | Range | Threshold | What it measures |
|--------|-------|-----------|------------------|
| CLIP text-image | 0-1 | 0.20 | Does the image match the design brief? |
| CLIP image-image | 0-1 | 0.70 | Is it still the same room? |
| Edge-SSIM | 0-1 | 0.30 | Are walls/windows/doors preserved? |
| Composite | 0-1 | 0.40 | Weighted average (35%/35%/30%) |
| Artifacts | bool | any | Annotation markers leaked into output? |

If any metric falls below its threshold, `needs_deep_eval` is set to `True`.

When dependencies are missing, all scores return 0.5 (neutral) instead of failing.

## Deep Eval Rubrics

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

You can run eval outside the activity pipeline:

```python
from PIL import Image
from app.utils.image_eval import run_fast_eval

original = Image.open("room_photo.jpg")
generated = Image.open("redesign.png")

result = run_fast_eval(generated, original)
print(f"Composite: {result.composite_score}")
print(f"Needs deep eval: {result.needs_deep_eval}")
```

```python
import asyncio
from app.activities.design_eval import evaluate_generation

result = asyncio.run(evaluate_generation(
    original_photo_url="https://...",
    generated_image_url="https://...",
    brief=my_design_brief,
))
print(f"Score: {result.total}/100 ({result.tag})")
for c in result.criteria:
    print(f"  {c.name}: {c.score}/{c.max_score}")
```

## File Map

| File | Purpose |
|------|---------|
| `app/utils/image_eval.py` | Fast eval layer (CLIP, SSIM, artifacts) |
| `app/activities/design_eval.py` | Deep eval layer (Claude Vision judge) |
| `app/utils/score_tracking.py` | JSONL score history + regression detection |
| `app/utils/prompt_versioning.py` | Versioned prompt loading |
| `app/config.py` | `eval_mode` setting |
| `prompts/prompt_versions.json` | Active prompt version manifest |
| `tests/eval/` | Eval test suite |
