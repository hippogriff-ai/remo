# Ralph Loop Prompt — Prompt Tuning: Image Generation Pipeline

You are iteratively optimizing the image generation prompts for the Remo project. Your mission is to improve the quality of Gemini-generated room redesigns through systematic, eval-driven prompt engineering. Each loop targets the single weakest quality dimension, applies a research-backed improvement, measures the delta, and ships or rolls back.

**Key principle**: Every prompt change must produce measurable evidence of improvement or non-regression. "It looks better to me" is not evidence. Numbers from the eval harness are.

## CRITICAL: Git Worktree Setup (MUST DO FIRST)

Before ANY other work, you MUST work in the T3 worktree. Do NOT work in the main `/Hanalei/remo` directory.

```bash
cd /Users/claudevcheval/Hanalei/remo-ai
git checkout -b team/ai/prompt-v{N}  # increment N for each tuning session
```

**Verify** you are in the correct worktree before starting any loop:
```bash
pwd  # Must show /Users/claudevcheval/Hanalei/remo-ai
git branch --show-current  # Must show team/ai/prompt-v*
```

## Sources of Truth

Read ALL of these before your first loop:

- **Eval guide**: `specs/EVAL_GUIDE_FOR_PROMPT_CHANGES.md` — The measurement protocol. Defines pass/fail criteria, sample sizes, regression detection.
- **Gemini prompting research**: `specs/RESEARCH_GEMINI_PROMPTING.md` — Model-specific behaviors, prompt architecture, 20 ranked improvements. This is your improvement playbook.
- **Design intelligence**: `specs/DESIGN_INTELLIGENCE.md` — Interior design reasoning (three-layer stack, translation engine, elevation rules). Prompts must reflect this vocabulary.
- **Current prompts**: `backend/prompts/` — All `.txt` template files + `prompt_versions.json` manifest.
- **Prompt assembly code**: `backend/app/activities/generate.py` (`_build_generation_prompt`, `_format_room_context`, `_OPTION_VARIANTS`) and `backend/app/activities/edit.py` (`_build_edit_instructions`, `CONTEXT_PROMPT`, `TEXT_FEEDBACK_TEMPLATE`).
- **Eval code**: `backend/app/utils/image_eval.py` (fast eval), `backend/app/activities/design_eval.py` (deep eval rubrics), `backend/app/utils/score_tracking.py` (regression detection).
- **Continuity**: `CONTINUITY.md`

## File Ownership (STRICT)

You ONLY create/modify these files:
- `backend/prompts/generation_v*.txt` — New generation prompt versions (NEVER overwrite existing versions)
- `backend/prompts/room_preservation_v*.txt` — New room preservation versions
- `backend/prompts/edit_v*.txt` — New edit prompt versions
- `backend/prompts/prompt_versions.json` — Version manifest (switch active versions)
- `backend/app/activities/generate.py` — ONLY `_build_generation_prompt()`, `_format_room_context()`, `_OPTION_VARIANTS` (prompt assembly logic)
- `backend/app/activities/edit.py` — ONLY `_build_edit_instructions()`, `CONTEXT_PROMPT`, `TEXT_FEEDBACK_TEMPLATE` (edit prompt assembly logic)
- `CONTINUITY.md` — Session state

You NEVER touch:
- `backend/app/models/contracts.py` — T0 owns (frozen)
- `backend/app/api/` — T0 owns
- `backend/app/workflows/` — T0 owns
- `backend/app/utils/image_eval.py`, `design_eval.py`, `score_tracking.py` — Eval infrastructure (read-only for you)
- `backend/app/activities/intake.py`, `shopping.py` — Separate concern
- `ios/` — T1 owns

---

## The Three Prompt Targets

You optimize three independent prompt systems. Each loop targets whichever is weakest.

### Target 1: Generation Prompt (`generation_v*.txt` + `_build_generation_prompt()`)

**What it controls**: The main room redesign prompt sent to Gemini with room photos + brief.

**Eval metrics that measure it**:
| Metric | Source | What weakness signals |
|--------|--------|----------------------|
| CLIP text-image | Fast eval | Brief not reflected in output (style, colors, mood) |
| Photorealism | Deep eval (0-15) | "Render look", flat lighting, impossible physics |
| Style Adherence | Deep eval (0-15) | Generic output, wrong style family |
| Color Palette | Deep eval (0-10) | Colors don't match brief's 60/30/10 spec |
| Furniture Scale | Deep eval (0-10) | Oversized/undersized furniture, impossible proportions |
| Lighting | Deep eval (0-10) | Flat lighting, inconsistent shadows, wrong direction |
| Design Coherence | Deep eval (0-10) | Mismatched elements, no unified vision |
| Brief Compliance | Deep eval (0-5) | Constraints ignored, fields not surfaced |
| Keep Items | Deep eval (0-5) | User's keep-items replaced or missing |

