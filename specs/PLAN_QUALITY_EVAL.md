# Quality & Eval Enhancement Plan

## Context

Remo's pipeline (intake -> generation -> editing -> shopping) is functionally complete with real AI wired end-to-end. However, the quality of outputs has not been systematically optimized or measured. The intake agent has a world-class eval framework (rubric scoring, 30 calibrated examples, 6 scenarios, score history). The generation and shopping pipelines have **zero evaluation infrastructure** and use baseline prompts that can be significantly improved based on current Gemini 3 Pro best practices.

This plan focuses on three pillars: (A) generation quality via prompt engineering, (B) shopping search quality via Exa API optimization, and (C) eval infrastructure to measure and track quality over time.

**Branch**: `quality-eval` (from `main` at d7f119f)
**Worktree**: `/Users/claudevcheval/Hanalei/remo-eval`

---

## Pillar A: Generation Prompt Engineering

### A1. Set output resolution to 2K (FREE quality win) — **DONE**

**DONE**: Added `image_config=types.ImageConfig(image_size="2K")` to `IMAGE_CONFIG` in `gemini_chat.py:44-46`. Test `TestGlobalImageConfig` verifies the config. Zero cost change, doubles output resolution.

**File**: `backend/app/utils/gemini_chat.py:44-46`

### A2. Match output aspect ratio to input room photo — **DONE**

**DONE**: Added `_detect_aspect_ratio(image) -> str` (snaps to nearest of 5 Gemini-supported ratios) and `_make_image_config(aspect_ratio)` (builds per-call config with 2K + ratio). `generate_designs()` detects ratio from first room photo and passes per-call config to both options. 7 tests added covering all ratios + edge cases.

**File**: `backend/app/activities/generate.py` -- `_generate_single_option()`

### A3. Rewrite generation.txt in narrative form — **DONE**

**DONE**: Rewrote from bullet-list to narrative paragraphs. Added photography terminology ("full-frame camera", "24mm wide-angle lens", "Architectural Digest", "physically accurate materials"). Added `{option_variant}` placeholder for A5. All template variables preserved. 1 new test verifies narrative content.

**File**: `backend/prompts/generation.txt`

### A4. Enhance room_preservation.txt with spatial anchoring — **DONE**

**DONE**: Expanded from 4 lines to 4 detailed paragraphs covering: vanishing point/focal length/perspective preservation, architectural element immutability, lighting direction matching, and furniture physical constraints (floor plane, clipping, passable walkways).

**File**: `backend/prompts/room_preservation.txt`

### A5. Differentiate the two generated options — **DONE**

**DONE**: Added `_VARIANT_A` (primary style emphasis) and `_VARIANT_B` (complementary variation) constants. `_build_generation_prompt()` accepts `option_variant` param. `generate_designs()` builds separate prompts for each option. 3 new tests verify variant injection and differentiation.

**File**: `backend/app/activities/generate.py` -- `_build_generation_prompt()`, `generate_designs()`

### A6. Enhance _format_room_context() with furniture dimensions (synergizes with LiDAR G5)

**Why**: Currently lists furniture types without dimensions. Including bounding box data from LiDAR enables Gemini to scale new furniture proportionally.

**File**: `backend/app/activities/generate.py` -- `_format_room_context()`

When `RoomDimensions.furniture` entries have dimensions, format them: "Sofa: 2.1m wide x 0.9m deep x 0.8m tall". Include opening dimensions from walls/openings lists.

**Note**: This is already partially done by G5 on LiDAR branch. Deferred to Phase 8 until LiDAR merges.

---

## Pillar B: Shopping Search Quality

### B1. Remove "buy"/"shop" prefixes from Exa queries — **DONE**

**DONE**: Removed all `"buy "` prefixes and `" shop"` suffixes from `_build_search_queries()`. Queries now use natural product descriptions (`f"{ref}"`, `f"{category} {style} {material} furniture"`). Exa's `useAutoprompt` handles purchase intent internally. Tests updated: removed `test_queries_include_shopping_intent`, added `TestNoBuyShopPrefixes` (4 tests).

**File**: `backend/app/activities/shopping.py` -- `_build_search_queries()`

