# T2: Image Generation Pipeline — Implementation Sub-Plan

> **Team**: T2 (Image Generation Pipeline)
> **Source**: `specs/PLAN_FINAL.md` v2.0
> **Worktree**: `/Hanalei/remo-gen`

---

## 1. Big Picture

Remo is an AI-powered room redesign app: users photograph their room, describe their style, and receive photorealistic redesign options they can iteratively refine, culminating in a downloadable design image and a shoppable product list with real purchase links.

### Overall Architecture

```
                    ┌─────────────────────────┐
                    │     iOS App (SwiftUI)    │
                    │                          │
                    │  NavigationStack-driven   │
                    │  Polls Temporal state     │
                    │  Sends signals via API    │
                    └────────────┬─────────────┘
                                 │ HTTPS
                                 ▼
                    ┌─────────────────────────┐
                    │   FastAPI Gateway        │
                    │                          │
                    │  POST /projects          │ ──→ Temporal: start workflow
                    │  POST /projects/{id}/*   │ ──→ Temporal: send signal
                    │  GET  /projects/{id}     │ ──→ Temporal: query state
                    │  DELETE /projects/{id}   │ ──→ Temporal: cancel workflow
                    │  GET  /health            │ ──→ Health check
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Server         │
                    │   (Temporal Cloud)  │
                    │                          │
                    │  DesignProjectWorkflow    │
                    │   ├── wait: photos        │
                    │   ├── wait: scan          │
                    │   ├── wait: intake        │
                    │   ├── activity: generate  │  ← T2
                    │   ├── wait: select/restart│
                    │   ├── activity: iterate   │  ← T2
                    │   ├── wait: approve       │
                    │   ├── activity: shopping  │
                    │   └── timer: purge        │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Worker         │
                    │   (separate Railway svc)  │
                    │                          │
                    │  Activities:              │
                    │   ├── run_intake_chat    │ → Claude (T3)
                    │   ├── generate_designs   │ → Gemini (T2) ★
                    │   ├── generate_inpaint   │ → Gemini (T2) ★
                    │   ├── generate_regen     │ → Gemini (T2) ★
                    │   ├── generate_shopping  │ → Claude + Exa (T3)
                    │   └── purge_project      │ → R2 + DB (T0)
                    └──┬──────┬──────┬─────────┘
                       │      │      │
                ┌──────┘  ┌───┘  ┌───┘
                ▼         ▼      ▼
           Google AI   Anthropic    Exa     Cloudflare R2    Railway PG
           (Gemini 3   (Claude)     API     (images)         (metadata)
           Pro Image)
```

### Where T2 Fits

T2 owns **ALL image generation** — initial designs, lasso inpainting, and full regeneration. You work with Gemini models via the Google AI API. Your outputs are the visual core of the product. The three `generate_*` activities marked with ★ above are entirely yours.

### 4-Team Structure

| Team | Focus |
|------|-------|
| **T0: Platform & Backend** | Contracts, Temporal, API, DB, R2, CI/CD, integration lead |
| **T1: iOS App** | All SwiftUI/UIKit UI screens and navigation |
| **T2: Image Gen Pipeline** | Gemini generation, inpainting, regeneration (YOU) |
| **T3: AI Agents** | Intake chat + shopping list (Claude-based) |

### Phase Overview

| Phase | Focus | T2 Role |
|-------|-------|---------|
| **P0: Foundation** | Contracts, scaffold, infra | Gemini quality spike (MANDATORY first task) |
| **P1: Independent Build** | All teams build in parallel | All 3 activities + mask utility + prompt templates |
| **P2: Integration** | Wire real activities into workflow | Quality test suite; support integration debugging |
| **P3: Stabilization** | Bug fixes, polish | Bug fixes from integration testing |

---

## 2. Your Team

- **Worktree**: `/Hanalei/remo-gen`
- **Branch prefix**: `team/gen/*`
- **Setup command**:
  ```bash
  git worktree add /Hanalei/remo-gen team/gen/gemini-spike
  ```

---

## 3. What You Own

| File | Description |
|------|-------------|
| `backend/activities/generate.py` | `generate_designs` activity |
| `backend/activities/inpaint.py` | `generate_inpaint` activity |
| `backend/activities/regen.py` | `generate_regen` activity |
| `backend/utils/image.py` | Mask rendering, image processing (shared with T0) |
| `backend/prompts/generation.txt` | Initial 2-option generation prompt template |
| `backend/prompts/inpaint.txt` | Lasso inpainting prompt template |
| `backend/prompts/regeneration.txt` | Full regenerate prompt template |
| `backend/prompts/room_preservation.txt` | Shared room preservation clause |