**Research-backed improvements** (from `RESEARCH_GEMINI_PROMPTING.md` Section 9):
1. Add explicit image role labels (High impact, Low effort) — prevents style bleeding from inspiration images into room geometry
2. Add layered mutability — immutable architecture / replaceable furniture / additive decor (High impact, Low effort)
3. Add enumeration of architectural features (High impact, Medium effort)
4. Add lived-in details like "stack of books, draped throw" (Medium impact, Low effort) — breaks showroom-render look
5. Translate user "avoid" preferences to positive language (Medium impact, Low effort)
6. Photography quality terms: use "architectural magazine" + camera specs, AVOID "4K", "8K", "masterpiece" (Medium impact, Low effort)
7. ICS Framework: Image type + Content + Style, keep each section under 25 words (Medium impact, Medium effort)

### Target 2: Room Preservation Prompt (`room_preservation_v*.txt`)

**What it controls**: Spatial fidelity — camera angle, architectural elements, lighting direction.

**Eval metrics that measure it**:
| Metric | Source | What weakness signals |
|--------|--------|----------------------|
| CLIP image-image | Fast eval | Room structure not preserved |
| Edge-SSIM | Fast eval | Wall/window/door geometry changed |
| Room Preservation | Deep eval (0-20) | Architecture modified, camera drifted |

**Research-backed improvements**:
1. Camera angle preservation — phenomenological anchoring: "viewer should feel they are standing in the same spot" (already partially in v2, strengthen)
2. Enumeration-based anchoring — explicit checklist of fixed architectural features per room (High impact)
3. Layered mutability language — "Layer 1 (IMMUTABLE): walls, windows, doors..." (High impact)
4. LiDAR data formatting — meters, absolute positions relative to walls, floor area as sanity check (Medium impact)
5. Scale reference — combine absolute measurements AND relative proportions (Medium impact)

### Target 3: Edit Prompt (`edit_v*.txt` + `_build_edit_instructions()`)

**What it controls**: Annotation-based iterative editing — applying targeted changes to marked regions.

**Eval metrics that measure it**:
| Metric | Source | What weakness signals |
|--------|--------|----------------------|
| Artifact detection | Fast eval | Colored circles leaked into output |
| Edit Fidelity | Deep eval (0-15) | Annotated regions not changed correctly |
| Preservation Fidelity | Deep eval (0-15) | Unmarked regions changed |
| Artifact Cleanliness | Deep eval (0-10) | Residual markers visible |
| Seamless Blending | Deep eval (0-5) | Obvious edit boundaries |
| Instruction Accuracy | Deep eval (0-5) | Edit doesn't match instruction |

**Research-backed improvements**:
1. Add TARGET + PRESERVE fields per annotation region (Medium impact, Low effort) — "TARGET: the area rug. PRESERVE: floor around rug edges, furniture legs on rug"
2. Add cumulative changelog to every edit prompt (High impact, Medium effort) — prevents drift across iterations
3. Enhanced lock language — list 3-5 specific visible elements to preserve (Medium impact, Low effort)
4. Re-include original room photo in every edit turn (High impact, Low effort) — spatial anchoring
5. Add design intent one-liner + changelog (Medium impact, Low effort)

---

## Each Loop — ONE Prompt Change, Measured Rigorously

### Step 1: Orient — Identify the Weakest Metric (~3 subagents)

Read the current state and find what to improve:

1. Read `CONTINUITY.md` to understand what's been done.
2. Read `backend/prompts/prompt_versions.json` to know current active versions.
3. Read the current active prompt files to understand what's already in them.
4. Check for eval history:

```bash
cd /Users/claudevcheval/Hanalei/remo-ai/backend

# Check if eval history exists
ls -la eval_history.jsonl tests/eval/score_history.jsonl 2>/dev/null

# If history exists, analyze scores:
.venv/bin/python -c "
from pathlib import Path
from app.utils.score_tracking import load_history
import json

for path in [Path('eval_history.jsonl'), Path('tests/eval/score_history.jsonl')]:
    if path.exists():
        history = load_history(path)
        print(f'--- {path} ({len(history)} records) ---')
        for r in history[-5:]:
            print(json.dumps({k: r[k] for k in ['scenario','prompt_version','fast_eval','deep_eval'] if k in r}, indent=2))
"
```