### B2. Switch to `auto` search type with `deep` for HIGH-priority items — **DONE**

**DONE**: Changed `_search_exa()` default from `"neural"` to `"auto"` (adds reranker). Added `search_type` keyword param. `search_products_for_item()` uses `"deep"` for HIGH-priority items, `"auto"` for others. 3 tests added: search type payload verification, HIGH → deep, MEDIUM → auto.

**File**: `backend/app/activities/shopping.py` -- `_search_exa()`, `search_products_for_item()`

### B3. Add `includeDomains` for curated retailer pass — **DONE**

**DONE**: Added `_RETAILER_DOMAINS` list (24 domains matching `_RETAILER_NAMES`). `search_products_for_item()` now runs dual-pass: Pass 1 with `includeDomains` + `includeText`, Pass 2 open web. Existing URL dedup merges results. `_search_exa()` gained `include_domains` keyword param. 3 tests added: retailer domains validation, dual-pass domain presence, dedup verification.

**File**: `backend/app/activities/shopping.py` -- `search_products_for_item()`, `_search_exa()`

### B4. Add `includeText` filter for product page detection — **DONE**

**DONE**: Added `include_text` keyword param to `_search_exa()`. Pass 1 uses `include_text=["add to cart"]` to filter for actual product pages. Pass 2 omits it for broader recall. 2 tests added: includeText payload verification, default omission.

**File**: `backend/app/activities/shopping.py` -- `_search_exa()`

### B5. Add color synonym expansion to query building — **DONE**

**DONE**: Added `_COLOR_SYNONYMS` dict (28 interior design color families, 3 synonyms each) and `_expand_color_synonym()` helper. `_build_search_queries()` appends one synonym-expanded query per item. 6 tests added in `TestColorSynonymExpansion`: known/unknown colors, case insensitivity, query integration, dict coverage.

**File**: `backend/app/activities/shopping.py` -- `_build_search_queries()`, `_expand_color_synonym()`

```python
_COLOR_SYNONYMS: dict[str, list[str]] = {
    "ivory": ["cream", "off-white", "vanilla"],
    "navy": ["dark blue", "indigo", "midnight blue"],
    "sage": ["muted green", "olive", "eucalyptus"],
    "charcoal": ["dark gray", "anthracite", "slate"],
    "walnut": ["dark brown", "espresso", "chocolate"],
    "blush": ["pale pink", "rose", "dusty pink"],
    # ... ~25 more families
}
```

### B6. Add category-adaptive scoring weights — **DONE**

**DONE**: Added `_CATEGORY_WEIGHTS` dict (9 categories: sofa, sectional, rug, lighting, lamp, chandelier, wall art, artwork, mirror) with per-category weight overrides. Added `_get_scoring_weights()` helper that merges overrides on top of base weights (default or LiDAR). `_build_scoring_prompt()` now uses `_get_scoring_weights()` instead of direct weight selection. 10 tests in `TestCategoryAdaptiveWeights` + 6 prompt-level tests.

**File**: `backend/app/activities/shopping.py` -- `_get_scoring_weights()`, `_build_scoring_prompt()`

### B7. Use Exa `summary` with JSON schema for structured product data — **DONE**

**DONE**: Added `summary` schema to `_search_exa()` payload (extracts: product_name, price_usd, material, color, dimensions, in_stock). `_extract_price_text()` now checks `summary.price_usd` first, falls back to regex. Added `_format_summary_section()` that enriches product description with structured data for the scoring model. 8 tests in `TestSummaryPriceExtraction` + 2 in `TestExaSummaryPayload` + 3 prompt-level tests.

**File**: `backend/app/activities/shopping.py` -- `_search_exa()`, `_extract_price_text()`, `_format_summary_section()`

Replace `"contents": {"text": ...}` with:
```python
"contents": {
    "text": {"maxCharacters": 1000},
    "summary": {
        "query": "Extract product details",
        "schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string"},
                "price_usd": {"type": "number"},
                "material": {"type": "string"},
                "color": {"type": "string"},
                "dimensions": {"type": "string"},
                "in_stock": {"type": "boolean"}
            }
        }
    }
}
```