---

## 4. Deliverables by Phase

### P0: Foundation

| Deliverable | Success Metric |
|------------|----------------|
| Gemini quality spike (MUST be first task) | Both models tested on 3 room photos; mask precision, photorealism, architecture preservation scored per model |
| Model selection decision document | Side-by-side results; winning model chosen with rationale; escalation plan if neither passes |

### P1: Independent Build

| Deliverable | Success Metric |
|------------|----------------|
| `generate_designs` activity | Takes room photos + brief → returns 2 design image URLs in R2 |
| Mask generation utility (polygon → binary mask) | Renders 1-3 polygon regions into a correctly scaled binary mask |
| Prompt template library | `prompts/` directory with versioned templates for each mode |
| `generate_inpaint` activity | Takes base image + mask + instructions → returns revised image URL; SSIM > 0.98 outside mask |
| `generate_regen` activity | Takes context + feedback → returns new design URL; visibly different from input |

### P2: Integration & Testing

| Deliverable | Success Metric |
|------------|----------------|
| Quality test suite | 5+ test cases per activity with scored results; 70%+ meet quality bar |

---

## 5. Dependencies

### What T2 Depends On

- **T0's contracts (P0 exit gate)**: T2 needs the Pydantic `*Input/*Output` models from `backend/models/contracts.py` before writing production activity code. These define the exact input/output shapes for `generate_designs`, `generate_inpaint`, and `generate_regen`.
- **T0's R2 utilities**: `backend/utils/r2.py` for uploading generated images and downloading source images.

### What T2 Does NOT Depend On

- **T1 (iOS)**: No dependency. T2 never touches iOS code.
- **T3 (AI Agents)**: No dependency. T2 and T3 are completely independent.

### What Can Start Immediately (No Dependencies)

The **Gemini quality spike** can start immediately. It's just API calls with test images — no contracts, no R2, no Temporal needed.

---

## 6. Technical Details

### P0 Gemini Quality Spike (MANDATORY FIRST TASK)

**Why**: Both Gemini models are preview-stage. We've never tested either for room redesign with precise region masking. If you pick a model blind and build 3 activities on it during P1, then discover at P2 integration that masks bleed or room architecture distorts, that's an entire phase wasted. This spike is a 2-3 hour comparative test that picks the best model before any real code is written.

**What to test** (run identical tests on BOTH models):

1. Upload 3 real room photos to **both** Gemini 3 Pro Image and Gemini 2.5 Flash Image
2. Generate redesigns with a sample brief on each
3. Test inpainting with precise polygon masks on each
4. Score each model on: (a) mask boundary adherence, (b) photorealism, (c) room architecture preservation, (d) style consistency
5. Document results with side-by-side screenshots

**Decision gate**:

- **One or both models pass** (mask boundary bleeding ≤ ~5% of non-masked area in 4+ of 5 test cases) → Pick the higher-scoring model. Build all activities on it. Proceed to P1.
- **Neither model passes** → Escalate. Evaluate alternatives (dedicated inpainting models, hybrid approach, or adjusted quality bar). Do NOT proceed to P1 until resolved.

**Deliverables**:
- A test script or notebook with 3+ room photos, run against both models
- Scored results for each evaluation criterion, per model
- Side-by-side screenshots of generated/inpainted images
- Written model selection decision document (which model won, why)

### Image Generation: All Modes

| Mode | Input | Output | Latency | Cost |
|------|-------|--------|---------|------|
| **Initial (2 options)** | Room photos + brief + inspiration | 2 redesign images (1K/2K) | ~15-30s parallel | ~$0.268 |
| **Lasso Inpaint** | Current image + binary mask + instructions | Revised image (masked regions changed) | ~15-30s | ~$0.134 |
| **Full Regenerate** | Room photos + brief + feedback + history | New full design image | ~15-30s | ~$0.134 |

### Activity Implementations

#### `generate_designs` — Initial Design Generation

**Input contract**: `GenerateDesignsInput`
```python
class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None
```

**Output contract**: `GenerateDesignsOutput`
```python
class GenerateDesignsOutput(BaseModel):
    options: list[DesignOption]          # exactly 2
```

