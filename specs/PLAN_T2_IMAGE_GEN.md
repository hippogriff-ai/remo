# T2: Image Generation Pipeline — Implementation Sub-Plan

> **Team**: T2 (Image Generation Pipeline)
> **Source**: `specs/PLAN_FINAL.md` v2.1 (revised for annotation-based editing)
> **Date**: 2026-02-11
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
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Server         │
                    │  DesignProjectWorkflow    │
                    │   ├── wait: photos        │
                    │   ├── wait: scan          │
                    │   ├── wait: intake        │
                    │   ├── activity: generate  │  ← T2 (standalone)
                    │   ├── wait: select/restart│
                    │   ├── activity: edit      │  ← T2 (multi-turn chat, loop ×5)
                    │   ├── wait: approve       │
                    │   ├── activity: shopping  │
                    │   └── timer: purge        │
                    └────────────┬─────────────┘
                                 │
                    ┌────────────▼─────────────┐
                    │   Temporal Worker         │
                    │                          │
                    │  Activities:              │
                    │   ├── run_intake_chat    │ → Claude (T3)
                    │   ├── generate_designs   │ → Gemini (T2) ★
                    │   ├── edit_design        │ → Gemini (T2) ★ multi-turn
                    │   ├── generate_shopping  │ → Claude + Exa (T3)
                    │   └── purge_project      │ → R2 + DB (T0)
                    └──┬──────┬──────┬─────────┘
                       │      │      │
                Google AI   Anthropic    Exa     Cloudflare R2
                (Gemini 3   (Claude)     API     (images + chat history)
                Pro Image)