When `summary` is available, use its structured price instead of regex. Fall back to regex for `text` only when `summary` is missing.

Implementation:
- Update `_search_exa()` payload to include `summary` alongside `text`
- Update `_extract_price_text()` to check `product.get("summary", {}).get("price_usd")` first
- Update `score_product()` to pass summary data to scoring prompt when available

---

## Pillar C: Multi-Layer Eval Framework

The eval pipeline uses a **two-tier architecture**: a fast local tier (CLIP + Edge-SSIM + artifact detection) that runs on 100% of outputs at $0 cost, and a deep tier (Claude Vision judge) that runs on 20% of outputs + anomalies flagged by the fast tier.

### C0. Fast Eval Layer -- `backend/app/utils/image_eval.py` (NEW) — **DONE**

**DONE**: Implemented all four metrics (C0a-d) in `backend/app/utils/image_eval.py`. `FastEvalResult` dataclass with composite scoring and `needs_deep_eval` logic. All heavy deps (numpy, torch, open_clip, cv2, scikit-image) gated behind `try/except` with neutral score fallback. Added `eval` optional dependency group to `pyproject.toml`. 27 unit tests in `tests/eval/test_fast_eval.py` covering: dataclass construction, brief-to-text, graceful degradation, composite scoring, thresholds, artifact detection, run_fast_eval integration.

Four local metrics that run in <100ms total, no API calls:

#### C0a. CLIP Text-Image Alignment
- **What**: OpenAI CLIP (ViT-B/32) cosine similarity between generated image and the DesignBrief description
- **Why**: Catches "the image has nothing to do with the brief" failures instantly
- **Threshold**: >= 0.20 = pass (interior design images typically score 0.20-0.35)
- **Text construction**: Concatenate `room_type + mood + colors + textures` into a natural sentence

#### C0b. CLIP Image-Image Similarity (Room Preservation)
- **What**: CLIP cosine similarity between original room photo and generated image
- **Why**: Measures whether the generated design looks like the _same room_
- **Threshold**: >= 0.70 = pass (same room, different furniture should score 0.70-0.85)

#### C0c. Edge-SSIM (Structural Preservation)
- **What**: SSIM computed on Canny edge maps, not raw pixels
- **Why**: Edges capture room geometry (walls, windows, doors) while ignoring furniture/color changes
- **Implementation**: `cv2.Canny()` on both images -> `skimage.metrics.structural_similarity()` on edge maps
- **Threshold**: >= 0.30 = pass

#### C0d. Annotation Artifact Detection (Edit outputs only)
- **What**: HSV color analysis + HoughCircles to detect residual annotation markers
- **Why**: Edit pipeline receives annotated images; if annotations leak into output, that's a hard failure
- **Implementation**: Convert to HSV, mask for annotation-typical colors, check contiguous pixel area + `cv2.HoughCircles()`
- **Output**: Boolean `has_artifacts` + list of detected regions

#### Composite Score
```python
@dataclass
class FastEvalResult:
    clip_text_score: float      # 0-1
    clip_image_score: float     # 0-1
    edge_ssim_score: float      # 0-1
    has_artifacts: bool         # edit outputs only
    composite_score: float      # weighted average
    needs_deep_eval: bool       # True if any metric below threshold
    metrics: dict               # raw metric values for logging
```

Composite: `0.35 * clip_text + 0.35 * clip_image + 0.30 * edge_ssim`. If composite < 0.40 or any individual metric below threshold -> `needs_deep_eval = True`.

#### Dependencies & Graceful Degradation
- `open-clip-torch`, `torch` (CPU only), `scikit-image`, `opencv-python-headless`
- Add to `pyproject.toml` under `[project.optional-dependencies]` as `eval` group
- Import guard: `try: import open_clip except ImportError` -> skip CLIP metrics, return neutral scores
- ~270MB wheels, ~680MB peak RAM (ViT-B/32 is small)

### C1. Deep Eval Layer -- `backend/app/activities/design_eval.py` (NEW) — **DONE**

