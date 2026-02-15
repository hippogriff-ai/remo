# Eval Guide for Prompt Changes

How to know if a prompt change is good or bad. This is the protocol for any agent modifying prompts in the Remo pipeline.

## The Rule

**Every prompt change must produce measurable evidence of improvement or non-regression before it ships.** "It looks better to me" is not evidence. Numbers from the eval harness are.

---

## Two Pipelines, Two Eval Paths

Remo has two independent prompt systems with separate eval harnesses:

| Pipeline | What it does | Prompt files | Eval harness |
|----------|-------------|-------------|-------------|
| **Intake agent** | Chat conversation → DesignBrief | `specs/PROMPT_T3_AI_AGENTS.md` (system prompt) | `intake_eval.py` → 100pt brief rubric + 25pt conversation rubric |
| **Image generation** | DesignBrief + photos → room redesign | `prompts/generation_*.txt`, `prompts/room_preservation_*.txt`, `prompts/edit*.txt` | `image_eval.py` (fast, $0) + `design_eval.py` (deep, ~$0.02) → scored to `eval_history.jsonl` |

Pick the right eval path for the prompt you're changing.

---

## Path A: Intake Agent Prompt Changes

### What you're changing

The intake agent's system prompt (in `specs/PROMPT_T3_AI_AGENTS.md`). This controls how Claude conducts the design consultation chat and produces the `DesignBrief`.

### Step 1: Run baseline scores BEFORE your change

```bash
cd backend
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest tests/eval/test_golden.py -x -v -m integration 2>&1 | tee baseline_golden.txt
```

This runs 8 golden tests with real Claude API calls. All must pass. Save the output.

Then run the scored eval scenarios:

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest tests/eval/test_live_eval.py -x -v -m integration 2>&1 | tee baseline_live.txt
```

This runs 6 scripted conversations (scenarios A-F in `tests/eval/scenarios.py`), then scores each brief via `evaluate_brief()` (100-point rubric) and each conversation via `evaluate_conversation_quality()` (25-point rubric).

Record the per-criterion scores. These are your baseline.

### Step 2: Make your prompt change

Edit the system prompt. Keep a clear description of what you changed and why.

### Step 3: Run the same tests again

```bash
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest tests/eval/test_golden.py -x -v -m integration 2>&1 | tee after_golden.txt
ANTHROPIC_API_KEY=sk-ant-... .venv/bin/python -m pytest tests/eval/test_live_eval.py -x -v -m integration 2>&1 | tee after_live.txt
```

### Step 4: Compare

**Pass criteria:**
- All 8 golden tests still pass (non-negotiable — these are structural checks)
- Brief rubric total (100pts) did not drop by more than 5 points on any scenario
- No individual criterion dropped by more than 2 points (noise) on average across scenarios
- If the change targets a specific criterion (e.g., `lighting_design`), that criterion must improve by at least 1 point on relevant scenarios

**How to interpret:**
- LLM-as-judge scores have ~3-5 point variance per run. A 2-point move could be noise.
- Run the scored eval 2-3 times and average if you need confidence.
- The `CRITERION_TO_PROMPT_SECTION` mapping in `tests/eval/scenarios.py` tells you which prompt sections affect which criteria — use this to focus changes.

### Step 5: Variance check (optional but recommended)

Because Claude's responses vary, run the live eval 3 times and compute per-criterion averages:

```python
# Quick comparison script
import json, statistics

runs = [run1_scores, run2_scores, run3_scores]  # each is dict of criterion: score
for criterion in runs[0]:
    values = [r[criterion] for r in runs]
    print(f"{criterion}: mean={statistics.mean(values):.1f} stdev={statistics.stdev(values):.1f}")
```

If stdev > 3 on a criterion, the result is inconclusive — you need more runs.

---

## Path B: Image Generation Prompt Changes

### What you're changing

Files in `backend/prompts/`:
- `generation_*.txt` — main generation prompt template
- `room_preservation_*.txt` — room structure preservation block
- `edit*.txt` — annotation-based edit prompt

Or Python code that assembles prompts:
- `generate.py`: `_build_generation_prompt()`, `_format_room_context()`
- `edit.py`: `_build_edit_instructions()`, `CONTEXT_PROMPT`, `TEXT_FEEDBACK_TEMPLATE`

### Step 1: Create a new prompt version (don't overwrite)

The prompt versioning system lets you test without breaking the current version.

```bash
# Example: creating generation v3
cp backend/prompts/generation_v2.txt backend/prompts/generation_v3.txt
# Edit generation_v3.txt with your changes
```

Update the manifest to make your version active:

```json
// backend/prompts/prompt_versions.json
{
  "generation": {"active": "v3", "previous": "v2"},
  "room_preservation": {"active": "v2"},
  "edit": {"active": "v1"}
}
```

**Important**: If your change is to the Python assembly code (e.g., `_format_room_context()`), you can't version it this way. Instead, make the code change directly but keep the before/after eval data.

### Step 2: Run generation with eval enabled

```bash
# Set environment
export EVAL_MODE=full                    # enables fast + deep eval
export ANTHROPIC_API_KEY=sk-ant-...      # for deep eval (Claude judge)
export GEMINI_API_KEY=...                # for actual generation