5. **If no eval history exists** (first loop): Run the full eval to establish a baseline. See Step 4 below.

6. **If eval history exists**: Identify the weakest metric across recent runs:
   - Find the lowest-scoring deep eval criterion (by average across scenarios)
   - Find any fast eval metric below threshold (CLIP text < 0.20, CLIP image < 0.70, Edge-SSIM < 0.30, composite < 0.40)
   - Check for artifact detections in edit evals
   - The weakest metric determines which prompt target and which improvement to apply

7. Cross-reference the weak metric against the tables in "The Three Prompt Targets" section above to find:
   - Which prompt file to change
   - Which research-backed improvement addresses that weakness

**Decision rule**: Pick the improvement that (a) addresses the weakest metric and (b) has the highest impact-to-effort ratio from the research doc.

### Step 2: Diagnose — Map Weakness to Specific Change (~2 subagents)

Before touching any prompt:

1. **Read the relevant research section** from `specs/RESEARCH_GEMINI_PROMPTING.md`:
   - Weak photorealism → Section 2.5 (photography terms), 2.6 (lived-in details)
   - Weak room preservation → Section 3 (spatial preservation, camera angle, LiDAR)
   - Weak style adherence → Section 4 (style transfer, materials, color)
   - Weak edit quality → Section 5 (multi-turn editing, lock language)
   - Artifacts → Section 5.3-5.4 (annotation practices, lock language)

2. **Read the relevant design intelligence section** from `specs/DESIGN_INTELLIGENCE.md`:
   - Weak color palette → Section 5 (60-30-10 rule, color psychology)
   - Weak design coherence → Section 1 (three-layer stack), Section 3 (DIAGNOSE pipeline)
   - Weak material vocabulary → Section 2 (translation engine)
   - Weak brief compliance → Section 5 (elevation rules)

3. **Read the current prompt** file you plan to modify. Understand what's already there — do not duplicate existing instructions or contradict them.

4. **Draft the specific change** in your notes. The change should be:
   - Targeted: addresses exactly one weakness
   - Concrete: specific language, not vague instructions
   - Compatible: doesn't contradict existing prompt language
   - Measurable: you can name which eval metric should improve

5. **Check for prompt length concerns**: Gemini works best with prompts under 25 words per section (ICS framework). If adding text, consider whether something can be tightened or removed.

### Step 3: Create New Version — Apply the Change

**NEVER overwrite an existing prompt version.** Always create a new version file.

```bash
# Example: creating generation v3
cd /Users/claudevcheval/Hanalei/remo-ai/backend

# 1. Copy the current active version
cp prompts/generation_v2.txt prompts/generation_v3.txt

# 2. Edit the new version with your targeted change
# (use your editor / Write tool)

# 3. Update the manifest to make your version active
# Edit prompts/prompt_versions.json:
#   "generation": {"active": "v3", "previous": "v2"}
```

**If your change is to the Python assembly code** (e.g., `_format_room_context()`, `_build_edit_instructions()`):
- Make the code change directly in `generate.py` or `edit.py`
- Keep the before/after eval data carefully — you can't version-rollback code changes as easily
- Consider whether the change can be expressed as a prompt template change instead (preferred)

**Change documentation**: In the new prompt file, add a comment at the top:
```
# v3 changelog: Added explicit image role labels (source room vs. style inspiration)
# to prevent style bleeding into room geometry.
# Target metric: CLIP image-image (room preservation), Room Preservation (deep eval)
# Research reference: RESEARCH_GEMINI_PROMPTING.md Section 2.4
```

### Step 4: Run Baseline Eval — Score Current Version (5 runs minimum)

Run the eval with the CURRENT (old) version active to establish a baseline. **5 runs minimum** — LLM outputs are stochastic and 3 runs cannot distinguish real improvement from luck.

```bash
cd /Users/claudevcheval/Hanalei/remo-ai/backend

# Set environment
export EVAL_MODE=full
export ANTHROPIC_API_KEY=sk-ant-...
export GEMINI_API_KEY=...

# Run 5 times, save output
for i in 1 2 3 4 5; do
  echo "=== Baseline run $i ==="
  .venv/bin/python -m pytest tests/eval/test_full_mode.py -x -v -m integration 2>&1 | tee baseline_run_${i}.txt
done
```