**DONE**: Implemented three rubric-based evaluators using Claude Sonnet as async multimodal judge. C1a: Generation rubric (100 pts, 9 criteria). C1b: Edit rubric (50 pts, 5 criteria). C1c: Shopping visual rubric (30 pts, 3 criteria). Tag assignment (EXCELLENT/GOOD/ACCEPTABLE/WEAK/FAIL), `_parse_criteria` with clamping, `_run_eval` with async Anthropic client + multimodal content blocks. `CriterionScore`, `GenerationEvalResult`, `EditEvalResult`, `ShoppingVisualEvalResult` dataclasses. 34 unit tests in `tests/eval/test_design_eval.py` covering tags, criteria parsing, rubric completeness, dataclasses, and mocked API evaluations.

Claude Sonnet as a multimodal vision judge, following the existing `intake_eval.py` pattern.

#### C1a. Generation Rubric (100 points, 9 criteria)

| Criterion | Points | Scoring Anchors |
|-----------|--------|-----------------|
| Photorealism | 15 | 15: indistinguishable from photo. 10: good but minor tells. 5: clearly AI. 0: obvious artifacts |
| Style Adherence | 15 | 15: nails the requested style. 10: mostly right. 5: generic. 0: wrong style |
| Color Palette | 10 | 10: matches brief colors + 60/30/10 rule. 7: right family. 3: clashing. 0: wrong |
| Room Preservation | 20 | 20: walls/windows/doors/ceiling identical. 15: minor drift. 5: noticeable changes. 0: different room |
| Furniture Scale | 10 | 10: proportional to room. 7: mostly right. 3: some items wrong. 0: obviously wrong |
| Lighting | 10 | 10: realistic shadows + consistent sources. 7: minor issues. 3: flat. 0: impossible lighting |
| Design Coherence | 10 | 10: unified vision. 7: mostly cohesive. 3: mismatched elements. 0: chaotic |
| Brief Compliance | 5 | 5: all constraints met. 3: most met. 1: few met. 0: ignored |
| Keep Items | 5 | 5: all kept items preserved. 3: most. 0: kept items replaced |

**Tags**: `EXCELLENT` (>=85), `GOOD` (>=70), `ACCEPTABLE` (>=55), `WEAK` (<55), `FAIL` (<40)

#### C1b. Edit Rubric (50 points, 5 criteria)

| Criterion | Points |
|-----------|--------|
| Edit Fidelity | 15 -- did annotated regions change as instructed? |
| Preservation Fidelity | 15 -- did unannotated regions stay unchanged? |
| Artifact Cleanliness | 10 -- no annotation markers in output? |
| Seamless Blending | 5 -- do edited regions blend naturally? |
| Instruction Accuracy | 5 -- does the edit match user's text instructions? |

#### C1c. Shopping Visual Rubric (30 points, 3 criteria)

| Criterion | Points |
|-----------|--------|
| Visual Match | 15 -- does the product look like what was described? |
| Style Consistency | 10 -- does the product fit the room's aesthetic? |
| Scale Appropriateness | 5 -- would this product physically fit in the room? |

#### Implementation Pattern (all rubrics)
```python
async def evaluate_generation(
    original_photo_url: str,
    generated_image_url: str,
    brief: DesignBrief,
    fast_eval: FastEvalResult | None = None,
) -> GenerationEvalResult:
    # 1. Build multimodal content blocks (image + image + JSON brief)
    # 2. Send to Claude Sonnet with structured output schema
    # 3. Parse per-criterion scores + notes
    # 4. Compute total, assign tag
    # 5. Return GenerationEvalResult with fast_eval embedded
```

**Cost**: ~$0.02/eval (Sonnet with 2 images + brief JSON). **Latency**: ~3s.

#### Trigger Strategy
- **Always run fast eval** (C0) on every generated/edited image
- **Deep eval triggers**: (1) fast eval `needs_deep_eval = True`, (2) random 20% sample, (3) explicit `evaluate=True` flag from API
- **Never block the workflow** -- eval runs async, results logged. Workflow returns the image immediately.

### C2. Golden Test Suite — **DONE**