**What happens inside**:
1. Download room photos and inspiration photos from R2 URLs
2. Build the generation prompt from `prompts/generation.txt` + `prompts/room_preservation.txt`, incorporating the design brief (style, colors, mood, constraints, etc.)
3. If inspiration photos are provided, include them with their notes
4. If room dimensions are available, include them for furniture scale accuracy
5. Call the selected Gemini model **twice in parallel** to generate 2 distinct design options
6. Upload both generated images to R2 under `projects/{project_id}/generated/option_0.png` and `option_1.png`
7. Return 2 `DesignOption` objects with R2 URLs and captions

**Error handling**:
- Rate limits → `ApplicationError("Gemini rate limited", non_retryable=False)` — Temporal retries automatically
- Content policy violation → `ApplicationError("Content policy violation", non_retryable=True)` — report to user
- Image download failure → `ApplicationError("Failed to download source image", non_retryable=False)` — retry
- R2 upload failure → `ApplicationError("Failed to upload generated image", non_retryable=False)` — retry

#### `generate_inpaint` — Lasso Inpainting

**Input contract**: `GenerateInpaintInput`
```python
class GenerateInpaintInput(BaseModel):
    base_image_url: str
    regions: list[LassoRegion]           # 1-3 regions
```

**Output contract**: `GenerateInpaintOutput`
```python
class GenerateInpaintOutput(BaseModel):
    revised_image_url: str
```

**What happens inside**:
1. Download the base image from R2
2. Generate a binary mask from the `regions` using the mask generation utility (see below)
3. Build the inpaint prompt from `prompts/inpaint.txt` + `prompts/room_preservation.txt`, incorporating each region's `action`, `instruction`, `avoid_tokens`, and `style_nudges`
4. Call the selected Gemini model with the base image, mask, and prompt
5. Upload the revised image to R2 under `projects/{project_id}/generated/revision_{n}.png`
6. Return the R2 URL

**Error handling**:
- Rate limits → retryable
- Content policy violation → non-retryable
- Mask generation failure (invalid polygons) → non-retryable
- SSIM check (if implemented): if non-masked regions differ significantly, log a warning but still return result

#### `generate_regen` — Full Regeneration

**Input contract**: `GenerateRegenInput`
```python
class GenerateRegenInput(BaseModel):
    room_photo_urls: list[str]
    design_brief: DesignBrief | None = None
    current_image_url: str
    feedback: str
    revision_history: list[RevisionRecord] = []
```

**Output contract**: `GenerateRegenOutput`
```python
class GenerateRegenOutput(BaseModel):
    revised_image_url: str
```

