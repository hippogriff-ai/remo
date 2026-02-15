# Gemini 3 Pro Image Prompting for Interior Design

> **Type**: Research document (no implementation)
> **Date**: 2026-02-14
> **Model**: `gemini-3-pro-image-preview` (Nano Banana Pro)
> **Scope**: Prompt engineering, quality evaluation, automated optimization

---

## Table of Contents

1. [Model-Specific Behaviors](#1-model-specific-behaviors)
2. [Prompt Architecture for Generation](#2-prompt-architecture-for-generation)
3. [Spatial Preservation & LiDAR Integration](#3-spatial-preservation--lidar-integration)
4. [Style Transfer & Aesthetic Control](#4-style-transfer--aesthetic-control)
5. [Multi-Turn Iterative Editing](#5-multi-turn-iterative-editing)
6. [Evaluation Metrics Pipeline](#6-evaluation-metrics-pipeline)
7. [Automated Prompt Optimization](#7-automated-prompt-optimization)
8. [Cost-Efficient Development](#8-cost-efficient-development)
9. [Actionable Recommendations](#9-actionable-recommendations)

---

## 1. Model-Specific Behaviors

### 1.1 Parameters That Matter

| Parameter | Supported Values | Recommendation |
|-----------|-----------------|----------------|
| `aspect_ratio` | `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `4:5`, `5:4`, `9:16`, `16:9`, `21:9` | Snap to source photo's native ratio. `4:3` is ideal for landscape room shots. |
| `image_size` | `1K`, `2K`, `4K` | Use `2K` — same price as 1K ($0.134), better quality. Reserve 4K ($0.24) for final approved download only. |
| `response_modalities` | `["IMAGE"]`, `["TEXT", "IMAGE"]` | Use `["TEXT", "IMAGE"]` — required for thought signatures in multi-turn editing. |
| `thinking_level` | `"low"`, `"high"` (default) | Keep `"high"` for generation + annotation edits. Consider `"low"` for simple text feedback edits. |
| `temperature` | Default 1.0 | **DO NOT CHANGE.** Google strongly warns that lowering temperature causes looping/degradation. |

### 1.2 system_instruction Is NOT Supported

The `system_instruction` parameter does NOT work with `gemini-3-pro-image-preview`. All instructions must go inline in `contents`. The current codebase correctly embeds everything in the prompt text — do not change this.

### 1.3 Multi-Image Input Rules

- **Max 14 images total** across all turns
- **First 6 slots receive highest-fidelity processing** — put room photos here
- **Image order matters**: first image carries highest weight
- **Explicit role labeling is critical**: "Image 1 is the source room. Image 2 is style inspiration." Without labels, the model ambiguously blends spatial layout from all images.

### 1.4 Thought Signatures (Critical for Editing)

Gemini 3 Pro Image uses encrypted thought signatures for multi-turn context. Rules:
- Signatures appear on the first part after thoughts (text or inlineData) and every subsequent inlineData part
- ALL signatures must be returned verbatim in the next turn
- Missing signatures → **400 error**
- Bypass string (if corrupted): `"context_engineering_is_the_way_to_go"`

**Warning**: The current `_prune_history_images()` strips inlineData parts that may carry thought_signatures. Verify that signatures on image parts are preserved or migrated to text placeholder parts.

### 1.5 Known Failure Modes

| Failure | Frequency | Mitigation |
|---------|-----------|------------|
| Perspective drift over 3+ edit rounds | Common | Re-state preservation prompt every turn; re-include original room photo |
| Furniture scale/proportion errors | Common | Include LiDAR dimensions + relative proportions ("sofa spans half the wall") |
| Annotation artifacts in output | Occasional | Strong "ZERO visual annotations" language + retry mechanism (already implemented) |
| Window/light source hallucination | Occasional | Enumerate fixed architectural elements explicitly |
| "Plastic" 3D render look | Common with generic prompts | Camera specs + magazine references + lived-in details |
| Style bleeding from inspiration images | Common without labeling | Explicit role separation in prompt |
| Quality degradation after edit round 3-4 | Known limitation | 5-round cap; consider "compact and restart" after 3 edits |

### 1.6 media_resolution (Gemini 3 Feature)

New per-image tokenization control:
- `media_resolution_low`: 70 tokens/image
- `media_resolution_medium`: 560 tokens/image
- `media_resolution_high`: 1120 tokens/image (recommended for room photos)

**Potential optimization**: High resolution for room photos (architectural detail matters), medium for inspiration photos (only style/mood matters). Requires v1alpha API.

---

## 2. Prompt Architecture for Generation

### 2.1 The ICS Framework

Validated structure for Gemini image generation: **Image type + Content + Style**. Research shows prompts under 25 words per section achieve 30% higher accuracy than verbose ones.

### 2.2 Recommended Generation Prompt Structure

```
[1. Image type + camera]
Photorealistic interior photograph, shot at eye level with a
wide-angle lens, architectural magazine editorial quality.

[2. Room identity — from source photos]
Redesign the room shown in Images 1 and 2. PRESERVE exact room
layout, dimensions, window positions, and architectural structure.

[3. Style identity — from intake brief + inspiration]
Apply {style_name} interior design: {2-3 specific material calls},
{color palette description}, {lighting mood}.

[4. Specific furniture + materials]
{Named pieces with materials from the DesignBrief}

[5. Lived-in details]
{1-2 small personal touches for realism}

[6. Spatial constraints]
{LiDAR-derived room context}

[7. Preservation rules]
{Room preservation clauses}

[8. Exclusions]
{Standard: no people, no text, no duplicate furniture.
 User avoidances translated to positive language.}
```

### 2.3 Image Slot Allocation

| Slot | Role | Content |
|------|------|---------|
| 1 | Source room (structural) | Primary room photo |
| 2 | Source room (structural) | Secondary angle photo |
| 3 | Style reference | Best inspiration photo |
| 4 | Style reference (optional) | Second inspiration |
| 5 | Color/palette reference (optional) | Third inspiration or palette swatch |
| 6+ | Supplementary | Additional references if available |

### 2.4 Image Role Labeling (Must Add)

Before the images in the prompt:
```
The first 2 images show the actual room to redesign — preserve their
exact architecture, camera angle, and perspective. Do NOT change room
shape, ceiling height, or structural elements.

The following images are style inspiration only — adopt their aesthetic,
color palette, and material choices, but NOT their spatial layout.
```

### 2.5 Photography Quality Terms

**Effective** (measurably improve output):
- `"architectural magazine editorial quality"` / `"Architectural Digest"`
- `"wide-angle shot at eye level"`
- `"full-frame DSLR"` / `"Canon EOS R5"` / `"Sony A7R V"`
- `"f/8 for maximum sharpness"` (whole-scene focus)
- `"natural depth of field"`
- `"cinematic framing"`

**Ineffective / harmful** (avoid):
- `"4K"`, `"8K"`, `"ultra HD"`, `"16K UHD"` — makes output worse
- `"masterpiece"`, `"trending on artstation"`, `"octane render"` — legacy SD terms, ignored
- Named photographers — hit-or-miss; publication names more reliable

### 2.6 Lived-In Details (Anti-"Render" Technique)

The single most underrated technique for photorealism. Add 1-2 personal touches:

```
"A stack of architecture books on the coffee table, a wool throw
 draped casually over the armrest"

"A single cutting board with fresh lemons on the countertop"

"Rumpled sheets, single espresso cup on the oak nightstand"
```

These prevent the "showroom render" look that signals AI generation.

---

## 3. Spatial Preservation & LiDAR Integration

### 3.1 Fundamental Limitation

DreamHome-Pano benchmarks (Feb 2026) show Gemini 3 Pro Image achieves **only 53% spatial IoU** on interior benchmarks (vs 70% for specialized models with ControlNet-style conditioning). Prompt engineering alone cannot fully close this gap. Plan for a failure rate and handle it gracefully.

### 3.2 Camera Angle Preservation (Ranked by Effectiveness)

1. `"Preserve the exact camera angle, focal length, and perspective distortion from the reference photo"` — strongest single phrase
2. `"The vanishing points, horizon line, and lens barrel distortion must match precisely"` — already in v2, keep it
3. `"The viewer should feel they are standing in the same spot looking at the same room"` — phenomenological anchoring
4. `"Captured with a 24mm lens"` — already in v2, effective

**What fails**: Abstract geometry terms ("two-point perspective", camera coordinates), focal lengths >200mm.

### 3.3 Architectural Element Anchoring

**Layered mutability** (from DreamHome-Pano insight — decouple structure from aesthetics):

```
Layer 1 (IMMUTABLE): walls, windows, doors, ceiling, floor — MUST NOT change
Layer 2 (REPLACEABLE): furniture, rugs, curtains, art — redesign these
Layer 3 (ADDITIVE): plants, accessories, lighting fixtures — add as needed
```

**Enumeration-based anchoring** — list specific fixed features:

```
Maintain these fixed architectural elements exactly as photographed:
- Left wall: single arched window, ~1.2m wide
- Right wall: doorway to hallway
- Far wall: two windows flanking a fireplace
- Ceiling: exposed wooden beams running east-west
- Floor: hardwood planks
```

This explicit checklist outperforms generic "preserve all architectural elements."

### 3.4 LiDAR Data as Text (Optimal Format)

**Use meters.** Lead with room dims → openings → furniture. Round to one decimal.

```
ROOM GEOMETRY (LiDAR-measured, precise):
- Dimensions: 4.2m wide x 5.8m long, ceiling height 2.7m
- Floor area: 24.4 m²

FIXED OPENINGS (do not relocate):
- North wall: window (1.4m x 1.2m), centered, sill at 0.9m
- East wall: doorway (0.9m x 2.1m), 1.3m from north corner

EXISTING FURNITURE (for scale reference):
- Sofa: 2.1m x 0.9m footprint, along south wall
- Coffee table: 1.2m x 0.6m, centered in room
```

**Key principles**:
- Absolute positions relative to walls ("1.3m from north corner"), not relative ("right of the window")
- Floor area as scale sanity check
- Skip items <0.3m (decorative noise)
- Cap at ~15 furniture items (diminishing returns)

### 3.5 Scale Reference Techniques

Combine absolute measurements AND relative proportions:

```
Room: 4.2m x 5.8m, ceiling height 2.7m

Furniture should respect realistic proportions:
- Sofa: ~2.1m wide (half the room width), along south wall
- Standard doorways are 2.1m tall; windows start at 0.9m from floor
```

### 3.6 Floor Plan as Additional Input (Potential Enhancement)

Generate a simple 2D overhead view from LiDAR scan data and include as an additional input image. Research shows floor plans improve spatial accuracy when paired with: `"Image N shows the floor plan. Preserve this room layout exactly. Do not change any details in the plan."`

**Trade-off**: Consumes one image slot. A/B test against losing an inspiration image.

### 3.7 Multi-Turn Spatial Correction

When spatial drift is detected:

1. **Complete re-description** (most effective): Re-describe the full layout, not just the delta
2. **Re-include the original room photo** in every editing turn (not just generated image)
3. **Re-state preservation prompt** every turn, not just the first
4. **Prefer re-generation over iterative correction** when drift is detected

---

## 4. Style Transfer & Aesthetic Control

### 4.1 Interior Design Vocabulary

**High fidelity** (consistent, accurate):
- Scandinavian/Nordic, Mid-century modern, Industrial, Art Deco, Minimalist, Japandi, Mediterranean, Farmhouse

**Moderate** (needs material specifics to anchor):
- Coastal grandmother, Quiet luxury, Cottagecore, Wabi-sabi

**Ambiguous / poor** (avoid as standalone):
- "Modern" alone (overloaded — always qualify: "modern Scandinavian")
- "Boho" without constraints
- "Transitional" (industry term, poorly understood by AI)
- "Eclectic" (gives AI too much freedom)

### 4.2 Materials That Render Well

**Level 3 (sweet spot)**: Named material + finish/color + one visual characteristic.

```
SURFACES:
  "honed travertine flooring with natural vein variation"
  "wide-plank walnut flooring with satin finish"
  "handmade zellige tile backsplash in sage green"

UPHOLSTERY:
  "cream boucle armchair with rounded silhouette"
  "cognac leather headboard with visible stitching"
  "rumpled white Belgian linen sheets"

METALS:
  "brushed brass pendant lights"
  "matte black cabinetry with integrated handles"
  "unlacquered brass faucet with natural patina"
```

**Over-specification** (Level 4, no additional benefit): "quarter-sawn white oak with cathedral grain in 7-inch planks with UV-cured polyurethane" — too much detail, may confuse.

### 4.3 Lighting (Most Important Factor for Photorealism)

**Photographic scene descriptions** beat technical specs:

```
BRIGHT/AIRY:
  "Bright natural daylight flooding through large windows, creating
   soft shadows on the floor"

WARM/COZY:
  "Warm ambient lighting from brass table lamps and candles,
   no overhead lighting, golden tones"

EDITORIAL/DRAMATIC:
  "Moody, dramatic lighting like an architectural magazine editorial,
   soft directional light from the left, deep shadows"
```

**Negative lighting instructions** are powerful:
- `"No overhead lighting"` → forces practical light sources (always more realistic)
- `"No flash photography"` → prevents flat, even lighting

**Kelvin values** are understood but descriptive terms work equally well. "Warm candlelight" > "2200K."

### 4.4 Color Palette Specification (Ranked)

1. **Descriptive names + context** (most reliable): "Warm neutral palette: cream walls, sand-colored linen, honey oak floors"
2. **Reference to input image** (very reliable): "Apply the color palette from Image 3"
3. **Named paint colors** (moderate): "Walls in Benjamin Moore White Dove"
4. **Hex codes** (unreliable): Not color-calibrated
5. **Pantone** (ineffective): No calibration in image models

**Best for Remo** — hybrid approach:
```
"Apply the color palette from the inspiration photo: warm neutrals
 with cream, sand, and honey tones. Accent colors: muted sage green
 and terracotta. Avoid: bright whites, cool grays, or any blue tones."
```

### 4.5 Furniture Style Consistency

Name the design language **once, early**, before listing individual pieces:

```
"All furniture in Scandinavian mid-century style: clean lines,
 tapered wooden legs, organic curves, warm wood tones throughout"
```

For intentional mixing, provide a **unifying thread**: "The unifying element is a warm, neutral color palette with brass hardware throughout."

### 4.6 Negative Prompting

**Positive specification almost always outperforms negation.**

| Less effective (negative) | More effective (positive) |
|--------------------------|--------------------------|
| "Avoid brass fixtures" | "Matte black fixtures" |
| "No patterns on upholstery" | "Solid-colored upholstery in a single tone" |
| "No clutter on countertops" | "Clean, minimal countertops with only a single decorative object" |

**Effective negative exclusions** (concrete, specific):
- "No overhead lighting" / "No TV" / "No people" / "No text or watermarks"

**Standard exclusion suffix**:
```
No people in the scene. No text or watermarks.
No duplicate or mirrored furniture. No distorted proportions.
```

---

## 5. Multi-Turn Iterative Editing

### 5.1 Context Turn Structure

Front-load all reference images in Turn 1. Each subsequent edit needs only the annotated image (0-1 images) + text.

```
Turn 1 (context): Room photos (2) + Inspiration (1-3) + Selected design (1) + Context prompt
  → Total: 4-6 images + text

Turn 2+ (edits): Annotated image (1) OR text-only + Edit instructions
  → Total: 0-1 images + text
```

### 5.2 Edit Instruction Format (Enhanced)

Current format works but can be improved:

```
Region #1 (red circle):
  TARGET: The area rug in the center of the room
  ACTION: Replace
  INSTRUCTION: Replace with a cream-colored, hand-woven neutral wool rug
  PRESERVE: Floor visible around rug edges, furniture legs resting on rug
  AVOID: Synthetic materials, bold geometric patterns
```

Key additions over current: **TARGET** (tells model what it's looking at) and **PRESERVE** (what to keep within the affected region).

### 5.3 Annotation Best Practices

The current approach (numbered colored circles) is well-grounded in ViP-LLaVA and Set-of-Mark research:
- Circle outlines (not filled) with `OUTLINE_WIDTH = 4` — correct
- Red/Blue/Green color coding — effective for disambiguation
- Max 3 simultaneous annotations — accuracy degrades beyond this
- Alpha-blended overlay — correct approach

### 5.4 Preventing Unintended Changes (Lock Language)

Enhanced preservation for edit prompts:

```
PRESERVATION RULES (strictly enforced):
- Modify ONLY the elements within the numbered circles
- Every element outside marked regions must remain pixel-identical
- Specifically preserve:
  * Wall colors and textures
  * All furniture not marked for editing
  * Window treatments and natural light
  * Floor material and any rugs not marked
  * Camera angle, perspective, and focal length
  * Overall color temperature and white balance
```

The "anchor image" technique: describe all existing elements before requesting changes, forcing the model to register each one.

### 5.5 Progressive Refinement (Anti-Drift)

**Cumulative changelog** in every edit prompt:

```
Design intent: warm minimalist living room with natural materials

Changes already applied:
1. Replaced blue sofa with beige linen sectional
2. Added warm pendant lighting over dining area

New edit: Replace the rug with a neutral wool rug
Apply only this new edit. All previous changes remain in effect.
```

### 5.6 Chat History Management

Current pruning (keep first 2 + last 2 turns, strip middle images) is sound. Enhancements:

1. **Preserve thought signatures** when stripping images — move to text placeholder part
2. **Replace stripped images with descriptive text**: `"[Previous edit: replaced blue sofa with beige linen sectional. Result accepted.]"`
3. **Consider "compact and restart" after 3 edits**: Take latest image as new baseline, start fresh chat with original room photos + latest design + condensed changelog

### 5.7 Contradiction Handling

Detect conflicts at the workflow/API layer (not in the Gemini prompt). Categorize edits (lighting, color, furniture, layout) and check for reversals:

```
"Your previous edit requested warmer lighting. This edit requests
cooler tones. Would you like to:
(a) Apply cooler tones (replaces warmer lighting)
(b) Keep warm lighting and skip this change"
```

If user confirms override, make it explicit: `"IMPORTANT: This edit intentionally overrides warmer lighting. Apply cooler tones. This is the user's final preference."`

### 5.8 Failure Recovery

**Tier 1 — Rephrase** (edit was partially correct): Add specific "do not" constraints based on failure. "Change ONLY the rug. Do not modify wall colors."

**Tier 2 — Revert and retry** (edit fundamentally failed): Discard the failed turn, start from last accepted image with substantially different instructions.

**Edit quality gate**: Run fast eval after each edit. If artifacts detected or SSIM too low (excessive unintended changes), auto-retry with enhanced constraints before returning to user.

---

## 6. Evaluation Metrics Pipeline

### 6.1 Composite Score Formula

```
Final = 0.40 × spatial(depth, edge_ssim, keypoints)
      + 0.25 × style(clip, gram_loss, color_emd)
      + 0.20 × realism(brisque, niqe, lpips)
      + 0.15 × design(llm_judge_score)
```

### 6.2 Spatial Accuracy (Priority 1)

| Metric | What It Captures | Cost | Implementation |
|--------|-----------------|------|----------------|
| Edge-SSIM (existing) | Wall/window geometry via Canny edges | $0 | Already implemented |
| Depth correlation (NEW) | 3D spatial consistency | $0, ~200ms | Depth Anything V2 ViT-L on both images, scale-align, compute AbsRel + delta1 |
| SIFT keypoint inlier ratio (existing concept) | Feature correspondence | $0 | On architectural features only |
| Line angle histogram (NEW) | Perspective drift detection | $0 | Hough lines, angle histogram chi-squared |

**Depth pipeline**: For Remo specifically, use **Prompt Depth Anything** (CVPR 2025) which accepts iPhone LiDAR as a "prompt" to produce metric depth maps. Compare against Depth Anything V2 output on the generated image.

```python
def depth_spatial_score(gt_depth, pred_depth):
    scale, shift = np.polyfit(pred_depth.flatten(), gt_depth.flatten(), 1)
    aligned = scale * pred_depth + shift
    abs_rel = np.mean(np.abs(aligned - gt_depth) / (gt_depth + 1e-6))
    ratio = np.maximum(aligned / (gt_depth + 1e-6), gt_depth / (aligned + 1e-6))
    delta1 = np.mean(ratio < 1.25)
    return 0.5 * (1.0 - min(abs_rel, 1.0)) + 0.5 * delta1
```

### 6.3 Style Consistency (Priority 2)

| Metric | Weight | What It Captures | Cost |
|--------|--------|-----------------|------|
| CLIP text-image (existing) | 0.25 | Semantic alignment with brief | $0 |
| Gram matrix style loss (NEW) | 0.30 | Texture/pattern fidelity (VGG-19 layers relu1_1 through relu4_1) | $0, ~50ms |
| Color EMD in Lab space (NEW) | 0.25 | Color palette adherence | $0, ~10ms |
| NIMA aesthetic score (NEW) | 0.20 | Overall design quality | $0, ~30ms |

### 6.4 Photorealism (Priority 3)

| Metric | Type | Notes |
|--------|------|-------|
| BRISQUE | No-reference | 0-100 scale (lower better). Fast <10ms. |
| NIQE | No-reference | Natural image statistics. Higher = worse. |
| LPIPS | Reference | Gold standard perceptual similarity. 0.87 correlation with human judgment. |
| NIMA | Aesthetic | Predicts human rating distribution. Interior images typically 5.0-7.5. |

### 6.5 LLM-as-Judge Rubric (Improved)

Replace current 9-criterion variable-scale rubric with hybrid approach:

```
Phase 1: Binary checklist (yes/no)
  1. Room structure preserved (walls/windows/doors match original)?
  2. Requested style applied (not generic)?
  3. Brief color palette present?
  4. No obvious AI artifacts (floating objects, impossible physics)?
  5. Keep items preserved?
  6. Furniture at plausible scale?

Phase 2: 3-point quality ratings (0=poor, 1=acceptable, 2=excellent)
  1. Photorealism quality
  2. Lighting coherence
  3. Design coherence (unified vision)
  4. Color palette execution

Phase 3: Holistic
  - Overall quality: 1-5
  - One-sentence rationale

Scoring: (sum_binary × 5) + (sum_quality × 5) + (holistic × 10) = 0-100
```

**Judge input**: Original room photo + generated image + DesignBrief text. Do NOT include inspiration images (wastes context budget with marginal benefit; brief text captures style intent).

---

## 7. Automated Prompt Optimization

### 7.1 DSPy GEPA (Population-Level, Primary)

Most mature framework for multimodal prompt optimization. Uses Bayesian reflection to learn from past iterations.

| Budget | Iterations | Cost (LLM + generation) |
|--------|-----------|------------------------|
| `auto="light"` | 5-10 | ~$5-8 |
| `auto="medium"` | 20-30 | ~$20-30 |
| `auto="heavy"` | 50+ | ~$45-80 |

Requires 20-50 training examples + 10-20 validation examples. Can use iterative refinement loop (vision LLM judges output) without gold-standard images.

### 7.2 OPRO Meta-Prompt (Lightweight Alternative)

Custom coding agent loop. Maintain sorted history of prompt-score pairs (ascending, max 20).

```
Previous prompts and scores (worst to best):
  Prompt: "..." → Score: 45/100 → Issues: low photorealism, scale errors
  Prompt: "..." → Score: 62/100 → Issues: good realism, generic style
  Prompt: "..." → Score: 78/100 → Issues: lighting inconsistencies

Generate a new prompt scoring higher. Focus on weakest dimensions.
```

Cost: ~$2-4 for 15 iterations. Include failure analysis (not just scores) for 2-3x faster convergence.

### 7.3 TextGrad (Instance-Level Recovery)

For fixing individual difficult cases that fail eval thresholds:

```
1. Generate design → fails threshold
2. LLM critiques output, revises prompt for this specific case
3. Re-generate with revised prompt
4. Cost: ~$0.12/retry, converges in 3-5 steps
```

Use for retry/refinement, not population-level optimization.

### 7.4 A/B Testing

**Bayesian approach** (recommended for expensive experiments):

```python
from scipy.stats import beta as beta_dist

# With 30-50 samples per arm ($2-3 per arm), can detect >10% improvements
# With 100-200 samples per arm ($6-12 per arm), can detect 3-5% improvements
```

**Multi-armed bandits** (TensorZero Track-and-Stop): 37% fewer samples than uniform A/B testing. Adaptive allocation shifts traffic away from underperforming variants. Cost: $25-50 per test instead of $180.

---

## 8. Cost-Efficient Development

### 8.1 Pricing

| Model | Output Cost | Notes |
|-------|-----------|-------|
| `gemini-3-pro-image-preview` 2K | $0.134/image | Same price as 1K |
| `gemini-3-pro-image-preview` 4K | $0.24/image | 79% premium |
| `gemini-2.5-flash-image` | $0.039/image | Cheaper but lower quality |
| Google AI Studio free tier | $0 (500 req/day) | May cover dev budget |
| Batch API | 50% discount | Async processing for experiments |

### 8.2 Four-Stage Development Workflow

1. **Free, unlimited**: Text-only mode. Ask Gemini to *describe* what it would generate. Catches prompt misinterpretations.
2. **~50 calls/day**: Single images with minimal prompts. 10 room types × 5 styles = 50 baselines.
3. **~100 calls/day**: Factorial experiments. 2⁴ fractional factorial = 16 API calls to understand main effects.
4. **~100 calls/day**: Multi-turn editing. Develop the iterative correction workflow.

### 8.3 Caching

Build content-addressable cache (hash prompt + image hashes + params). Store in SQLite. The codebase already has LLM response caching for dev/test.

Structure prompts with static content first, variable content last to maximize Gemini's implicit caching (90% discount on cached input tokens for Gemini 2.5+).

---

## 9. Actionable Recommendations

### Immediate (Prompt Changes Only)

| # | Change | Impact | Effort |
|---|--------|--------|--------|
| 1 | Add explicit image role labels ("Images 1-2 are source room, Images 3+ are style inspiration only") | High — prevents style-from-inspiration bleeding into room geometry | Low |
| 2 | Add layered mutability (immutable architecture / replaceable furniture / additive decor) | High — clearer structural preservation | Low |
| 3 | Add enumeration of specific architectural features after room photo analysis | High — gives model a preservation checklist | Medium |
| 4 | Add lived-in details ("stack of books, draped throw") | Medium — breaks showroom-render look | Low |
| 5 | Translate user "avoid" preferences to positive language | Medium — "no brass" → "matte black fixtures" | Low |
| 6 | Add cumulative changelog to every edit prompt | High — prevents drift across iterations | Medium |

### Short-Term (Code Changes)

| # | Change | Impact | Effort |
|---|--------|--------|--------|
| 7 | Verify thought signature preservation during history pruning | Critical — may cause 400 errors | Medium |
| 8 | Add TARGET + PRESERVE fields to edit instruction format | Medium — improves edit precision | Low |
| 9 | Re-include original room photo in every edit turn | High — spatial anchoring | Low |
| 10 | Add enhanced preservation/lock language to edit prompt | Medium — reduces unintended changes | Low |
| 11 | Implement contradiction detection for conflicting edits | Medium — UX improvement | Medium |

### Medium-Term (Pipeline Enhancements)

| # | Change | Impact | Effort |
|---|--------|--------|--------|
| 12 | Add depth correlation metric (Depth Anything V2) to fast eval | High — catches spatial drift Edge-SSIM misses | Medium |
| 13 | Add Gram matrix style loss + Color EMD to fast eval | Medium — quantifies style adherence | Medium |
| 14 | Add BRISQUE/NIQE + LPIPS to fast eval | Medium — quantifies photorealism | Low |
| 15 | Restructure LLM judge rubric (binary + 3-point + holistic) | Medium — better score calibration | Medium |
| 16 | Implement edit quality gate (auto-retry on low eval scores) | High — catches failures before user sees them | Medium |
| 17 | Set up OPRO-style prompt optimization loop | High — systematic prompt improvement | High |
| 18 | Consider per-image media_resolution (high for room, medium for inspiration) | Low — token optimization | Low |
| 19 | Consider "compact and restart" after 3 edit rounds | Medium — combats degradation cliff | Medium |
| 20 | A/B test [IMAGE] vs [TEXT, IMAGE] for initial generation | Low — might improve focus | Low |

### Recommended Prompt Templates

**Enhanced room preservation** (evolution of `room_preservation_v2.txt`):

```
CRITICAL — Room Structure Preservation:

CAMERA: Preserve the exact camera position, angle, and focal length.
Vanishing points, horizon line, and lens distortion must match precisely.
The viewer must feel they are standing in the exact same spot.

ARCHITECTURE (DO NOT MODIFY — Layer 1, immutable):
{enumerated_architectural_elements}

LIGHTING: Match the original light direction. Natural light enters from
{direction} through {windows}. Shadows must fall consistently.

SPATIAL: New furniture must sit on the floor plane at correct perspective
depth. No floating, clipping, or proportion violations.
{room_dimensions_context}
```

**Enhanced edit prompt** (evolution of `edit.txt`):

```
This interior design image has numbered colored circles marking areas to change.

{edit_instructions}

PRESERVATION RULES:
- Modify ONLY the elements within the numbered circles
- Every element outside marked regions must remain pixel-identical
- Specifically preserve: {list 3-5 key visible elements}
- Camera angle, perspective, and color temperature unchanged

Design intent: {1-sentence brief summary}
Changes already applied: {changelog}

Output a clean photorealistic photograph with ZERO annotations.
```

---

## Tool Recommendations

| Area | Tool | Package | Cost |
|------|------|---------|------|
| Depth estimation | Depth Anything V2 (ViT-L) | `depth-anything-v2` | $0, GPU |
| LiDAR-guided depth | Prompt Depth Anything | `github.com/DepthAnything/PromptDA` | $0, GPU |
| Style (Gram matrix) | PyTorch VGG-19 | `torchvision` | $0 |
| Color comparison | EMD in Lab space | `scipy.stats.wasserstein_distance` | $0 |
| Aesthetic quality | NIMA | `github.com/IDLabMedia/NIMA` | $0, GPU |
| Photorealism (NR) | BRISQUE, NIQE | `imquality` | $0 |
| Perceptual similarity | LPIPS | `lpips` | $0, GPU |
| Population quality | Clean FID | `clean-fid` | $0 |
| Prompt optimization | DSPy GEPA | `dspy` | ~$20-30/run |
| Instance refinement | TextGrad | `textgrad` | ~$0.12/retry |
| A/B testing | TensorZero (bandits) | `tensorzero` | infra |
| Bayesian A/B | SciPy Beta | `scipy` | $0 |

---

## Sources

- Google AI: Image Generation docs — https://ai.google.dev/gemini-api/docs/image-generation
- Google AI: Gemini 3 Developer Guide — https://ai.google.dev/gemini-api/docs/gemini-3
- Vertex AI: Gemini 3 Pro Image — https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-pro-image
- DreamHome-Pano (Feb 2026) — https://arxiv.org/html/2602.06494v1
- ViP-LLaVA: Visual Prompting — https://arxiv.org/html/2312.00784v1
- Multi-turn Consistent Image Editing (Zhou et al., ICCV 2025) — https://arxiv.org/abs/2505.04320
- DSPy GEPA optimizer — https://arxiv.org/abs/2507.19457
- TextGrad (Stanford HAI, Nature 2025) — https://hai.stanford.edu/news/textgrad
- BLPO: Meta AI — https://arxiv.org/abs/2502.03918
- Prompt Depth Anything (CVPR 2025) — https://github.com/DepthAnything/PromptDA

*End of research document.*