**DONE**: C2a: Created `tests/eval/fixtures/` with manifest.json (3 scenarios: living_room_mcm, bedroom_minimal, kitchen_modern), each with brief.json and metadata.json. Images stored in R2 (URLs null until fixture capture). C2c: Score tracking in `app/utils/score_tracking.py` — append_score, load_history, detect_regression (rolling 5-run average, 10-point threshold). Empty `score_history.jsonl` initialized. 17 tests.

#### C2a. Fixture Management

**Dir**: `backend/tests/eval/fixtures/` (NEW)

```
fixtures/
  manifest.json              # maps scenario -> R2 URLs + expected score ranges
  living_room_mcm/
    brief.json               # DesignBrief
    metadata.json            # expected score ranges, tags, notes
  bedroom_minimal/
    brief.json
    metadata.json
  kitchen_modern/
    brief.json
    metadata.json
```

**Image storage**: Images stored in R2 (too large for git). `manifest.json` maps scenario names to R2 URLs. Test setup downloads fixtures on first run and caches locally in `.cache/eval_fixtures/`.

**Fixture capture**: `scripts/capture_eval_fixture.py` -- runs a real generation, prompts human to review and approve, then uploads to R2 and updates manifest.

#### C2b. Test Suites

| Test File | Tests | Marks |
|-----------|-------|-------|
| `tests/eval/test_generation_eval.py` | 3-5 scenarios, assert total >= 60/100 | `@pytest.mark.integration` |
| `tests/eval/test_edit_eval.py` | 2-3 edit scenarios, assert total >= 35/50 | `@pytest.mark.integration` |
| `tests/eval/test_fast_eval.py` | Unit tests for CLIP/SSIM/artifact detection | `@pytest.mark.eval` (no API) |
| `tests/eval/test_calibration.py` | Known-good and known-bad images, verify scoring | `@pytest.mark.integration` |

#### C2c. Score Tracking & Regression Detection

**File**: `backend/tests/eval/score_history.jsonl`

Each eval run appends a JSON line:
```json
{"timestamp": "...", "scenario": "living_room_mcm", "prompt_version": "v2",
 "fast_eval": {"clip_text": 0.28, "clip_image": 0.76, "edge_ssim": 0.42, "composite": 0.48},
 "deep_eval": {"total": 72, "tag": "GOOD", "criteria": {}},
 "model": "gemini-3-pro-image-preview", "duration_ms": 4200}
```

**Regression detection**: Compare latest score to rolling 5-run average. Alert if total drops >10 points or any criterion drops >5 points.

### C3. Prompt Versioning & A/B Testing — **DONE**

**DONE**: Created `prompts/prompt_versions.json` manifest with active/previous versions for generation (v2/v1), room_preservation (v2/v1), edit (v1). Created versioned prompt files: `generation_v1.txt`, `generation_v2.txt`, `room_preservation_v1.txt`, `room_preservation_v2.txt`, `edit_v1.txt`. Prompt versioning utility in `app/utils/prompt_versioning.py` — `load_versioned_prompt()`, `get_active_version()`, `get_previous_version()`, `list_versions()`. Falls back to unversioned file. 16 tests.

**File**: `backend/prompts/prompt_versions.json` (NEW)

```json
{
  "generation": {"active": "v2", "previous": "v1"},
  "room_preservation": {"active": "v2", "previous": "v1"},
  "edit": {"active": "v1"}
}
```

Prompt files named `generation_v1.txt`, `generation_v2.txt`, etc. `_load_prompt()` reads the active version from manifest. Enables:
- **A/B testing**: Run the same fixture through v1 and v2 prompts, compare scores
- **Rollback**: Switch active version in manifest without code changes
- **Regression tracking**: Score history records prompt version

---

## Execution Sequence

```
Phase 1: Free wins (A1, A2)
    |     2K resolution + aspect ratio matching -- zero cost, immediate quality boost
    |
Phase 2: Prompt rewrite (A3, A4, A5)
    |     Narrative generation prompt + spatial anchoring + option differentiation
    |
Phase 3: Shopping quick wins (B1, B2, B3, B4, B5)
    |     Query engineering + Exa features -- search relevance boost
    |
Phase 4: Shopping scoring (B6, B7)
    |     Category-adaptive weights + Exa summary extraction
    |
Phase 5: Fast eval layer (C0a-d)
    |     CLIP + Edge-SSIM + artifact detection -- runs on 100% of outputs, $0 cost
    |
Phase 6: Deep eval layer (C1a-c)
    |     Claude Vision judge rubrics -- generation, edit, shopping visual
    |
Phase 7: Test infrastructure (C2a-c, C3)
    |     Golden fixtures + test suites + score tracking + prompt versioning
    |
Phase 8: Room context enrichment (A6)
          Furniture dimensions from LiDAR -- synergy with LiDAR branch
```