**What happens inside**:
1. Download room photos and current image from R2
2. Build the regen prompt from `prompts/regeneration.txt` + `prompts/room_preservation.txt`, incorporating:
   - The original design brief
   - The user's textual feedback
   - Revision history (so the model understands what's been tried before)
3. Call the selected Gemini model with room photos as reference, current image as context, and the regen prompt
4. Upload the new design image to R2
5. Return the R2 URL

**Error handling**:
- Same pattern as `generate_designs` — rate limits retryable, content policy non-retryable

### Prompt Template Strategy

Create a `prompts/` directory with versioned templates:

```
backend/prompts/
  generation.txt          # Initial 2-option generation
  inpaint.txt             # Lasso inpainting
  regeneration.txt        # Full regenerate
  room_preservation.txt   # Shared clause (camera angle, walls, architecture)
```

**Room structure preservation clause** (included in ALL generation calls):
```
Preserve the exact camera angle, room geometry, walls, ceiling, windows,
doors, and floor plane from the reference photo. Do not modify the room
architecture or viewing perspective.
```

Templates use simple string formatting (e.g., `{room_type}`, `{style_description}`, `{feedback}`). No Jinja — keep it simple.

**Versioning**: Templates are plain text files checked into git. Version history is tracked by git itself. If a template change is needed, update the file and note the change in the PR description.

### Mask Generation Utility

The mask generation utility lives in `backend/utils/image.py` (shared with T0).

**Input**: List of `LassoRegion` objects, each containing:
- `path_points: list[tuple[float, float]]` — normalized 0-1 coordinates from the iOS app
- Additional metadata (action, instruction, etc.) — used by the prompt, not the mask

**Process**:
1. Receive `path_points` per region (normalized 0-1 values from iOS)
2. Scale coordinates to actual image dimensions: `x_px = x_norm * image_width`, `y_px = y_norm * image_height`
3. Render filled polygons onto a blank (black) image using Pillow (`ImageDraw.polygon`) or OpenCV (`cv2.fillPoly`)
4. Apply small Gaussian feather (2-3px sigma) at mask boundaries for better blending
5. Composite multiple regions (1-3) into a single binary mask — white = edit area, black = preserve
6. Return the mask as a PIL Image or bytes

**Coordinate system**: iOS sends normalized 0-1 coordinates. The mask utility scales to the actual pixel dimensions of the base image. This decouples the iOS drawing surface from the generated image resolution.

**Edge cases**:
- Single-point or two-point regions → reject (minimum 3 points for a polygon)
- Self-intersecting polygons → accept (Pillow/OpenCV handle this via even-odd fill rule)
- Overlapping regions → union (white + white = white)

### Gemini Model Details

| Role | Model | Model ID |
|------|-------|----------|
| **Candidate A** | Gemini 3 Pro Image | `gemini-3-pro-image-preview` |
| **Candidate B** | Gemini 2.5 Flash Image | `gemini-2.5-flash-preview-image-generation` |

Both models use the **same Google AI API key** and the **same SDK** (`google-genai` or `google-generativeai`). Switching between them requires changing only the model ID string — zero additional provider setup, zero new credentials.

**Model selection**: The P0 quality spike tests both models head-to-head. The winner is used for all activities. It's also possible to use models selectively (e.g., one model for generation, the other for inpainting) if the spike reveals different strengths.

---

## 7. Contracts You Implement

T2 **consumes** these contracts — T0 owns them. Do not modify; request changes via T0.

### Activity Input/Output Models

```python
# === Activity Input/Output (from backend/models/contracts.py) ===

class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None

class GenerateDesignsOutput(BaseModel):
    options: list[DesignOption]          # exactly 2

class GenerateInpaintInput(BaseModel):
    base_image_url: str
    regions: list[LassoRegion]           # 1-3 regions

class GenerateInpaintOutput(BaseModel):
    revised_image_url: str

class GenerateRegenInput(BaseModel):
    room_photo_urls: list[str]
    design_brief: DesignBrief | None = None
    current_image_url: str
    feedback: str
    revision_history: list[RevisionRecord] = []

class GenerateRegenOutput(BaseModel):
    revised_image_url: str
```

### Shared Types Used by T2

```python
class DesignBrief(BaseModel):
    room_type: str
    occupants: str | None = None
    pain_points: list[str] = []
    keep_items: list[str] = []
    style_profile: StyleProfile | None = None
    constraints: list[str] = []
    inspiration_notes: list[InspirationNote] = []

class StyleProfile(BaseModel):
    lighting: str | None = None       # warm / cool / bright natural
    colors: list[str] = []
    textures: list[str] = []
    clutter_level: str | None = None  # minimal / curated / layered
    mood: str | None = None

class InspirationNote(BaseModel):
    photo_index: int
    note: str
    agent_clarification: str | None = None

class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []     # JSONB-friendly
    openings: list[dict] = []  # doors, windows

class LassoRegion(BaseModel):
    region_id: int                       # 1-3
    path_points: list[tuple[float, float]]  # normalized 0-1
    action: str                          # Replace, Remove, Change, Resize, Reposition
    instruction: str                     # min 10 chars
    avoid_tokens: list[str] = []
    style_nudges: list[str] = []

class DesignOption(BaseModel):
    image_url: str
    caption: str

class RevisionRecord(BaseModel):
    revision_number: int
    type: str                            # "lasso" or "regen"
    base_image_url: str
    revised_image_url: str
```

**Contract change policy**: If you need a contract change, message T0 immediately. Additive changes (new optional fields) are fast-merged. Breaking changes require discussion with all consuming teams.

---

## 8. Cost Per Call

| Component | Cost |
|-----------|------|
| Initial generation (2 x Gemini 3 Pro Image) | $0.268 |
| Lasso inpaint (1 x Gemini 3 Pro Image) | $0.134 |
| Full regenerate (1 x Gemini 3 Pro Image) | $0.134 |
| Lasso iterations (3 x Gemini 3 Pro Image, typical) | $0.402 |

**Per-session cost from T2 activities** (typical): ~$0.804 (initial + 3 lasso + 1 regen)

---

## 9. Git & Collaboration

### Worktree Setup

```bash
# From the main remo repo:
git worktree add /Hanalei/remo-gen team/gen/gemini-spike
```

### Branch Naming

```
team/gen/gemini-spike        # P0: Quality spike
team/gen/generate-designs    # P1: Initial generation activity
team/gen/mask-utility        # P1: Mask generation utility
team/gen/inpaint             # P1: Inpainting activity
team/gen/regen               # P1: Regeneration activity
team/gen/prompt-templates    # P1: Prompt template library
team/gen/quality-tests       # P2: Quality test suite
```

### PR Merge Order

T2's PRs are in **group 6** of the master merge order — they can merge in any order during P1 (after T0's P0 gates are merged):