Record the per-metric scores in a comparison table:

```
| Metric              | Run 1 | Run 2 | Run 3 | Run 4 | Run 5 | Mean  | StDev |
|---------------------|-------|-------|-------|-------|-------|-------|-------|
| CLIP text-image     |       |       |       |       |       |       |       |
| CLIP image-image    |       |       |       |       |       |       |       |
| Edge-SSIM           |       |       |       |       |       |       |       |
| Composite           |       |       |       |       |       |       |       |
| Photorealism (0-15) |       |       |       |       |       |       |       |
| Style Adher. (0-15) |       |       |       |       |       |       |       |
| Color Palette(0-10) |       |       |       |       |       |       |       |
| Room Preserv.(0-20) |       |       |       |       |       |       |       |
| Furn. Scale  (0-10) |       |       |       |       |       |       |       |
| Lighting     (0-10) |       |       |       |       |       |       |       |
| Design Coher.(0-10) |       |       |       |       |       |       |       |
| Brief Compl. (0-5)  |       |       |       |       |       |       |       |
| Keep Items   (0-5)  |       |       |       |       |       |       |       |
| TOTAL        (0-100)|       |       |       |       |       |       |       |
```

### Step 5: Run Changed Eval — Score New Version (5 runs minimum)

Switch to the new version and run the same eval:

```bash
# Ensure prompt_versions.json points to the new version
# Then run 5 times
for i in 1 2 3 4 5; do
  echo "=== Changed run $i ==="
  .venv/bin/python -m pytest tests/eval/test_full_mode.py -x -v -m integration 2>&1 | tee changed_run_${i}.txt
done
```

Fill in the same comparison table for the new version.

### Step 6: Compare & Decide — Statistical Significance Gate

**The improvement must be statistically real, not a lucky streak.** Use the bootstrap confidence interval test below to verify.

#### 6a. Run the significance test

```python
import numpy as np

def is_improvement_real(baseline_scores: list[float], changed_scores: list[float],
                        min_effect: float = 3.0, confidence: float = 0.90,
                        n_bootstrap: int = 10000) -> dict:
    """
    Bootstrap test: is the new version genuinely better?

    Args:
        baseline_scores: Total scores from baseline runs (e.g., [72, 68, 75, 70, 71])
        changed_scores:  Total scores from changed runs  (e.g., [78, 76, 80, 74, 77])
        min_effect:      Minimum meaningful improvement in points (practical significance)
        confidence:      Confidence level for the interval (0.90 = 90% CI)
        n_bootstrap:     Number of bootstrap resamples

    Returns:
        dict with verdict, CI bounds, mean improvement, and p_better
    """
    baseline = np.array(baseline_scores)
    changed = np.array(changed_scores)
    observed_diff = changed.mean() - baseline.mean()

    # Bootstrap: resample with replacement, compute mean difference each time
    boot_diffs = []
    for _ in range(n_bootstrap):
        b_sample = np.random.choice(baseline, size=len(baseline), replace=True)
        c_sample = np.random.choice(changed, size=len(changed), replace=True)
        boot_diffs.append(c_sample.mean() - b_sample.mean())
    boot_diffs = np.array(boot_diffs)

    alpha = 1.0 - confidence
    ci_low = np.percentile(boot_diffs, 100 * alpha / 2)
    ci_high = np.percentile(boot_diffs, 100 * (1 - alpha / 2))
    p_better = np.mean(boot_diffs > 0)  # probability new version is better at all

    # Verdict logic
    if ci_low > min_effect:
        verdict = "SHIP"          # CI entirely above min_effect — real improvement
    elif ci_low > 0:
        verdict = "LIKELY_BETTER" # CI above zero but overlaps min_effect — probably real, consider more runs
    elif ci_high < 0:
        verdict = "ROLLBACK"      # CI entirely below zero — regression
    else:
        verdict = "INCONCLUSIVE"  # CI spans zero — need more runs

    return {
        "verdict": verdict,
        "mean_improvement": round(observed_diff, 2),
        "ci_90": [round(ci_low, 2), round(ci_high, 2)],
        "p_better": round(p_better, 3),       # probability new > old
        "baseline_mean": round(baseline.mean(), 2),
        "changed_mean": round(changed.mean(), 2),
        "recommendation": {
            "SHIP": "Improvement is real and meaningful. Ship it.",
            "LIKELY_BETTER": "Probably better but effect is small. Run 5 more per version to confirm.",
            "INCONCLUSIVE": "Cannot tell. Run 5 more per version (total 10) before deciding.",
            "ROLLBACK": "New version is worse. Rollback immediately.",
        }[verdict]
    }

# --- Usage ---
baseline_totals = [72, 68, 75, 70, 71]  # deep eval totals from baseline runs
changed_totals  = [78, 76, 80, 74, 77]  # deep eval totals from changed runs

result = is_improvement_real(baseline_totals, changed_totals)
print(result)
# {
#   "verdict": "SHIP",
#   "mean_improvement": 6.8,
#   "ci_90": [2.4, 11.2],
#   "p_better": 0.987,
#   "baseline_mean": 71.2,
#   "changed_mean": 77.0,
#   "recommendation": "Improvement is real and meaningful. Ship it."
# }
```