**Parallelization**: Phases 1-2 (generation) and Phases 3-4 (shopping) are independent. Phase 5 (fast eval) is independent of all others. Phase 6 depends on Phase 5's data types. Phase 7 depends on Phases 5+6. Phase 8 deferred until LiDAR branch merges.

---

## What This Plan Does NOT Include (deferred)

- **Phase 1a contracts** (PLAN_ARCH_EVOLUTION_P1.md) -- skills + cost/feasibility types. Ship separately.
- **LiDAR gap fixes** (G3-G13) -- in progress on LiDAR branch.
- **LPIPS metric** -- perceptual similarity for room preservation. Torch already a dependency; add later alongside CLIP.
- **SerpAPI/Google Shopping** -- second search source. Evaluate after Exa improvements.
- **Two-stage generation** (Flash drafts, Pro finals) -- cost optimization after prompt quality baseline.
- **Fresh context injection after 3 edits** -- edit quality degradation fix.
- **CI integration** -- GitHub Actions workflow for eval regression. Add after eval proves valuable locally.
- **Rich terminal dashboards** -- score visualization. Add after score_history.jsonl accumulates data.

---

## Verification

```bash
# After each phase:
cd backend
.venv/bin/python -m pytest -x -q                           # all tests pass
.venv/bin/python -m ruff check .                            # lint clean
.venv/bin/python -m ruff format --check .                   # format clean
.venv/bin/python -m mypy app/                               # type check clean

# After Phase 5 (eval):
.venv/bin/python -m pytest tests/eval/test_fast_eval.py -xvs -m eval

# Manual quality check after Phases 1-3:
# Run a real generation with the new prompts and compare to previous outputs
# Run a real shopping pipeline and compare match quality
```

---

## Key Files

| File | Changes |
|------|---------|
| `backend/app/utils/gemini_chat.py` | A1: IMAGE_CONFIG with 2K resolution |
| `backend/app/activities/generate.py` | A2: aspect ratio, A5: option differentiation, A6: room context |
| `backend/prompts/generation.txt` | A3: narrative rewrite with photography language |
| `backend/prompts/room_preservation.txt` | A4: spatial anchoring enhancement |
| `backend/app/activities/shopping.py` | B1-B7: query engineering, Exa features, scoring weights |
| `backend/prompts/product_scoring.txt` | B6: category-adaptive weight references |
| `backend/app/utils/image_eval.py` | **NEW** -- C0: CLIP, Edge-SSIM, artifact detection (fast layer) |
| `backend/app/activities/design_eval.py` | **NEW** -- C1: Vision judge rubrics (deep layer) |
| `backend/tests/eval/test_fast_eval.py` | **NEW** -- C2b: unit tests for fast eval (no API) |
| `backend/tests/eval/test_generation_eval.py` | **NEW** -- C2b: golden suite (integration) |
| `backend/tests/eval/test_edit_eval.py` | **NEW** -- C2b: edit eval (integration) |
| `backend/tests/eval/test_calibration.py` | **NEW** -- C2b: score calibration |
| `backend/tests/eval/fixtures/manifest.json` | **NEW** -- C2a: fixture -> R2 URL mapping |
| `backend/tests/eval/score_history.jsonl` | **NEW** -- C2c: regression tracking |
| `backend/prompts/prompt_versions.json` | **NEW** -- C3: version manifest |
| `backend/tests/test_shopping.py` | Update for B1-B7 changes |
| `backend/tests/test_generate.py` | Update for A1-A5 changes |
| `backend/pyproject.toml` | `[eval]` deps: `open-clip-torch`, `torch`, `scikit-image`, `opencv-python-headless` |