```
1-5. T0 P0 PRs (scaffold, contracts, temporal, api-gateway, swift-models)
──── All teams can work independently after this point (P1) ────
6.   Activity PRs (any order, during P1):
     - team/gen/generate-designs   → main
     - team/gen/inpaint           → main
     - team/gen/regen             → main
     ... (other teams' PRs)
7.   team/platform/integration-*   → main (P2)
```

### Review Process

- T0 reviews T2 PRs for **contract compliance** (correct input/output shapes, proper error handling)
- PR size: 200-400 lines preferred; single-purpose
- Merge strategy: squash merge to main

---

## 10. Success Metrics

| Metric | Verification |
|--------|-------------|
| `generate_designs` produces 2 images | Activity returns 2 `DesignOption` with valid R2 URLs |
| Images are photorealistic | Human eval: 7/10+ score for 70%+ of generations |
| Room architecture preserved | Edge map correlation > 0.7 with original photo |
| Inpainting respects mask | SSIM > 0.98 for non-masked regions |
| Regeneration incorporates feedback | Human eval: feedback addressed in 80%+ of cases |
| All activities complete in time | < 3 minutes per activity |
| Prompt templates exist | `prompts/` directory with all generation modes |

---

## 11. Code Quality

### Testing Requirements

| Test Type | Description | Tools |
|-----------|-------------|-------|
| Real API calls | Each activity tested with real Gemini API and test room photos | pytest + real API keys |
| Contract validation | Output validates against Pydantic output model | pytest + Pydantic |
| Quality test suite (P2) | 5+ test cases per activity with scored results; 70%+ meet quality bar | pytest + human eval |
| Mask utility tests | Unit tests for polygon rendering, coordinate scaling, feathering, edge cases | pytest + Pillow |

### Error Handling

**Retryable errors** (Temporal retries automatically via `RetryPolicy`):
- Rate limits from Gemini API
- Transient network errors
- R2 upload/download failures
- Temporary Gemini service issues

**Non-retryable errors** (report to user via `WorkflowError`):
- Content policy violations
- Invalid input (e.g., no room photos, invalid mask polygons)
- Persistent API failures after retry exhaustion

```python
# Pattern for all activities:
from temporalio import activity
from temporalio.exceptions import ApplicationError

@activity.defn
async def generate_designs(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    try:
        # ... generation logic ...
        pass
    except RateLimitError:
        raise ApplicationError("Gemini rate limited", non_retryable=False)
    except ContentPolicyError:
        raise ApplicationError("Content policy violation", non_retryable=True)
```

### Activity Design Principle

Activities are **stateless**. They receive all inputs via the contract, produce outputs, and have no side effects beyond R2 uploads. No database writes, no workflow state mutations.

---

## 12. Risks & Open Questions

### Risks Relevant to T2

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Neither Gemini model passes mask precision threshold | High | P0 spike tests both models head-to-head; if neither passes, escalate to evaluate alternatives before P1 |
| Gemini preview model instability (rate limits, deprecation) | High | Both models use same API key/SDK; can swap model ID instantly if one becomes unstable |
| Generated images not photorealistic enough | Medium | Prompt engineering iteration; multiple prompt versions; human eval feedback loop |
| Room architecture not preserved in generation | Medium | Room preservation clause in all prompts; edge map correlation testing |
| Mask feathering produces visible artifacts | Low | Adjustable Gaussian sigma; visual inspection during quality spike |

### Open Questions

| Question | Decision Needed By | Owner |
|----------|-------------------|-------|
| Which Gemini model wins the head-to-head spike? | P0 end | T2 (you decide based on spike results) |
| Optimal Gaussian feather sigma for mask boundaries? | P1 mid | T2 (determined empirically during implementation) |
| Should SSIM check be enforced (reject bad inpaints) or advisory (log warning)? | P1 end | T2 + T0 discussion |
| Caption generation for DesignOption — model-generated or template? | P1 start | T2 (start with template, upgrade if time permits) |

---

## Reference

For the full system context — workflow code, API endpoints, data model, iOS architecture, and integration plan — see `specs/PLAN_FINAL.md`.

*End of T2 sub-plan.*