# Run the E2E test that exercises the full pipeline
.venv/bin/python -m pytest tests/eval/test_full_mode.py -x -v -m integration
```

Or run the golden path test which exercises the entire workflow (intake → generation → edit → shopping):

```bash
.venv/bin/python -m pytest tests/test_e2e.py -x -v -k "golden" -m integration
```

Each generation automatically:
1. Runs `run_fast_eval()` — 4 metrics, $0, <100ms
2. If flagged or 20% sample: runs `evaluate_generation()` — 9 criteria, 100 points, ~$0.02
3. Appends results to `eval_history.jsonl` with `prompt_version` tag

### Step 3: Compare versions in eval_history.jsonl

After running with both v2 and v3, compare scores:

```python
from pathlib import Path
from app.utils.score_tracking import load_history

history = load_history(Path("eval_history.jsonl"))

v2_scores = [r for r in history if r["prompt_version"] == "v2"]
v3_scores = [r for r in history if r["prompt_version"] == "v3"]

# Compare fast eval composites
v2_composites = [r["fast_eval"]["composite_score"] for r in v2_scores if r["fast_eval"]]
v3_composites = [r["fast_eval"]["composite_score"] for r in v3_scores if r["fast_eval"]]

print(f"v2 composite: mean={sum(v2_composites)/len(v2_composites):.3f} (n={len(v2_composites)})")
print(f"v3 composite: mean={sum(v3_composites)/len(v3_composites):.3f} (n={len(v3_composites)})")

# Compare deep eval totals (if available)
v2_deep = [r["deep_eval"]["total"] for r in v2_scores if r["deep_eval"].get("total")]
v3_deep = [r["deep_eval"]["total"] for r in v3_scores if r["deep_eval"].get("total")]

if v2_deep and v3_deep:
    print(f"v2 deep total: mean={sum(v2_deep)/len(v2_deep):.1f} (n={len(v2_deep)})")
    print(f"v3 deep total: mean={sum(v3_deep)/len(v3_deep):.1f} (n={len(v3_deep)})")
```

### Step 4: Check regression detection

```python
from pathlib import Path
from app.utils.score_tracking import detect_regression

result = detect_regression(
    history_path=Path("eval_history.jsonl"),
    scenario="generation_option_0",
    latest_total=75,      # your latest deep eval score
    window=5,             # compare against last 5 runs
    threshold=10,         # 10-point drop = regression
)