Run this for **both** the deep eval total AND the specific target metric you aimed to improve. Both must pass.

#### 6b. Interpret the verdict

| Verdict | Meaning | Action |
|---------|---------|--------|
| **SHIP** | 90% CI is entirely above `min_effect` (3 pts). The improvement is real AND meaningful. | Ship. Update `prompt_versions.json`. |
| **LIKELY_BETTER** | 90% CI is above zero but overlaps `min_effect`. Probably real but could be small. | Run 5 more per version (total 10). If verdict upgrades to SHIP, ship it. If still LIKELY_BETTER after 10 runs, ship it — the improvement is real, just small. |
| **INCONCLUSIVE** | 90% CI spans zero. Cannot distinguish from noise. | Run 5 more per version (total 10). If still INCONCLUSIVE after 10, the change has no effect — rollback (no point keeping a no-op change). |
| **ROLLBACK** | 90% CI is entirely below zero. New version is worse. | Rollback immediately. Do not run more. |

#### 6c. Minimum effect sizes (practical significance thresholds)

Even a "statistically significant" 0.5-point improvement is not worth shipping — it's real but trivial. These are the minimum improvements worth keeping:

| Metric | Minimum meaningful improvement | Rationale |
|--------|-------------------------------|-----------|
| Deep eval total (0-100) | 3 points | Below this, users won't notice |
| Individual criterion (0-20 scale) | 2 points | 1 point is within judge noise |
| Individual criterion (0-10 scale) | 1 point | Finer scale, smaller threshold |
| Individual criterion (0-5 scale) | 1 point | Floor threshold |
| Fast eval composite (0-1) | 0.02 | Below this, metric noise dominates |
| CLIP text-image (0-1) | 0.02 | Same |
| CLIP image-image (0-1) | 0.02 | Same |
| Edge-SSIM (0-1) | 0.03 | Higher variance metric |

#### 6d. Additional safety checks

After the significance test passes, also verify:

1. No individual fast metric dropped below its hard threshold (CLIP text ≥ 0.20, CLIP image ≥ 0.70, Edge-SSIM ≥ 0.30)
2. No NEW artifact detections appeared (for edit prompts)
3. `detect_regression()` returns `is_regression: false`:

```python
from pathlib import Path
from app.utils.score_tracking import detect_regression

result = detect_regression(
    history_path=Path("eval_history.jsonl"),
    scenario="generation_option_0",
    latest_total=CHANGED_MEAN_TOTAL,
    window=5,
    threshold=10,
)
print(result)
```

#### 6e. Decision matrix summary

```
Statistical test → SHIP + no hard threshold violations + no artifacts → SHIP
Statistical test → SHIP + hard threshold violated → ROLLBACK (improvement in one area caused regression in another)
Statistical test → LIKELY_BETTER → run more samples, then re-evaluate
Statistical test → INCONCLUSIVE → run more samples, then re-evaluate (rollback if still inconclusive after 10 runs)
Statistical test → ROLLBACK → ROLLBACK immediately
```

**To rollback:**
```bash
# Revert prompt_versions.json to previous version
# The old prompt file is still there — no code changes needed
```

**To ship:**
```bash
# Leave prompt_versions.json pointing to the new version
# Both old and new prompt files stay in the repo
```

**Baseline ratchet**: When you ship a new version, its scores become the **new baseline** for the next loop. You always compare against your best-known-good version, never against the original v1. This prevents regression across multiple loops:

```
Loop 1: v2 (baseline) → v3 (improved) → SHIP → v3 is now baseline
Loop 2: v3 (baseline) → v4 (improved) → SHIP → v4 is now baseline
Loop 3: v4 (baseline) → v5 (regressed) → ROLLBACK → v4 remains baseline
Loop 4: v4 (baseline) → v5b (improved) → SHIP → v5b is now baseline
```

When rolling back, the previous version's scores remain the baseline. Do NOT re-run baseline eval for a version you already measured — reuse the scores from the prior loop's "changed eval" run that proved that version was good.

**Record the current baseline scores in `CONTINUITY.md`** after every ship decision so the next loop can skip the baseline eval if the scores are fresh (< 24 hours old and no code changes to prompt assembly).

### Step 7: Document — Record Results

Update `CONTINUITY.md` with:
- Which metric was weakest
- What change was made (version, file, specific language)
- Before/after score comparison table
- Ship or rollback decision with rationale
- **Current baseline version and its mean scores** (so the next loop knows what to beat)
- What the NEXT weakest metric is (for the next loop)

Add the comparison table to the prompt file's changelog comment.

---

## Improvement Queue — Research-Backed Priority Order

When no eval data exists (cold start), follow this priority order from `RESEARCH_GEMINI_PROMPTING.md` Section 9. Once eval data exists, let the scores drive prioritization instead.

### Immediate (Prompt-Only Changes, High Impact)

| # | Change | Target | Impact | Effort |
|---|--------|--------|--------|--------|
| 1 | Add explicit image role labels: "Images 1-2 are source room. Images 3+ are style inspiration only." | Generation | High | Low |
| 2 | Add layered mutability: immutable architecture / replaceable furniture / additive decor | Room Preservation | High | Low |
| 3 | Add architectural feature enumeration (explicit checklist of fixed elements per room) | Room Preservation | High | Medium |
| 4 | Add lived-in details: "stack of books, draped throw" — breaks showroom render look | Generation | Medium | Low |
| 5 | Translate user "avoid" to positive language: "no brass" → "matte black fixtures" | Generation (assembly code) | Medium | Low |
| 6 | Add cumulative changelog to every edit prompt | Edit | High | Medium |
| 7 | Add TARGET + PRESERVE fields per annotation region | Edit (assembly code) | Medium | Low |
| 8 | Re-include original room photo in every edit turn | Edit (assembly code) | High | Low |
| 9 | Enhanced lock language — list 3-5 visible elements to preserve | Edit | Medium | Low |
| 10 | Camera preservation — phenomenological anchoring + vanishing points | Room Preservation | Medium | Low |

### Medium-Term (Code + Prompt Changes)

| # | Change | Target | Impact | Effort |
|---|--------|--------|--------|--------|
| 11 | ICS framework: restructure generation prompt into Image type + Content + Style blocks, 25 words max per section | Generation | Medium | Medium |
| 12 | Verify thought signature preservation during history pruning | Edit (code) | Critical | Medium |
| 13 | Add design intent one-liner + changelog summary to edit prompt | Edit | Medium | Low |
| 14 | Implement contradiction detection for conflicting edits | Edit (code) | Medium | Medium |
| 15 | "Compact and restart" after 3 edit rounds — new baseline from latest image | Edit (code) | Medium | Medium |
| 16 | Per-image media_resolution: high for room photos, medium for inspiration | Generation (code) | Low | Low |
| 17 | Add photography quality terms from research: "architectural magazine editorial", "Canon EOS R5", "f/8" | Generation | Medium | Low |
| 18 | Color palette spec: use descriptive names + context + inspiration reference + explicit avoidances | Generation (assembly code) | Medium | Low |
| 19 | Furniture style consistency: name design language once early, then list individual pieces | Generation (assembly code) | Low | Low |
| 20 | Negative prompting: replace "avoid X" with "use Y" throughout all prompts | All | Low | Low |

---

## Target 4: LiDAR Scene Data

### Problem

The current `_format_room_context()` outputs flat dimension text like `"Room dimensions: 4.2m × 5.8m, ceiling height 2.7m"`. This lacks the structural detail Gemini needs for spatial accuracy — wall orientations, furniture proportions relative to walls, and a clear hierarchy of geometric data. Example failure: LiDAR shows no extra wall space next to a bathtub, but a towel in the input photo causes Gemini to render 10 inches of phantom wall. Better spatial data also improves shopping (furniture must fit specific wall segments).