```

### Where T2 Fits

T2 owns **ALL image generation and editing** — initial design generation and annotation-based iterative editing. You work with Gemini models via the Google AI API. Your outputs are the visual core of the product.

**Key architecture change**: Instead of separate mask-based inpainting and full regeneration activities, T2 uses a **single multi-turn Gemini chat session** for all iterative editing. Users mark areas on the generated image with numbered annotations (circles, badges), and Gemini edits only those areas while preserving everything else. This is Google's intended interaction pattern — the same approach used by the Gemini app's Markup tool.

### 4-Team Structure

| Team | Focus |
|------|-------|
| **T0: Platform & Backend** | Contracts, Temporal, API, DB, R2, CI/CD, integration lead |
| **T1: iOS App** | All SwiftUI/UIKit UI screens and navigation |
| **T2: Image Gen Pipeline** | Gemini generation + annotation-based editing (YOU) |
| **T3: AI Agents** | Intake chat + shopping list (Claude-based) |

### Phase Overview

| Phase | Focus | T2 Role |
|-------|-------|---------|
| **P0: Foundation** | Contracts, scaffold, infra | Gemini quality spike (MANDATORY first task) |
| **P1: Independent Build** | All teams build in parallel | 2 activities + annotation utility + chat manager + prompts |
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
| `backend/app/activities/generate.py` | `generate_designs` activity (standalone Gemini calls) |
| `backend/app/activities/edit.py` | `edit_design` activity (multi-turn Gemini chat) |
| `backend/app/utils/image.py` | Annotation drawing (circles, numbered badges on images) |
| `backend/app/utils/gemini_chat.py` | Gemini chat session manager (create, serialize to R2, restore) |
| `backend/prompts/generation.txt` | Initial 2-option generation prompt template |
| `backend/prompts/edit.txt` | Annotation-based edit prompt template |
| `backend/prompts/room_preservation.txt` | Shared room preservation clause |

**Files removed from T2 scope** (no longer needed):
- ~~`backend/app/activities/inpaint.py`~~ — replaced by `edit.py`
- ~~`backend/app/activities/regen.py`~~ — replaced by `edit.py`
- ~~`backend/prompts/inpaint.txt`~~ — replaced by `edit.txt`
- ~~`backend/prompts/regeneration.txt`~~ — replaced by `edit.txt`

---

## 4. Deliverables by Phase

### P0: Foundation

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 1 | Gemini quality spike (MUST be first task) | Both models tested on 3 room photos: (a) initial generation quality, (b) annotation-based editing quality (numbered circles → targeted edits), (c) chat history serialization round-trip (thought signatures survive). Decision: which model wins. |
| 2 | Model selection decision document | Side-by-side results; winning model chosen with rationale; escalation plan if neither passes |

### P1: Independent Build

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 3 | Annotation drawing utility (`utils/image.py`) | Draws 1-3 numbered circle badges with distinct colors on PIL Image; badges clearly visible at 1024px |
| 4 | Gemini chat session manager (`utils/gemini_chat.py`) | Creates new session, serializes to R2 (including thought signatures), deserializes and continues. Round-trip test passes. |
| 5 | Prompt template library (`prompts/`) | 3 template files: `generation.txt`, `edit.txt`, `room_preservation.txt` |
| 6 | `generate_designs` activity | Takes room photos + brief → returns 2 design image URLs in R2. Two parallel Gemini calls, no chat session. |
| 7 | `edit_design` activity | First call: bootstraps chat with refs + selected image. Subsequent calls: draws annotations on image, sends to Gemini in chat, stores updated history. Returns revised image URL. |

### P2: Integration & Testing

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 8 | Quality test suite | 5+ test cases per activity with scored results; 70%+ meet quality bar; annotation edits preserve non-edited regions |

---

## 5. Dependencies

### What T2 Depends On

- **T0's contracts (P0 exit gate)**: T2 needs the Pydantic `*Input/*Output` models from `backend/models/contracts.py`.
- **T0's R2 utilities**: `backend/utils/r2.py` for uploading generated images, downloading source images, and storing/retrieving Gemini chat history.

### What T2 Does NOT Depend On

- **T1 (iOS)**: No dependency. T2 never touches iOS code.
- **T3 (AI Agents)**: No dependency. T2 and T3 are completely independent.

### What Can Start Immediately (No Dependencies)

The **Gemini quality spike** can start immediately. It's just API calls with test images — no contracts, no R2, no Temporal needed.

---

## 6. Technical Details

### P0 Gemini Quality Spike (MANDATORY FIRST TASK)

**Why**: Both Gemini models are preview-stage. We must validate that annotation-based editing works for interior design before building the pipeline. The spike now tests three things: initial generation quality, annotation-based editing quality, and chat history persistence.

**What to test** (run identical tests on BOTH models):

1. **Initial generation**: Upload 3 real room photos + a sample brief to both Gemini 3 Pro Image and Gemini 2.5 Flash Image. Score photorealism and room architecture preservation.

2. **Annotation-based editing**: On each generated image, draw numbered circles (using Pillow) on 2-3 areas. Send the annotated image with edit instructions. Score: (a) did the model correctly identify the circled areas? (b) were edits applied only to marked areas? (c) were unmarked areas preserved?

3. **Chat history round-trip**: Create a multi-turn chat, serialize the history (including thought signatures for Gemini 3 Pro), deserialize, and send a follow-up edit. Verify the chain doesn't break with a 400 error.

4. **Text-only editing**: In the same chat session, send a text-only edit instruction (no annotation). Verify the model applies changes without needing visual markup.

**Passing criteria per test case**:

| Criterion | Pass | Fail |
|-----------|------|------|
| **Correct area edited** | Gemini modifies the area inside/near the drawn circle | Edits wrong area or ignores circle entirely |
| **Non-annotated areas preserved** | Areas outside circles are visually unchanged (SSIM > 0.95 vs original for non-circled regions) | Unrelated areas change significantly |
| **Output image is clean** | No circles, badges, or annotation artifacts in Gemini's output | Circles/numbers appear in the returned image |
| **Instruction followed** | The edit matches the text instruction (human eval) | Edit is unrelated to instruction |
| **Chat round-trip works** | Serialize → deserialize → follow-up edit succeeds | 400 error on follow-up, or context lost |

**Decision gate**:

- **Pass**: 4+ of 5 test cases meet ALL criteria above, chat round-trip works.
- **Fail**: Escalate. Evaluate alternatives before P1.

**If output images contain annotation artifacts**: Add explicit prompt instruction — "Do not include any annotations, circles, numbers, or markers in your output image. Return only the edited room photograph." Re-test. If still failing, this is a model-level blocker.

**Model selection constraint**: `gemini-2.5-flash-image` supports max 3 input images. Our workflow sends 2 room photos + 3 inspiration photos + the generated/annotated image = up to 6 images. **`gemini-3-pro-image-preview` (up to 14 images) is likely required.** The spike will confirm.

| Feature | `gemini-2.5-flash-image` | `gemini-3-pro-image-preview` |
|---|---|---|
| Max input images | **3 recommended** | **Up to 14** |
| Output resolution | 1024px | Up to 4K |
| Cost per output image | ~$0.039 | ~$0.134 (1K/2K) |
| Thought signatures | Not required | **Required, strictly enforced** |
| Multi-turn editing | Supported | Best-in-class |
| Internal reasoning | No | Yes (cannot be disabled) |

### Two Activities, Not Three

The old plan had 3 activities: `generate_designs`, `generate_inpaint`, `generate_regen`. The annotation-based approach collapses the last two into a single `edit_design` activity:

| Activity | When Called | Gemini Session |
|----------|-----------|----------------|
| `generate_designs` | After intake completes | **No chat session** — two independent parallel Gemini calls |
| `edit_design` | After user selects a design and wants to iterate | **Multi-turn chat** — first call bootstraps, subsequent calls continue |

### Activity: `generate_designs` (Standalone)

**Input contract**: `GenerateDesignsInput`
**Output contract**: `GenerateDesignsOutput`

**What happens inside**:
1. Download room photos and inspiration photos from R2 URLs
2. Build the generation prompt from `prompts/generation.txt` + `prompts/room_preservation.txt`, incorporating the design brief
3. Call the selected Gemini model **twice in parallel** to generate 2 distinct design options
4. Upload both generated images to R2 under `projects/{project_id}/generated/option_0.png` and `option_1.png`
5. Return 2 `DesignOption` objects with R2 URLs and captions

**No chat session is created here.** The user picks one option, and the editing chat starts only when they request their first edit.

**Error handling**:
- Rate limits → `ApplicationError("Gemini rate limited", non_retryable=False)` — Temporal retries automatically
- Content policy violation → `ApplicationError("Content policy violation", non_retryable=True)` — report to user
- Image download failure → `ApplicationError("Failed to download source image", non_retryable=False)` — retry

### Activity: `edit_design` (Multi-Turn Chat)

**Input contract**: `EditDesignInput`
**Output contract**: `EditDesignOutput`

This single activity handles BOTH annotation-based edits AND text-only regeneration — they're just different chat turns.

**What happens inside**:

**First call** (no `chat_history_key` in input):
1. Download room photos, inspiration photos, and the selected design image from R2
2. Create a new Gemini chat session via `google-genai` SDK
3. Send Turn 1: reference images + selected design + context prompt ("Here is a room redesign. I'll send edits as annotated images or text instructions.")
4. If annotations provided: draw numbered circles/badges on the design image using the annotation utility, then send as Turn 2 with edit instructions
5. If feedback provided (text-only): send as Turn 2 text message
6. Extract the revised image from Gemini's response
7. Upload revised image to R2
8. Serialize full chat history (including thought signatures) to R2
9. Return revised image URL + chat history R2 key

**Subsequent calls** (has `chat_history_key`):
1. Deserialize chat history from R2
2. Download the current design image (the latest revision)
3. If annotations: draw annotations on the image, send as next chat turn
4. If feedback: send text as next chat turn
5. Extract revised image, upload to R2
6. Serialize updated chat history to R2
7. Return revised image URL + updated chat history key

**Error handling**:
- Rate limits → retryable
- Content policy → non-retryable
- Chat history deserialization failure → non-retryable (data corruption)
- Thought signature validation error (400) → non-retryable (indicates history corruption)
- Image generation not triggered (text-only response) → retry once with explicit "generate an image" instruction

### Annotation Drawing Utility

The annotation utility lives in `backend/app/utils/image.py`.

**Input**: List of `AnnotationRegion` objects + base image (PIL Image or downloaded from R2)

**What it draws** (per region):
1. A colored circle outline around the target area (3-5px line width)
2. A small filled circle badge with a white number inside (pin/badge effect)
3. Colors cycle through: red (#FF0000), blue (#0000FF), green (#00FF00) for regions 1-3

**Annotation parameters**:
- **Circle outline**: `ImageDraw.ellipse()` with 4px line width
- **Number badge**: filled circle radius ~16px, white number 24-28px font with 2px black stroke
- **Coordinates**: `center_x` and `center_y` are normalized 0-1. Scale to image dimensions: `x_px = center_x * width`, `y_px = center_y * height`
- **Radius**: normalized 0-1. Scale to image dimensions similarly.

**Edge cases**:
- Radius too small (< 2% of image) → clamp to minimum visible size
- Regions overlap → draw all (they're just visual markers, not masks)
- Image already contains numbers → use letters (A, B, C) as fallback (P2 enhancement)

### Gemini Chat Session Manager

The session manager lives in `backend/app/utils/gemini_chat.py`.

**Responsibilities**:
1. **Create session**: Initialize a `client.chats.create()` with the selected Gemini model and `response_modalities=["TEXT", "IMAGE"]`
2. **Serialize to R2**: After each turn, call `chat.get_history()`, serialize all parts (text, inline_data, thought_signature) to JSON, upload to R2 at `projects/{project_id}/gemini_chat_history.json`
3. **Restore from R2**: Download the serialized history, reconstruct the `contents` array, create a new `generate_content` call with the full history + new message
4. **Cleanup**: Delete chat history from R2 when project is purged or "Start Over" is triggered

**Thought signature handling**:
- For `gemini-3-pro-image-preview`: thought signatures are mandatory. The serializer MUST preserve every `thoughtSignature` field from all model response parts.
- If using the SDK's chat interface, thought signatures are handled automatically within a session. The serializer captures them for cross-request persistence.
- For `gemini-2.5-flash-image`: thought signatures are not used. Serialization is simpler.

**History size considerations**:
- Each image in history is ~500KB-1MB base64-encoded
- After 5 edit rounds with 5 reference images: history could reach 30-50MB
- R2 handles this fine. Deserialization adds ~1-2s latency per edit (acceptable for MVP).
- Consider: after 3 edits, start a fresh session carrying only the latest image + refs (reduces context, may improve quality). Evaluate during P0 spike.

### Prompt Template Strategy

Create a `prompts/` directory with 3 templates:

```
backend/prompts/
  generation.txt          # Initial 2-option generation
  edit.txt                # Annotation-based editing (replaces inpaint.txt + regeneration.txt)
  room_preservation.txt   # Shared clause (camera angle, walls, architecture)
```

**Room preservation clause** (included in ALL generation/edit calls):
```
Preserve the exact camera angle, room geometry, walls, ceiling, windows,
doors, and floor plane from the reference photo. Do not modify the room
architecture or viewing perspective.
```

**Edit prompt template** (`edit.txt`):
```
This interior design image has numbered annotations marking areas to change.
Please apply these edits:
{edit_instructions}
Keep all unmarked elements exactly as they are. Preserve room architecture,
camera angle, and lighting direction. Return a clean photorealistic image
without any annotations or markup.
```

Where `{edit_instructions}` is built from the annotation regions:
```
1 (red circle, {description_of_area}) — {instruction}
2 (blue circle, {description_of_area}) — {instruction}
```

For **text-only regen** (no annotations), a simpler prompt:
```
Please modify this room design based on the following feedback:
{feedback}
Keep the room architecture, camera angle, and overall composition.
Return a clean photorealistic image reflecting these changes.
```

Templates use simple string `.format()`. No Jinja.

---

## 7. Contracts You Implement

T2 **consumes** these contracts — T0 owns them. Do not modify; request changes via T0.

### Activity Input/Output Models

```python
# === generate_designs (standalone, no chat) ===

class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None

class GenerateDesignsOutput(BaseModel):
    options: list[DesignOption]          # exactly 2

# === edit_design (multi-turn chat) ===

class AnnotationRegion(BaseModel):
    region_id: int                       # 1-3
    center_x: float                      # normalized 0-1
    center_y: float                      # normalized 0-1
    radius: float                        # normalized 0-1
    instruction: str                     # min 10 chars

class EditDesignInput(BaseModel):
    project_id: str                      # to locate chat history in R2
    base_image_url: str                  # current design image
    # For bootstrapping (first call only):
    room_photo_urls: list[str] = []
    inspiration_photo_urls: list[str] = []
    design_brief: DesignBrief | None = None
    # Edit content (at least one must be provided):
    annotations: list[AnnotationRegion] = []
    feedback: str | None = None
    # Chat continuity:
    chat_history_key: str | None = None  # None = first call, creates session

class EditDesignOutput(BaseModel):
    revised_image_url: str
    chat_history_key: str                # R2 key for serialized chat history
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
    lighting: str | None = None
    colors: list[str] = []
    textures: list[str] = []
    clutter_level: str | None = None
    mood: str | None = None

class InspirationNote(BaseModel):
    photo_index: int
    note: str
    agent_clarification: str | None = None

class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []
    openings: list[dict] = []

class DesignOption(BaseModel):
    image_url: str
    caption: str

class RevisionRecord(BaseModel):
    revision_number: int
    type: str                            # "annotation" or "regen"
    base_image_url: str
    revised_image_url: str
    instructions: list[str] = []         # edit text (for shopping pipeline)
```

**Contract change policy**: If you need a contract change, message T0 immediately. Additive changes (new optional fields) are fast-merged. Breaking changes require discussion with all consuming teams.

---

## 8. Cost Per Call

| Component | Cost |
|-----------|------|
| Initial generation (2 x Gemini 3 Pro Image) | $0.268 |
| Annotation edit (1 x Gemini 3 Pro Image) | $0.134 |
| Context accumulation per turn | ~$0.01 (negligible) |
| Typical 3 annotation edits | $0.402 |
| Implicit caching discount | 75-90% on repeated reference images |

**Per-session cost from T2 activities** (typical): ~$0.70 (initial + 3 edits, with caching)

---

## 9. Git & Collaboration

### Worktree Setup

```bash
# From the main remo repo:
git worktree add /Hanalei/remo-gen team/gen/gemini-spike
```

### Branch Naming

```
team/gen/gemini-spike        # P0: Quality spike (generation + annotation editing + chat persistence)
team/gen/annotation-utility  # P1: Annotation drawing utility
team/gen/chat-manager        # P1: Gemini chat session manager
team/gen/generate-designs    # P1: Initial generation activity
team/gen/edit-design         # P1: Edit design activity (multi-turn)
team/gen/prompt-templates    # P1: Prompt template library
team/gen/quality-tests       # P2: Quality test suite
```

### PR Merge Order

T2's PRs are in **group 6** of the master merge order — they can merge in any order during P1 (after T0's P0 gates are merged).

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
| Room architecture preserved | Visual comparison: camera angle, walls, windows match original |
| Annotation edits target marked areas | Circled objects change; non-circled areas preserved |
| Text-only regen works in same chat | Text feedback produces visible changes in next image |
| Chat history round-trip works | Serialize → deserialize → continue editing without 400 errors |
| All activities complete in time | < 3 minutes per activity |
| Prompt templates exist | `prompts/` directory with all templates |

---

## 11. Code Quality

### Testing Requirements

| Test Type | Description | Tools |
|-----------|-------------|-------|
| Annotation utility tests | Unit tests: circle drawing, coordinate scaling, badge rendering, edge cases | pytest + Pillow |
| Chat manager tests | Unit tests: serialization round-trip, thought signature preservation | pytest + mocks |
| Contract validation | Output validates against Pydantic output model | pytest + Pydantic |
| Real API calls | Each activity tested with real Gemini API and test room photos | pytest + real API keys |
| Quality test suite (P2) | 5+ test cases per activity with scored results; 70%+ meet quality bar | pytest + human eval |

### Error Handling

**Retryable errors** (Temporal retries automatically via `RetryPolicy`):
- Rate limits from Gemini API
- Transient network errors
- R2 upload/download failures
- Image generation not triggered (text-only response from Gemini)

**Non-retryable errors** (report to user via `WorkflowError`):
- Content policy violations
- Invalid input (e.g., no room photos)
- Chat history corruption / thought signature validation failure
- Persistent API failures after retry exhaustion

```python
from temporalio import activity
from temporalio.exceptions import ApplicationError

@activity.defn
async def edit_design(input: EditDesignInput) -> EditDesignOutput:
    try:
        # ... editing logic ...
        pass
    except RateLimitError:
        raise ApplicationError("Gemini rate limited", non_retryable=False)
    except ContentPolicyError:
        raise ApplicationError("Content policy violation", non_retryable=True)
    except ThoughtSignatureError:
        raise ApplicationError("Chat history corrupted", non_retryable=True)
```

### Activity Design Principle

Activities are **stateless** in the Temporal sense — they receive all inputs via the contract and produce outputs. The Gemini chat history is persisted to R2 (not held in memory across calls), making each activity invocation independently restartable.

---

## 12. Risks & Open Questions

### Risks Relevant to T2

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Gemini annotation editing doesn't target areas precisely enough | High | P0 spike validates this before any P1 work. Use numbered badges + text references (both visual and verbal). |
| Thought signature serialization breaks across requests | High | P0 spike includes round-trip test. If SDK chat handles it automatically, minimize manual history management. |
| Chat history size causes latency issues (30-50MB after 5 edits) | Medium | Start fresh session every 3 edits if quality degrades. Evaluate in spike. |
| Gemini preview model instability (rate limits, deprecation) | High | Both models use same API key/SDK; can swap model ID. Retry with backoff. |
| Gemini returns text-only response instead of image | Medium | Retry once with explicit "generate an image" in prompt. Known Gemini issue. |
| Safety filters block interior design edits | Low | Rephrase prompts to emphasize creative/design context. |

### Open Questions

| Question | Decision Needed By | Owner |
|----------|-------------------|-------|
| Which Gemini model wins the head-to-head spike? | P0 end | T2 |
| Does annotation editing work well enough for precise furniture replacement? | P0 end | T2 |
| Optimal annotation style (circles vs rectangles vs freehand outlines)? | P0 end | T2 |
| Should chat sessions reset every 3 edits to avoid degradation? | P1 mid | T2 |
| Is `gemini-2.5-flash-image` usable for edits (cheaper) while Pro is used for generation? | P0 end | T2 |

---

*For full context, see the master plan at `specs/PLAN_FINAL.md`.*