print(result)
# {"is_regression": False, "rolling_avg": 78.4, "delta": -3.4, "window_size": 5}
```

### Step 5: Pass/fail criteria

**Fast eval (always available):**

| Metric | Baseline threshold | Regression = |
|--------|-------------------|-------------|
| CLIP text-image | 0.20 | New version averages below 0.20 |
| CLIP image-image | 0.70 | New version averages below 0.70 |
| Edge-SSIM | 0.30 | New version averages below 0.30 |
| Composite | 0.40 | New version mean drops >0.05 from old version mean |
| Artifacts (edit) | false | New version produces more artifact detections |

**Deep eval (when available):**

| Tag | Score range | Acceptable? |
|-----|-----------|-------------|
| EXCELLENT | 85-100 | Yes |
| GOOD | 70-84 | Yes |
| ACCEPTABLE | 55-69 | Marginal — investigate per-criterion scores |
| WEAK | 40-54 | No — rollback or iterate |
| FAIL | 0-39 | No — rollback immediately |

**The change is good if:**
1. Fast eval composite mean is equal or higher than before
2. No individual fast metric drops below its threshold
3. Deep eval total mean is equal or higher, OR within 5 points (noise range)
4. `detect_regression()` returns `is_regression: false`

**The change is bad if:**
1. Fast eval composite drops >0.05
2. Deep eval total drops >10 points
3. `detect_regression()` returns `is_regression: true`
4. New artifact detections appear in edit prompts

### Step 6: Ship or rollback

**Ship**: Leave `prompt_versions.json` pointing to the new version. Commit both the new prompt file and the manifest update.

**Rollback**: Revert `prompt_versions.json` to the previous version. No code changes needed — the old prompt file is still there.

---

## Edit Prompt Changes (Additional Checks)

Edit prompts (`edit*.txt`) have an extra concern: annotation artifact leakage. The edit eval runs `detect_annotation_artifacts()` which looks for residual colored circles in the output image.

When changing edit prompts:
1. Run the edit eval specifically: set `EVAL_MODE=full` and trigger an edit workflow
2. Check `has_artifacts` in the fast eval result — must be `false`
3. The edit deep eval has `artifact_cleanliness` (0-10) — must be 7+

---

## What Each Metric Actually Measures

Understanding what the numbers mean helps you diagnose WHY a prompt change helped or hurt.

### Fast eval (image_eval.py)

| Metric | What it detects | Affected by which prompts |
|--------|----------------|--------------------------|
| **CLIP text-image** (0-1) | Does the generated image match the design brief semantically? | `generation_*.txt` (brief injection), style/color/mood wording |
| **CLIP image-image** (0-1) | Is it recognizably the same room? | `room_preservation_*.txt`, camera/architecture wording |
| **Edge-SSIM** (0-1) | Are wall edges, window frames, door positions preserved? | `room_preservation_*.txt`, spatial constraint wording |
| **Artifact detection** (bool) | Did annotation markers leak into the edit output? | `edit*.txt`, the "ZERO annotations" instruction |

### Deep eval — generation (design_eval.py, 100 points)

| Criterion | Points | Prompt sections that affect it |
|-----------|--------|-------------------------------|
| Photorealism | 0-15 | "indistinguishable from a professional photograph", "full-frame camera and a 24mm wide-angle lens" |
| Style Adherence | 0-15 | `{brief}` injection — how style profile fields are formatted |
| Color Palette | 0-10 | `{brief}` color fields, any palette proportion instructions |
| Room Preservation | 0-20 | `room_preservation_*.txt` entirely — camera, architecture, lighting direction |
| Furniture Scale | 0-10 | `{room_context}` — room dimensions, furniture sizes from LiDAR |
| Lighting | 0-10 | `room_preservation_*.txt` lighting paragraph + `{brief}` lighting field |
| Design Coherence | 0-10 | `{option_variant}` differentiation + brief formatting coherence |
| Brief Compliance | 0-5 | `{brief}` + `{keep_items}` — whether all brief fields are surfaced |
| Keep Items | 0-5 | `{keep_items}` injection — the "Keep these existing items" line |

### Deep eval — intake (intake_eval.py, 100 points)

| Criterion | Points | System prompt sections that affect it |
|-----------|--------|--------------------------------------|
| Style Coherence | 0-10 | TRANSLATION ENGINE, NARRATE step |
| Color Strategy | 0-15 | TRANSLATION ENGINE colors, Elevation Rules, Color Psychology |
| Lighting Design | 0-15 | Room-Specific Guidance, Elevation Rules, 20-Rule Validation |
| Material & Texture | 0-15 | TRANSLATION ENGINE textures, Elevation Rules |
| Design Intelligence | 0-10 | Three-layer design stack, biophilic elements, GENERATE step |
| Diagnostic Depth | 0-5 | DIAGNOSE pipeline DETECT+ANALYZE, Diagnostic Question Bank |
| Actionability | 0-15 | OUTPUT FORMAT elevation rules, SPECIFY step, 20-Rule Validation |
| Completeness | 0-10 | Design Domain Notepad, Mode Instructions, Room-Specific Guidance |
| User Fidelity | 0-5 | Summary Turn instructions, EVALUATE step |

The full mapping is in `tests/eval/scenarios.py` → `CRITERION_TO_PROMPT_SECTION`.

---

## Minimum Sample Sizes

Image generation uses real Gemini API calls ($0.04/generation + $0.02/deep eval). Budget your experiments.

| Confidence level | Samples per version | Total cost (2 versions) | Detectable effect size |
|-----------------|--------------------|-----------------------|----------------------|
| Quick sanity check | 3-5 | ~$0.36-$0.60 | Large (>15 point deep eval swing) |
| Reasonable confidence | 10-15 | ~$1.20-$1.80 | Medium (>8 point swing) |
| Statistical confidence | 30-50 | ~$3.60-$6.00 | Small (>5 point swing) |

For intake eval, each scenario run costs ~$0.10-0.15 (Claude API). Running all 6 scenarios 3 times = ~$2.70.

---

## Checklist for Prompt Change PRs

Before marking a prompt change PR as ready:

- [ ] New prompt version created (not overwritten) OR code change clearly scoped
- [ ] Baseline scores recorded (before change)
- [ ] Post-change scores recorded (after change)
- [ ] No metric regressed beyond noise threshold
- [ ] Target metric improved (if change was targeted)
- [ ] `detect_regression()` returns `is_regression: false`
- [ ] Golden tests still pass (intake) or unit tests still pass (generation)
- [ ] `prompt_versions.json` updated if new template version
- [ ] Eval data included in PR description (before/after table)

---

## File Reference

| File | What it does |
|------|-------------|
| `app/utils/image_eval.py` | Fast eval: CLIP scores, Edge-SSIM, artifact detection |
| `app/activities/design_eval.py` | Deep eval: Claude Vision judge, 3 rubrics (gen/edit/shopping) |
| `app/activities/intake_eval.py` | Intake eval: brief rubric (100pt) + conversation rubric (25pt) |
| `app/utils/score_tracking.py` | JSONL history append + rolling regression detection |
| `app/utils/prompt_versioning.py` | Version-aware prompt loading from manifest |
| `prompts/prompt_versions.json` | Active version manifest (edit to switch versions) |
| `tests/eval/test_golden.py` | 8 structural golden tests for intake agent (real API) |
| `tests/eval/test_live_eval.py` | 6 scored scenarios for intake agent (real API + judge) |
| `tests/eval/scenarios.py` | Scenario definitions + `CRITERION_TO_PROMPT_SECTION` mapping |
| `tests/eval/test_full_mode.py` | Full eval mode integration test for generation |
| `eval_history.jsonl` | Score history (auto-appended, gitignored) |