### Phase A — T3-only: Restructure prompt assembly (current branch)

Restructure `_format_room_context()` to output structured scene data with section headers (ROOM GEOMETRY / WALLS / FIXED OPENINGS / EXISTING FURNITURE), wall compass orientations, relative proportions for large furniture, and small-item filtering. Create `room_preservation_v5.txt` with dimensional constraint language referencing LiDAR measurements.

**Eval metrics**: Room Preservation (deep eval, 0-20), Furniture Scale (deep eval, 0-10), Edge-SSIM (fast eval).

Changes:
- A1: `_format_room_context()` restructured with section headers + `_orientation_to_compass()` helper
- A2: `room_preservation_v5.txt` — adds DIMENSIONAL CONSTRAINTS section
- A3: Furniture items < 0.3m filtered as noise; capped at 15 items
- A4: Large furniture gets relative proportions: "spans ~36% of shorter wall"

### Phase B — T1+T0: Export wall/opening/furniture positions + camera pose from iOS

Requires T1 iOS changes to RoomPlan export:
- B1: Per-wall opening positions (which wall, offset from left edge)
- B2: Per-wall furniture placement (which wall a sofa is against)
- B3: Camera pose (position + rotation quaternion) from ARSession
- B4: Update `RoomDimensions` model with new fields (T0 contract change)

**Shopping impact**: With furniture-to-wall assignments, shopping can verify "this 2.4m sofa fits the 3.1m wall it's placed against" instead of just "fits in the room."

### Phase C — Experimental: USDZ mesh, scene graph, camera estimation

Research-stage ideas:
- C1: Export USDZ mesh from RoomPlan for 3D-aware generation
- C2: Build scene graph (object relationships: "sofa against wall_2, coffee table 0.5m in front")
- C3: Estimate camera pose from photo when no LiDAR available (monocular depth estimation)

### Pipeline (7-step, fully realized)

1. **Export mesh** — T1 iOS exports RoomPlan data (walls, openings, furniture, camera)
2. **Extract bbox** — Parse furniture bounding boxes with wall assignments
3. **Extract walls** — Build wall list with orientations, widths, opening positions
4. **Camera** — Include camera pose for perspective-correct generation
5. **Prompt** — `_format_room_context()` formats structured scene data
6. **Generate** — Gemini receives precise spatial context
7. **Compare** — Eval measures spatial accuracy improvement

### Shopping Impact by Phase

| Phase | Shopping capability |
|-------|-------------------|
| A (current) | Relative proportions help filter oversized furniture. "Spans 36% of shorter wall" → reject items wider than wall. |
| B | Wall assignments enable "fits this specific wall" checks. Camera pose enables "visible from this angle" filtering. |
| C | Scene graph enables "doesn't block walkway" and "matches adjacent furniture style" checks. |

### Eval Metrics and Risks

**Target improvements** (Phase A):
- Room Preservation: +0.5pt (from 18.0/20) — structured data reduces phantom walls
- Furniture Scale: +0.5-1.0pt (from 9.0/10) — relative proportions constrain sizing
- Edge-SSIM: potential +0.01-0.02 — better spatial consistency

**Risks**:
- Longer room context could reduce prompt budget for other instructions
- Compass directions may confuse Gemini if it doesn't reason about cardinal directions
- Furniture filtering (< 0.3m) could drop relevant small fixtures in some rooms

---

## Metric-to-Prompt Mapping Quick Reference

When a metric is weak, this tells you which prompt file and section to change:

| Weak Metric | Prompt File | Specific Section | Research Doc Reference |
|-------------|-------------|------------------|----------------------|
| CLIP text-image < 0.20 | `generation_v*.txt` | `{brief}` injection, style/color wording | Sec 4.1-4.4 |
| CLIP image-image < 0.70 | `room_preservation_v*.txt` | Camera, architecture wording | Sec 3.2-3.3 |
| Edge-SSIM < 0.30 | `room_preservation_v*.txt` | Spatial constraint wording | Sec 3.3-3.4 |
| Artifacts detected | `edit_v*.txt` | "ZERO annotations" instruction | Sec 5.3-5.4 |
| Photorealism low | `generation_v*.txt` | Camera specs, lived-in details | Sec 2.5-2.6 |
| Style Adherence low | `generation_v*.txt` | Style vocabulary, material descriptors | Sec 4.1-4.2 |
| Color Palette low | `_build_generation_prompt()` | Color field formatting, 60/30/10 | Sec 4.4 |
| Room Preservation low | `room_preservation_v*.txt` | Layered mutability, feature enumeration | Sec 3.2-3.5 |
| Furniture Scale low | `_format_room_context()` | LiDAR data formatting, proportions | Sec 3.4-3.5 |
| Lighting low | `room_preservation_v*.txt` | Lighting paragraph + `{brief}` lighting field | Sec 4.3 |
| Design Coherence low | `generation_v*.txt` | `{option_variant}` + brief formatting | Sec 4.5 |
| Brief Compliance low | `_build_generation_prompt()` | Brief field surfacing, keep_items line | Sec 2.2 |
| Edit Fidelity low | `edit_v*.txt` | Edit instruction format | Sec 5.2 |
| Preservation Fidelity low | `edit_v*.txt` | Lock language, preservation rules | Sec 5.4 |
| Artifact Cleanliness low | `edit_v*.txt` | "ZERO annotations" strength | Sec 5.3 |

---

## Design Intelligence Vocabulary Reference

When writing prompt language, use professional interior design vocabulary from `specs/DESIGN_INTELLIGENCE.md`:

**Materials** (Level 3 — sweet spot for Gemini):
- Not "brown table" → "walnut dining table with satin finish"
- Not "gold lamp" → "brushed brass arc floor lamp with linen shade"
- Not "white sofa" → "ivory boucle sofa with down-blend cushions"
- Not "wood floor" → "wide-plank walnut flooring with satin finish"

**Lighting** (always three layers):
- Ambient: "warm diffused base lighting (2700K)"
- Task: "reading lamp at seating position"
- Accent: "uplighting on textured wall"

**Colors** (always with proportions and application):
- Not "blue and white" → "navy accent (10% — throw pillows, art), warm ivory walls (60%), natural oak and cream (30% — furniture, textiles)"

**Style** (use high-fidelity terms):
- High fidelity: Scandinavian, Mid-century modern, Industrial, Art Deco, Minimalist, Japandi
- Needs qualification: "Modern" alone is overloaded → "modern Scandinavian"
- Avoid standalone: "Eclectic", "Transitional", "Boho without constraints"

---

## Rules

- You may use up to 20 subagents total per loop.
- Each loop MUST produce exactly ONE prompt change (one file or one code function). No exceptions.
- NEVER overwrite an existing prompt version. Always create a new version file.
- NEVER skip the eval measurement. No shipping without before/after data.
- If the eval shows regression, rollback immediately. Do not try to "fix it forward" — start a fresh loop.
- If a metric has stdev > 3 across runs, run 2 more times before concluding.
- Never stop or do nothing. If blocked on eval infrastructure, work on prompt drafting and document your planned change for when eval is available.
- Do NOT git push. Leave all changes unstaged for the user to review.
- After finishing one loop (ship or rollback), proceed to the next loop immediately.
- Keep the prompt file comments up to date with changelog entries for every version.

## Environment Notes

```bash
cd /Users/claudevcheval/Hanalei/remo-ai/backend

# Virtual environment
source .venv/bin/activate  # or use .venv/bin/python

# Required environment variables
export ANTHROPIC_API_KEY=sk-ant-...    # For deep eval (Claude judge)
export GEMINI_API_KEY=...              # For image generation
export EVAL_MODE=full                  # Enables fast + deep eval

# Run tests
.venv/bin/python -m pytest -x -q                                    # all unit tests
.venv/bin/python -m pytest tests/eval/test_full_mode.py -x -v -m integration  # full eval
.venv/bin/python -m pytest tests/test_e2e.py -x -v -k "golden" -m integration  # golden path

# Lint + type check
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app/
```

## Cost Budget per Loop

| Action | Cost | Runs per loop (5+5) |
|--------|------|---------------------|
| Gemini generation (2K) | $0.134/image | 10 (5 baseline + 5 changed) |
| Deep eval (Claude judge) | ~$0.02/eval | 10 |
| Fast eval | $0 | 10 |
| **Standard loop (5+5)** | **~$1.54** | |
| **Extended loop (10+10)** | **~$3.08** | When verdict is LIKELY_BETTER or INCONCLUSIVE |

5 runs per version balances statistical rigor with cost. This detects improvements > 3 points (deep eval) or > 0.02 (fast eval composite) with 90% confidence. If the bootstrap test returns INCONCLUSIVE, extend to 10 runs per version before deciding.
