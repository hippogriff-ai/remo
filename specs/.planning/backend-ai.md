# Backend & AI Pipeline — Feasibility Analysis

> **Scope**: Server framework, API design, AI image generation pipeline, intake chat agent, photo validation, shopping list pipeline, cost estimation, and risk areas.
> **Date**: 2026-02-10

---

## 1. Backend Framework Recommendation

### Recommendation: **Python / FastAPI**

| Criterion | FastAPI (Python) | Node/Express (TS) | Verdict |
|-----------|-----------------|-------------------|---------|
| AI SDK ecosystem | Native SDKs for OpenAI, Anthropic, Replicate, BFL, Exa — all first-class Python | SDKs exist but thinner; image processing is weaker | **FastAPI** |
| Image processing | Pillow, OpenCV, numpy — mature, fast | Sharp (libvips binding) — capable but less flexible | **FastAPI** |
| Async support | Native async/await, WebSocket streaming built-in | Native async via event loop | Tie |
| Hackathon speed | Pydantic models = instant validation + serialization; auto-generated OpenAPI docs | Zod + manual typing; less auto-tooling | **FastAPI** |
| LiDAR data parsing | Open3D, trimesh, scipy for point cloud / mesh processing | No serious equivalent | **FastAPI** |
| Deployment | Single Dockerfile; Railway / Fly.io / Render one-click | Same | Tie |

### Stack Details

```
Runtime:        Python 3.12+
Framework:      FastAPI 0.115+
Orchestration:  Temporal (workflow engine for durable execution)
                - Project lifecycle = Temporal workflow
                - Image gen, shopping list, validation = Temporal activities
                - 24h/48h purge timers = Temporal timers
                - Crash recovery / resume = automatic (Temporal's durable state)
Storage:        S3-compatible object store (Cloudflare R2 for cost, or AWS S3)
Database:       SQLite (via aiosqlite) for MVP — lightweight metadata only;
                workflow state lives in Temporal's persistence layer
                (swap to PostgreSQL post-MVP)
Cache:          In-memory dict for MVP; Redis post-MVP
WebSocket:      FastAPI native WebSocket for intake chat streaming
Worker:         Temporal Python SDK worker process (runs activities)
```

### Why Not Something Else?

- **Django**: Too heavy for a stateless API server; ORM overhead with no benefit for ephemeral data.
- **Node/Express**: Viable but worse AI/image processing ecosystem. The team would spend time wrapping Python libraries anyway.
- **Go/Rust**: Fast but slow to develop; no AI SDK ecosystem advantage at hackathon pace.

---

## 1b. Temporal Orchestration Layer

### Why Temporal?

The Remo project workflow (photo upload -> validation -> scan -> intake -> generation -> iteration -> approval -> shopping list) is a long-running, stateful process that must survive app crashes, server restarts, and user interruptions. Temporal provides **durable execution** — meaning workflow state is automatically persisted and recovered. This eliminates the need for a hand-rolled state machine, custom job queue, and manual crash-recovery logic.

### Workflow Design

The entire project lifecycle is a single Temporal workflow, identified by the project ID (which doubles as the Temporal workflow ID). The iOS app reconnects by querying this workflow ID.

```python
@workflow.defn
class DesignProjectWorkflow:
    """
    Single Temporal workflow per design project.
    Workflow ID = project_id (UUID).
    """

    def __init__(self):
        self.state = ProjectState()  # current step, photos, brief, images, etc.

    @workflow.run
    async def run(self, project_id: str) -> ProjectResult:
        # Phase 1: Wait for photo uploads (signals from API)
        await workflow.wait_condition(lambda: self.state.room_photos_valid)

        # Phase 2: Wait for scan or skip (signal)
        await workflow.wait_condition(lambda: self.state.scan_completed or self.state.scan_skipped)

        # Phase 3: Wait for intake completion or skip (signal)
        await workflow.wait_condition(lambda: self.state.intake_completed or self.state.intake_skipped)

        # Phase 4: Generate 2 design options (activity)
        options = await workflow.execute_activity(
            generate_design_options,
            args=[self.state.to_generation_input()],
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self.state.design_options = options

        # Phase 5: Wait for user to select an option (signal)
        await workflow.wait_condition(lambda: self.state.selected_option is not None)

        # Phase 6: Iteration loop (up to 5 rounds)
        while self.state.iteration_count < 5:
            action = await workflow.wait_condition(
                lambda: self.state.pending_action is not None  # lasso, regen, or approve
            )
            if self.state.pending_action == "approve":
                break
            elif self.state.pending_action == "lasso":
                result = await workflow.execute_activity(
                    run_lasso_inpainting,
                    args=[self.state.current_image, self.state.lasso_payload],
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                self.state.apply_iteration(result)
            elif self.state.pending_action == "regenerate":
                result = await workflow.execute_activity(
                    run_full_regenerate,
                    args=[self.state.to_regeneration_input()],
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )
                self.state.apply_iteration(result)
            self.state.pending_action = None

        # Phase 7: Generate shopping list (activity)
        shopping_list = await workflow.execute_activity(
            generate_shopping_list,
            args=[self.state.final_image, self.state.brief, self.state.scan_data],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        self.state.shopping_list = shopping_list

        # Phase 8: Grace period — keep data accessible for 24 hours
        # After 24h, the workflow completes and data is eligible for purge
        try:
            await asyncio.sleep(timedelta(hours=24).total_seconds())
        except asyncio.CancelledError:
            pass  # workflow cancelled externally (manual cleanup)

        # Purge project data from S3
        await workflow.execute_activity(
            purge_project_data,
            args=[project_id],
            start_to_close_timeout=timedelta(minutes=1),
        )
        return ProjectResult(status="purged")

    # --- Signals: API endpoints send signals to advance the workflow ---

    @workflow.signal
    async def photo_uploaded(self, photo: PhotoUploadResult):
        self.state.add_photo(photo)

    @workflow.signal
    async def scan_completed(self, scan_data: ScanData):
        self.state.scan_data = scan_data
        self.state.scan_completed = True

    @workflow.signal
    async def scan_skipped(self):
        self.state.scan_skipped = True

    @workflow.signal
    async def intake_completed(self, brief: DesignBrief):
        self.state.brief = brief
        self.state.intake_completed = True

    @workflow.signal
    async def intake_skipped(self):
        self.state.intake_skipped = True

    @workflow.signal
    async def option_selected(self, option_id: str):
        self.state.selected_option = option_id

    @workflow.signal
    async def submit_lasso(self, payload: LassoPayload):
        self.state.lasso_payload = payload
        self.state.pending_action = "lasso"

    @workflow.signal
    async def submit_regenerate(self, feedback: str):
        self.state.regen_feedback = feedback
        self.state.pending_action = "regenerate"

    @workflow.signal
    async def approve_design(self):
        self.state.pending_action = "approve"

    # --- Queries: API endpoints query workflow state for the iOS app ---

    @workflow.query
    def get_state(self) -> ProjectState:
        return self.state
```

### Activities (Long-Running Tasks)

Each AI-calling operation is a Temporal **activity** — an isolated unit of work with its own timeout and retry policy. Activities run on a Temporal worker process.

| Activity | What It Does | Timeout | Retries | Idempotent? |
|----------|-------------|---------|---------|-------------|
| `validate_photo_content` | Call gpt-4o-mini to classify image content | 30s | 3 | Yes |
| `generate_design_options` | Call gpt-image-1 to produce 2 design images | 3min | 3 | Yes (same inputs = deterministic prompt) |
| `run_lasso_inpainting` | Render mask + call Flux Fill Pro | 2min | 3 | Yes |
| `run_full_regenerate` | Call gpt-image-1 with full context + feedback | 3min | 3 | Yes |
| `generate_shopping_list` | Extract items (Claude) + Exa search + parse | 5min | 2 | Yes |
| `purge_project_data` | Delete images from S3, cleanup | 1min | 3 | Yes |

### Signals & Queries (API <-> Workflow Communication)

The FastAPI server communicates with the running workflow via:

- **Signals**: Fire-and-forget messages that advance the workflow. Example: when the user uploads a photo, the API handler calls `workflow.signal("photo_uploaded", photo_result)`. The workflow processes it when ready.
- **Queries**: Synchronous reads of workflow state. Example: when the iOS app calls `GET /projects/:id`, the API handler calls `workflow.query("get_state")` and returns the current step, images, iteration count, etc. Queries never mutate state.

```python
# FastAPI endpoint example
@app.post("/api/v1/projects/{project_id}/iterate/lasso")
async def submit_lasso(project_id: str, body: LassoRequest):
    handle = temporal_client.get_workflow_handle(project_id)
    await handle.signal(DesignProjectWorkflow.submit_lasso, body.to_payload())
    return {"status": "accepted", "iteration": (await handle.query(DesignProjectWorkflow.get_state)).iteration_count + 1}
```

### Timers & Data Lifecycle

Temporal's built-in timer mechanism replaces custom cron jobs or scheduled tasks:

| Timer | Duration | Trigger | Action |
|-------|----------|---------|--------|
| **Approval grace period** | 24 hours | User approves design | Workflow sleeps 24h then executes `purge_project_data` activity |
| **Abandonment timeout** | 48 hours | No signal received for 48h | Workflow times out; Temporal's workflow execution timeout set to 48h of inactivity; triggers purge |

The 48-hour abandonment is implemented via Temporal's `workflow.wait_condition()` with a timeout. If no signal arrives within 48 hours at any waiting point, the workflow raises a timeout and jumps to the purge phase.

### Crash Recovery

This is where Temporal's value proposition is strongest:

1. **Server crashes during image generation**: The activity has a `start_to_close_timeout`. If the worker dies, Temporal retries the activity on a new worker. The workflow itself never loses state.
2. **User closes app mid-intake**: The workflow is parked at `wait_condition(lambda: self.state.intake_completed)`. When the user reopens the app, the iOS client sends `GET /projects/:id` which queries the workflow. The workflow is still alive. The user resumes.
3. **API server restarts**: No state is lost — all state lives in Temporal's persistence. FastAPI is stateless; it only proxies signals/queries to Temporal.

### Infrastructure Requirements

```
Temporal Server:    Temporal Cloud (managed, recommended for MVP)
                    OR self-hosted via docker-compose (1 container)
Persistence:        Temporal Cloud handles this; self-hosted uses PostgreSQL or SQLite
Worker:             1 Python worker process running alongside FastAPI
                    (can scale to N workers later)
SDK:                temporalio (Python SDK, pip install temporalio)
```

**Temporal Cloud pricing**: Free tier includes 1,000 workflow executions/month, sufficient for MVP. Pay-as-you-go after that (~$0.02 per 1K actions).

**Self-hosted alternative**: `docker-compose up` with Temporal server + PostgreSQL. Zero cost, but you own the ops. Acceptable for hackathon.

---

## 2. API Design

### Base URL: `POST /api/v1/...`

All endpoints are JSON unless noted. Image uploads use `multipart/form-data`. Async operations (generation, iteration, shopping list) are backed by Temporal activities — the client polls project state via `GET /projects/:id` rather than separate job endpoints.

### 2.1 Project Lifecycle

```
POST   /projects                          → Create new project (starts Temporal workflow)
GET    /projects/:id                      → Get project state (Temporal query — for resume + polling)
DELETE /projects/:id                      → Abandon project (cancel Temporal workflow)
```

**Create Project**: Starts a new Temporal workflow with `workflow_id = project_id`.

**Create Project Response**:
```json
{
  "project_id": "uuid",
  "status": "created",
  "step": "photo_upload",
  "created_at": "2026-02-10T...",
  "expires_at": "2026-02-12T..."
}
```

**Get Project State** (used for resume and polling during async operations):
```json
{
  "project_id": "uuid",
  "step": "generating" | "iterating" | "approved" | ...,
  "photos": { "room": [...], "inspiration": [...] },
  "scan": { ... } | null,
  "brief": { ... } | null,
  "design_options": [{ "option_id", "image_url", "caption" }] | null,
  "selected_option": "uuid" | null,
  "current_image_url": "..." | null,
  "iteration_count": 2,
  "max_iterations": 5,
  "shopping_list": { ... } | null,
  "error": null | { "message": "...", "retryable": true }
}
```

This single endpoint replaces all per-job polling endpoints. The iOS client polls this during generation/iteration (~every 2-3s) and stops when `step` advances.

### 2.2 Photo Upload & Validation

```
POST /projects/:id/photos/room
  Content-Type: multipart/form-data
  Body: { file: <image>, angle_index: 1|2 }
  → 200: { photo_id, validation: { passed: true } }
  → 422: { photo_id, validation: { passed: false, reason: "blurry", message: "..." } }

POST /projects/:id/photos/inspiration
  Content-Type: multipart/form-data
  Body: { file: <image>, note?: string(max=200) }
  → 200: { photo_id, validation: { passed: true } }
  → 422: { photo_id, validation: { passed: false, reason: "...", message: "..." } }

DELETE /projects/:id/photos/:photo_id
  → 204
```

Validation runs synchronously (< 2s) because all checks are lightweight (blur, resolution, classification). Response includes pass/fail immediately.

### 2.3 LiDAR Scan Data Upload

```
POST /projects/:id/scan
  Content-Type: multipart/form-data
  Body: { file: <USDZ or PLY or custom format> }
  → 200: { scan_id, dimensions: { width_m, length_m, height_m }, wall_count, openings: [...] }
  → 422: { error: "invalid_scan_data", message: "..." }
```

The iOS app captures RoomPlan data (ARKit `CapturedRoom`) and serializes it. The server parses the geometry to extract wall lengths, floor area, ceiling height, and opening positions. These dimensions feed into generation prompts and shopping list size filtering.

### 2.4 Intake Chat (Streaming)

```
WebSocket /projects/:id/intake/ws
  → Client sends: { "type": "start", "mode": "quick"|"full"|"open" }
  → Server streams: { "type": "message", "content": "Who uses...", "options": [...], "progress": "1/3" }
  → Client sends: { "type": "answer", "value": "1" | "free text" }
  → Server streams: { "type": "message", "content": "Got it..." }
  → ... (repeat)
  → Server sends: { "type": "summary", "brief": { <DesignBrief> } }
  → Client sends: { "type": "confirm" } | { "type": "edit", "field": "...", "value": "..." }
  → Server sends: { "type": "complete", "brief": { <DesignBrief> } }
```

**Why WebSocket over SSE?** Bidirectional — the intake is a conversation, not a one-way stream. The client sends answers, the server sends follow-ups. SSE would require a parallel POST channel for user answers, adding complexity.

**Alternative (simpler, if WebSocket is too complex for MVP)**:
```
POST /projects/:id/intake/start    → { session_id, first_question }
POST /projects/:id/intake/answer   → { next_question | summary }
POST /projects/:id/intake/confirm  → { brief: <DesignBrief> }
```
This REST approach is simpler but loses streaming feel. Acceptable for MVP if WebSocket adds too much iOS client complexity.

### 2.5 Design Generation (Async via Temporal)

Generation is triggered automatically by the Temporal workflow after intake completion (or skip). No explicit API call needed — the workflow advances on its own. The iOS client detects this by polling `GET /projects/:id` and observing `step: "generating"` -> `step: "choosing"` with `design_options` populated.

If the user taps "Start Over" (returning to intake after seeing options), the client sends:
```
POST /projects/:id/restart-intake
  → 200: { status: "accepted" }
```
This signals the workflow to reset the intake/generation phase (does not consume an iteration).

**Polling pattern**: The iOS client polls `GET /projects/:id` every 2-3 seconds during generation. When `design_options` is non-null, the generation is complete. This is simpler than WebSocket and works through proxies/CDNs.

### 2.6 Select Design Option

```
POST /projects/:id/select
  Body: { option_id: "uuid" }
  → 200: { status: "selected", iteration: 0, max_iterations: 5 }
```

### 2.7 Lasso Iteration (Submit Regions + Generate Revision)

```
POST /projects/:id/iterate/lasso
  Body: {
    regions: [
      {
        region_id: 1,
        mask_polygon: [[x,y], ...],    // normalized 0-1 coordinates
        action: "Replace"|"Remove"|"Change finish"|"Resize"|"Reposition",
        instruction: "Replace rug with solid neutral wool rug",
        avoid: ["brass", "patterns"],
        style_nudges: ["more minimal", "pet-friendly"]
      },
      ...
    ]
  }
  → 202: { status: "accepted", iteration: 3 }
```

This sends a **Temporal signal** (`submit_lasso`) to the workflow. The workflow's `run_lasso_inpainting` activity executes:
1. Takes the current design image as base.
2. Renders mask polygons into a binary mask image (Pillow/OpenCV).
3. Renders an overlay image with numbered chips for the prompt.
4. Composes the structured editing prompt (see spec 4.7.6).
5. Calls the Flux Fill API with base image + mask + prompt.
6. Stores the new image and updates workflow state.

The client polls `GET /projects/:id` until `current_image_url` changes and `iteration_count` increments.

### 2.8 Full Regenerate

```
POST /projects/:id/iterate/regenerate
  Body: { feedback: "Make the whole room feel warmer..." }
  → 202: { status: "accepted", iteration: 4 }
```

Sends a **Temporal signal** (`submit_regenerate`). Same polling pattern — client watches project state until `current_image_url` updates.

### 2.9 Approve Design

```
POST /projects/:id/approve
  → 200: { status: "approved" }
```

Sends a **Temporal signal** (`approve_design`). The workflow advances to the shopping list generation activity. The client polls `GET /projects/:id` until `shopping_list` is populated.

### 2.10 Shopping List

The shopping list is available via `GET /projects/:id` once the workflow completes the `generate_shopping_list` activity. The response shape within the project state:

```json
"shopping_list": {
      status: "complete",
      total_estimated_cost: 4250.00,
      groups: [
        {
          category: "Seating",
          items: [
            {
              item_name: "Linen Sofa",
              design_description: "Oat-colored linen sofa, modern low-profile",
              match: {
                product_name: "Harmony Linen Sofa — Oat",
                retailer: "West Elm",
                price: 1299.00,
                currency: "USD",
                dimensions: "84W x 36D x 33H inches",
                url: "https://...",
                image_url: "https://...",
                confidence: 0.85,
                confidence_label: null,
                fit_badge: "fits",         // null if no LiDAR
                fit_detail: "Your wall is 8ft — this sofa is 7ft wide"
              }
            }
          ]
        }
      ],
      unmatched: [
        {
          item_name: "Abstract wall art",
          search_keywords: "abstract canvas wall art earth tones 24x36",
          search_url: "https://shopping.google.com/..."
        }
      ]
    }
```

---

## 3. AI Image Generation Pipeline

### 3.1 Model Comparison

| Model | Photorealism | Inpainting (Masked) | Image-to-Image | Speed | Cost/Image | API Available | Interior Design Quality |
|-------|-------------|---------------------|-----------------|-------|-----------|--------------|------------------------|
| **gpt-image-1** (OpenAI) | Excellent | Yes, but "soft mask" — may modify outside mask | Yes (edit endpoint) | ~15-30s | $0.042-0.167 | Yes | Very good — understands spatial context |
| **gpt-image-1.5** (OpenAI) | Excellent | Improved over 1.0 | Yes | ~15-30s | TBD (likely similar) | Rolling out | Best-in-class for multi-step editing |
| **DALL-E 3** (OpenAI) | Good | No native inpainting | Text-to-image only | ~10-15s | $0.04-0.08 | Yes | Good but no editing workflow |
| **Flux.1 Fill [pro]** (BFL) | Excellent | Yes — dedicated inpainting model, respects masks precisely | Yes | ~10-20s | $0.055 (Replicate) | Yes (Replicate, BFL API, fal.ai) | Excellent mask adherence |
| **Flux.1 [pro]** (BFL) | Excellent | Via ControlNet (less native) | Yes (img2img) | ~10-15s | $0.055 (Replicate) | Yes | Good for full generation |
| **SDXL + ControlNet** | Good | Yes (mature ecosystem) | Yes | ~5-10s | $0.01-0.02 (self-hosted) | Self-host or Replicate | Requires heavy prompt engineering |
| **Midjourney** | Excellent | Limited | Via "describe + vary" | ~30-60s | $0.05+ | Unofficial only | Great aesthetics, poor programmability |

### 3.2 Inpainting Capability Analysis

The lasso iteration feature is the most technically demanding part of the pipeline. It requires **precise masked inpainting** — modifying only the lasso'd region while preserving everything else.

**gpt-image-1 Inpainting Behavior**:
- Supports mask-based editing via `/v1/images/edits` endpoint.
- **Critical limitation**: gpt-image-1 uses a "soft mask" approach — it may modify pixels outside the masked area. Community reports confirm it sometimes replaces the entire image rather than just the masked region. This is a significant risk for the lasso feature, which demands pixel-precise region editing.
- The prompt-based nature means results depend heavily on prompt quality.

**Flux.1 Fill Inpainting Behavior**:
- Purpose-built for inpainting/outpainting.
- Uses a dedicated model architecture (not a hack on a base model).
- Respects masks precisely — fills only the transparent/masked area.
- Maintains context and style consistency with surrounding pixels.
- Available via Replicate API and BFL's native API.

**Verdict for Lasso**: Flux.1 Fill is significantly better for the lasso use case because it respects mask boundaries. gpt-image-1's soft mask behavior is a dealbreaker for "do not change anything outside the numbered regions."

### 3.3 Recommended Architecture: Hybrid Pipeline

**Initial Generation (2 options)**: Use **gpt-image-1** (or gpt-image-1.5 if available).
- Reason: Superior at understanding complex design briefs, spatial reasoning, and style interpretation from text. The initial generation is text+image-to-image (no mask needed).
- Input: Room photos + inspiration photos + Design Brief as structured prompt.
- Output: 2 photorealistic redesign images.

**Lasso Iteration (masked inpainting)**: Use **Flux.1 Fill [pro]** via Replicate or BFL API.
- Reason: Precise mask adherence is critical. Flux Fill is purpose-built for this.
- Input: Current design image + binary mask (from lasso polygons) + structured edit prompt.
- Output: Revised image with only masked regions changed.

**Full Regenerate**: Use **gpt-image-1** again.
- Reason: Full regeneration benefits from GPT's superior prompt understanding. No mask needed.
- Input: Original room photos + brief + all prior feedback context.
- Output: New full design image.

### 3.4 Prompt Composition Strategy

**Initial Generation Prompt Template**:
```
You are an expert interior designer. Redesign this room based on the following brief.

ROOM CONTEXT:
- Room type: {room_type}
- Current photos attached (2 angles)
- Room dimensions: {width}m x {length}m x {height}m ceiling (if LiDAR)

DESIGN BRIEF:
- Mood: {mood}
- Lighting: {lighting}
- Colors: {colors}
- Textures: {textures}
- Clutter level: {clutter_level}
- Keep items: {keep_items}
- Constraints: {constraints}

INSPIRATION:
{for each inspiration photo: "Inspiration {n}: {user_note}. {agent_clarification}"}

RULES:
- Output must be photorealistic — it should look like a real photograph
- Preserve the room's architecture, walls, windows, doors, and ceiling
- Match the camera angle of the primary room photo
- No text in the image
- Design should look achievable with real, purchasable furniture and decor
{if keep_items: "- Keep the following items exactly as they appear: {keep_items}"}
```

**Lasso Edit Prompt Template** (per spec 4.7.6):
```
You are editing Image A. Image B shows numbered regions.
1) Region #1 ({action}): {instruction}. Avoid: {avoid_tokens}. Constraints: {constraint_tokens}.
2) Region #2 ({action}): {instruction}. ...
Do not change anything outside the numbered regions.
Preserve camera angle, room architecture, lighting direction, and all unchanged items.
No text in the final image.
Output must be photorealistic.
```

### 3.5 Image-to-Image vs Text-to-Image

| Approach | Use Case | Pros | Cons |
|----------|----------|------|------|
| **Image-to-image** (edit/inpaint) | Lasso iteration, preserve room structure | Maintains spatial layout, architecture | Quality depends on base image |
| **Text-to-image with reference** | Initial generation | Maximum creative freedom | May not match room geometry perfectly |
| **Image-to-image with reference images** | Initial generation (recommended) | Preserves room shape while applying new design | Best balance for our use case |

**Recommendation**: Use image-to-image with the room photo as structural reference for initial generation. Use masked inpainting for lasso iterations.

### 3.6 Quality vs Speed vs Cost Trade-offs

| Scenario | Model | Time | Cost | Quality |
|----------|-------|------|------|---------|
| Initial gen (2 options) | gpt-image-1 medium | ~30s x 2 | $0.084 | High |
| Initial gen (2 options) | gpt-image-1 high | ~45s x 2 | $0.334 | Very high |
| Lasso iteration | Flux Fill Pro | ~15s | $0.055 | High (precise masks) |
| Full regenerate | gpt-image-1 medium | ~30s | $0.042 | High |

**MVP Recommendation**: Use `gpt-image-1` at **medium quality** for initial generation and full regenerate (good enough, 4x cheaper than high). Use **Flux Fill Pro** for lasso iterations.

---

## 4. Intake Chat Agent

### 4.1 Implementation with Claude API

Use **Claude Sonnet 4.5** (`claude-sonnet-4-5-20250929`) for the intake agent. It's the best balance of quality, speed, and cost for conversational AI.

**Architecture**:

```python
class IntakeAgent:
    def __init__(self, mode: str, project_context: dict):
        self.mode = mode  # "quick" | "full" | "open"
        self.system_prompt = self._build_system_prompt(mode, project_context)
        self.messages: list[dict] = []
        self.domain_checklist: dict[str, bool] = {
            "room_usage": False,
            "pain_points": False,
            "keep_items": False,
            "lighting": False,
            "colors": False,
            "textures": False,
            "clutter_level": False,
            "mood": False,
            "constraints": False,
            "inspiration_refs": False,
        }

    async def process_answer(self, user_input: str) -> AgentResponse:
        self.messages.append({"role": "user", "content": user_input})
        response = await anthropic.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            system=self.system_prompt,
            messages=self.messages,
            # Use structured output for domain tracking
        )
        self.messages.append({"role": "assistant", "content": response.content})
        return self._parse_response(response)
```

### 4.2 System Prompt Strategy

The system prompt instructs Claude to:
1. Act as a friendly interior design consultant.
2. Follow the adaptive question plan (see spec 4.5).
3. Track which domains are covered via an internal checklist.
4. Output each response as structured JSON containing:
   - `message`: The user-facing text.
   - `options`: Array of numbered quick-reply options (or null for open-ended questions).
   - `progress`: "2/3" or "Conversation" for open mode.
   - `domains_covered`: Updated checklist.
   - `is_summary`: Boolean — true when the agent is presenting the final summary.
   - `brief`: Partial DesignBrief object (updated incrementally).

### 4.3 Structured Output for Design Brief

Use Claude's tool-use / structured output to ensure the Design Brief is always valid JSON:

```python
tools = [{
    "name": "update_design_brief",
    "description": "Update the design brief with information gathered from the user",
    "input_schema": {
        "type": "object",
        "properties": {
            "room_type": {"type": "string"},
            "occupants": {"type": "string"},
            "pain_points": {"type": "array", "items": {"type": "string"}},
            "keep_items": {"type": "array", "items": {"type": "string"}},
            "style_profile": {
                "type": "object",
                "properties": {
                    "lighting": {"type": "string"},
                    "colors": {"type": "array", "items": {"type": "string"}},
                    "textures": {"type": "array", "items": {"type": "string"}},
                    "clutter_level": {"type": "string"},
                    "mood": {"type": "string"}
                }
            },
            "constraints": {"type": "array", "items": {"type": "string"}},
            "inspiration_notes": {"type": "array", "items": {"type": "object"}}
        }
    }
}]
```

The agent calls `update_design_brief` after each user response, incrementally building the brief. When the conversation ends, the final tool call produces the complete brief.

### 4.4 Conversation State Management

- **State stored in Temporal workflow**: The full `messages` array + domain checklist + partial brief are part of the workflow state. The workflow is parked at `wait_condition(lambda: self.state.intake_completed)` during the intake phase, and each user answer arrives as a Temporal signal.
- **Resume**: On reconnect, the iOS client queries the workflow via `GET /projects/:id`. The workflow is still alive and waiting for the next signal. The client can retrieve the conversation history from the workflow state and resume the UI.
- **No client-side state**: The iOS client only stores the project ID. All conversation history lives in Temporal's durable state.
- **TTL**: Conversation state is purged with the project when the workflow completes (24h after approval, or 48h abandonment timeout).
- **Note on intake signals**: Each intake answer is sent as a Temporal signal. The workflow's intake phase processes the signal by calling Claude (as a short-lived activity) and storing the response. This means intake conversations survive server restarts seamlessly.

### 4.5 Cost Per Intake Session

| Mode | Estimated Turns | Input Tokens | Output Tokens | Cost (Sonnet 4.5) |
|------|-----------------|-------------|---------------|-------------------|
| Quick (3 questions) | ~8 messages | ~3,000 | ~1,500 | ~$0.03 |
| Full (10 questions) | ~22 messages | ~8,000 | ~4,000 | ~$0.08 |
| Open Conversation | ~15-30 messages | ~10,000 | ~5,000 | ~$0.11 |

---

## 5. Photo Validation Pipeline

### 5.1 Validation Checks

| Check | Method | Library | Threshold | Latency |
|-------|--------|---------|-----------|---------|
| **Blur detection** | Laplacian variance | OpenCV | Variance < 100 = blurry | < 50ms |
| **Resolution** | Read image dimensions | Pillow | Shortest side < 1024px = fail | < 10ms |
| **Content: room vs not-room** | Image classification | OpenAI Vision (gpt-4o-mini) or CLIP | Confidence < 0.7 = not a room | ~1-2s |
| **Content: people/animals** | Object detection | OpenAI Vision (gpt-4o-mini) or CLIP | Any person/animal detected with confidence > 0.5 = fail | ~1-2s |

### 5.2 Implementation Strategy

**Blur + Resolution**: Run on-device (iOS) for instant feedback. These are trivial computations:
```python
# Server-side fallback (also validates)
import cv2
import numpy as np

def check_blur(image_bytes: bytes, threshold: float = 100.0) -> bool:
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    variance = cv2.Laplacian(img, cv2.CV_64F).var()
    return variance >= threshold

def check_resolution(image_bytes: bytes, min_side: int = 1024) -> bool:
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(image_bytes))
    return min(img.size) >= min_side
```

**Content Classification**: Run server-side using a lightweight vision model. Two approaches:

**Option A: OpenAI gpt-4o-mini with vision** (recommended for MVP)
```python
response = await openai.chat.completions.create(
    model="gpt-4o-mini",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "Classify this image. Return JSON: {is_room: bool, has_people: bool, has_animals: bool, room_type: string|null}"},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"}}
        ]
    }],
    response_format={"type": "json_object"}
)
```
- Cost: ~$0.001 per image (cheap).
- Latency: ~1-2 seconds.
- Accuracy: Very high for room vs. not-room and people/animal detection.

**Option B: CLIP (self-hosted)**
- Free but requires GPU hosting.
- Faster (~200ms) but less accurate for nuanced classification.
- Not worth the infrastructure cost for MVP.

### 5.3 On-Device vs Server-Side

| Check | On-Device (iOS) | Server-Side | Recommendation |
|-------|----------------|-------------|----------------|
| Blur (Laplacian) | Yes — Core Image / Accelerate | Yes — OpenCV | **Both** — on-device for instant UX, server for validation |
| Resolution | Yes — trivial | Yes — trivial | **Both** |
| Content (room/people) | Possible via Core ML + CLIP | Yes — gpt-4o-mini | **Server-side only** for MVP (simpler) |

**MVP approach**: Blur + resolution on-device for instant feedback. Content classification on server after upload. Total validation latency: < 3 seconds.

---

## 6. Shopping List Pipeline

### 6.1 Pipeline Steps

```
Approved Design Image
        ↓
[1] Item Extraction (Claude Vision)
        ↓
[2] Query Construction (per item)
        ↓
[3] Exa Search (per item, parallelized)
        ↓
[4] Result Parsing + Scoring (Claude)
        ↓
[5] Dimension Filtering (if LiDAR)
        ↓
[6] Confidence Scoring + Fallback
        ↓
Shopping List Output
```

### 6.2 Step 1: Item Extraction

Use Claude Sonnet 4.5 with vision to analyze the approved design image:

```python
response = await anthropic.messages.create(
    model="claude-sonnet-4-5-20250929",
    max_tokens=2048,
    messages=[{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64_image}},
            {"type": "text", "text": """Analyze this interior design image. Identify every distinct furnishing, fixture, and decor element visible.

For each item, provide:
- category (e.g., "sofa", "coffee table", "pendant light", "area rug")
- style_attributes (e.g., "mid-century modern", "Scandinavian", "industrial")
- material (e.g., "linen", "walnut wood", "brass", "marble")
- color (e.g., "oat", "charcoal", "warm white")
- approximate_proportions (e.g., "large 3-seat", "small accent", "6x4 feet")
- room_area (e.g., "Seating", "Lighting", "Rugs & Flooring", "Decor & Accessories")

Return as JSON array."""}
        ]
    }]
)
```

### 6.3 Step 2: Query Construction

For each extracted item, construct an Exa search query:

```python
def build_exa_query(item: dict, dimensions: dict | None) -> str:
    query_parts = [
        item["category"],
        item["style_attributes"],
        item["material"],
        item["color"],
    ]
    if dimensions and item.get("approximate_proportions"):
        query_parts.append(item["approximate_proportions"])
    query_parts.append("buy online")
    return " ".join(query_parts)

# Example: "linen sofa mid-century modern oat 84 inch buy online"
```

### 6.4 Step 3: Exa Search Integration

```python
from exa_py import Exa

exa = Exa(api_key=EXA_API_KEY)

async def search_product(query: str) -> list[dict]:
    results = exa.search_and_contents(
        query=query,
        type="auto",
        num_results=5,
        use_autoprompt=True,
        text=True,         # Get page text for parsing
        highlights=True,   # Get relevant snippets
        category="product",  # If Exa supports category filtering
    )
    return results.results
```

**Parallelization**: All item searches run concurrently via `asyncio.gather()`. For 8 items, this means ~1 round of Exa calls rather than 8 sequential calls.

### 6.5 Step 4: Result Parsing + Scoring

Use Claude to extract structured product data from Exa results and score match quality:

```python
response = await anthropic.messages.create(
    model="claude-sonnet-4-5-20250929",
    messages=[{
        "role": "user",
        "content": f"""I'm looking for: {item_description}

Here are search results from retailers:
{exa_results_text}

For each result, extract:
- product_name
- retailer
- price (USD)
- dimensions (if listed)
- product_url
- image_url (if found)
- match_confidence (0.0-1.0): how well does this match the target item in style, material, color, and size?
- why_match: one sentence explaining the match

Return the single best match as JSON. If no result is a good match (confidence < 0.5), return null."""
    }]
)
```

### 6.6 Step 5: Dimension Filtering (LiDAR)

If the user completed a LiDAR scan:
1. The item extraction step includes estimated real-world dimensions (from the scan-calibrated image).
2. Product dimensions from Exa results are compared against available space.
3. Fit badge logic:
   - Product fits with >6 inches clearance: `"fits"` ("Fits your space")
   - Product fits with <6 inches clearance: `"tight"` ("May be tight")
   - Product doesn't fit: filter out, search for smaller alternatives

### 6.7 Step 6: Confidence + Fallback

- **High confidence (>= 0.8)**: Show product normally.
- **Medium confidence (0.5-0.79)**: Show with "Close match" label.
- **Low confidence (< 0.5)**: Don't show product. Show fallback:
  ```json
  {
    "item_name": "Abstract wall art",
    "search_keywords": "abstract canvas wall art earth tones 24x36",
    "search_url": "https://www.google.com/search?tbm=shop&q=abstract+canvas+wall+art+earth+tones+24x36"
  }
  ```

### 6.8 Exa Pricing for Shopping List

| Per Project | Queries | Cost |
|-------------|---------|------|
| Items to search | 6-10 items | — |
| Exa searches | 6-10 queries | $0.03-$0.05 (at $5/1K searches) |
| Claude parsing | 6-10 LLM calls | ~$0.05-$0.08 |
| **Total shopping list** | — | **~$0.08-$0.13** |

---

## 7. Cost Estimation Per Session

### 7.1 Per-Session Breakdown (Typical Happy Path)

| Component | Detail | Cost |
|-----------|--------|------|
| **Photo validation** | 2 room + 2 inspiration = 4 images x gpt-4o-mini | $0.004 |
| **Intake chat** | Full intake (~22 messages, Sonnet 4.5) | $0.08 |
| **Initial generation** | 2 design options x gpt-image-1 medium | $0.084 |
| **Lasso iterations** | 3 rounds x Flux Fill Pro | $0.165 |
| **Full regenerate** | 1 round x gpt-image-1 medium | $0.042 |
| **Shopping list extraction** | Claude Sonnet 4.5 vision | $0.02 |
| **Exa product search** | ~8 queries | $0.04 |
| **Shopping list parsing** | ~8 Claude calls for result scoring | $0.06 |
| **Total per session** | | **~$0.50** |

### 7.2 Range Estimates

| Scenario | Cost |
|----------|------|
| **Minimal** (quick intake, pick first option, approve immediately, 6 items) | ~$0.20 |
| **Typical** (full intake, 2-3 iterations, 8 items) | ~$0.50 |
| **Maximum** (open conversation, 5 iterations, 10+ items, high-quality gen) | ~$1.20 |

### 7.3 Scaling Context

At $0.50/session average:
- 100 sessions/day = $50/day = $1,500/month
- 1,000 sessions/day = $500/day = $15,000/month

This is very manageable for an MVP. The main cost driver is image generation, which scales linearly.

---

## 8. Risk Areas

### 8.1 Critical Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| **gpt-image-1 soft mask modifies outside lasso region** | High | High | Use Flux Fill for inpainting; only use GPT for initial gen and full regen |
| **Generated designs don't look photorealistic enough** | High | Medium | Use high-quality tier; invest in prompt engineering; consider Flux Pro for generation too |
| **Inpainting changes room architecture/angle** | High | Medium | Strong prompt constraints; use ControlNet/depth conditioning with Flux if needed |
| **Style consistency across iterations degrades** | High | Medium | Pass full revision history as context; use the same model consistently per session |

### 8.2 Quality Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| **"Keep items" not preserved in generation** | Medium | High | Mask keep-items regions as "do not modify" zones; consider ControlNet depth/canny |
| **Inspiration photos not reflected in output** | Medium | Medium | Reference inspiration images directly in the prompt; use image-to-image blending |
| **Shopping list products don't visually match design** | Medium | Medium | Use vision model to compare product images against design regions |
| **Exa returns stale/broken product links** | Low | Medium | Validate URLs before presenting; cache results; fallback to Google Shopping |

### 8.3 Latency Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| **Initial generation takes > 60s** | Medium | Medium | Run 2 options in parallel; show engaging loading state; use medium quality |
| **Shopping list generation takes > 30s** | Medium | Medium | Parallelize Exa searches; stream results as they arrive |
| **Intake chat feels laggy** | Low | Low | Claude Sonnet 4.5 is fast (~1-2s response); use streaming |
| **LiDAR data processing slow on server** | Low | Low | Parse on device (RoomPlan provides structured data); send only extracted dimensions |

### 8.4 Technical Unknowns

| Unknown | Impact | Investigation Needed |
|---------|--------|---------------------|
| **Can Flux Fill maintain photorealism quality matching gpt-image-1 initial gen?** | Style consistency between models | Prototype: generate with GPT, inpaint with Flux; evaluate visual coherence |
| **How well does gpt-image-1 handle room photos as image-to-image input?** | Core feature viability | Build a test: upload a room photo, prompt for redesign, evaluate if room shape is preserved |
| **RoomPlan (ARKit) data format — what exactly does the iOS app send to the server?** | API design for scan endpoint | Test RoomPlan on a physical device; document the `CapturedRoom` serialization format |
| **Exa search quality for niche furniture items** | Shopping list completeness | Run 20 test queries for common furniture items; measure hit rate |
| **Token usage for long intake conversations** | Cost at scale | Monitor actual token usage in beta; adjust model or max turns if needed |
| **Temporal workflow state size limits** | Workflow reliability | Temporal has a ~50MB event history limit per workflow; verify that storing intake messages + image URLs (not image bytes) stays well under this |
| **Temporal Cloud free tier sufficiency for MVP launch** | Cost | Free tier = 1,000 workflow executions/month; verify this covers expected beta usage |

### 8.5 Model Availability Risks

| Risk | Mitigation |
|------|------------|
| OpenAI API outage during generation | Temporal retries the activity automatically (up to 3 attempts); fallback to Flux Pro for generation |
| Replicate/BFL API outage during inpainting | Temporal retries; fallback to gpt-image-1 inpainting (accept softer masks) |
| Anthropic API outage during intake | Temporal retries the `run_intake_turn` activity; conversation state is preserved in workflow |
| Exa API outage during shopping list | Temporal retries; fall back to Google Shopping links for all items |

### 8.6 Temporal-Specific Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|-----------|------------|
| **Temporal Cloud outage** | High | Very Low | Temporal Cloud has 99.99% SLA; if self-hosting, run with PostgreSQL persistence for durability |
| **Workflow state grows too large** | Medium | Low | Keep images in S3, store only URLs in workflow state; intake messages are the largest payload (~50KB for a full conversation) |
| **Temporal learning curve slows development** | Medium | Medium | Temporal Python SDK is well-documented; the workflow pattern here is straightforward (linear with wait points); no complex saga patterns needed |
| **Worker scaling** | Low | Low | Single worker is sufficient for MVP; Temporal natively supports multiple workers claiming tasks from the same task queue |
| **Temporal adds deployment complexity** | Low | Medium | Use Temporal Cloud (managed) for MVP to avoid ops burden; if self-hosting, it's one `docker-compose` service |

---

## 9. Recommended MVP Architecture Diagram

```
                     iOS App (SwiftUI)
                          │
              project_id  │  poll state / send actions
                          ▼
                     FastAPI Server (stateless API layer)
                     ┌──────────────────────────────────────┐
                     │                                      │
                     │  /projects    → start/query workflow  │
                     │  /photos      → upload to S3 + signal │
                     │  /scan        → parse + signal        │
                     │  /intake      → WebSocket or REST     │
                     │  /iterate     → signal workflow       │
                     │  /approve     → signal workflow       │
                     │                                      │
                     │  All async work is delegated to       │
                     │  Temporal via signals & queries.       │
                     │  FastAPI holds NO workflow state.      │
                     │                                      │
                     └──────────────┬───────────────────────┘
                                    │
                          signals   │  queries
                                    ▼
                     ┌──────────────────────────────────────┐
                     │         Temporal Server               │
                     │  (workflow orchestration + durable    │
                     │   state + timers + retry)             │
                     │                                      │
                     │  DesignProjectWorkflow                │
                     │  ├── wait for photos (signals)        │
                     │  ├── wait for scan (signal)           │
                     │  ├── wait for intake (signals)        │
                     │  ├── generate_design_options (act.)   │
                     │  ├── wait for selection (signal)      │
                     │  ├── iteration loop (signals + act.)  │
                     │  ├── generate_shopping_list (act.)    │
                     │  ├── 24h grace timer                  │
                     │  └── purge_project_data (act.)        │
                     │                                      │
                     └──────────────┬───────────────────────┘
                                    │
                         dispatches │ activities
                                    ▼
                     ┌──────────────────────────────────────┐
                     │       Temporal Worker (Python)        │
                     │                                      │
                     │  Activities:                          │
                     │  ├── validate_photo_content           │
                     │  │   └── OpenAI gpt-4o-mini           │
                     │  ├── generate_design_options          │
                     │  │   └── OpenAI gpt-image-1           │
                     │  ├── run_lasso_inpainting             │
                     │  │   └── Replicate Flux Fill Pro       │
                     │  ├── run_full_regenerate              │
                     │  │   └── OpenAI gpt-image-1           │
                     │  ├── run_intake_turn                  │
                     │  │   └── Anthropic Claude Sonnet 4.5   │
                     │  ├── generate_shopping_list           │
                     │  │   └── Claude + Exa API             │
                     │  └── purge_project_data               │
                     │      └── S3 delete                    │
                     │                                      │
                     └──────────────────────────────────────┘
                                    │
                                    ▼
                     ┌──────────────────────────────────────┐
                     │           S3 / Cloudflare R2          │
                     │  (photos, generated images, masks)    │
                     └──────────────────────────────────────┘
```

### Key Architectural Properties

- **FastAPI is stateless**: It proxies HTTP requests to Temporal signals/queries. It can be restarted or scaled horizontally without state concerns.
- **Temporal owns all workflow state**: Current step, uploaded photos, design brief, generated images, iteration history, shopping list — all live in Temporal's durable storage.
- **The iOS app reconnects via project_id**: Project ID = Temporal workflow ID. On app reopen, `GET /projects/:id` queries the workflow and returns the full current state. The app renders the correct screen.
- **Crash recovery is automatic**: If the worker dies mid-activity, Temporal retries it. If the server dies, the workflow is unaffected. If the user's app crashes, the workflow waits patiently for the next signal.

---

## 10. Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Backend framework | FastAPI (Python) | Best AI SDK ecosystem, image processing, hackathon speed |
| Workflow orchestration | Temporal | Durable execution, automatic crash recovery, built-in timers for data lifecycle, eliminates custom state machine |
| Database | SQLite (MVP, metadata only) | Zero-config; workflow state lives in Temporal |
| Object storage | Cloudflare R2 or S3 | Cheap image storage with CDN |
| Initial generation model | gpt-image-1 (medium quality) | Best prompt understanding for design briefs |
| Inpainting model | Flux.1 Fill Pro (via Replicate) | Precise mask adherence for lasso edits |
| Intake agent model | Claude Sonnet 4.5 | Best quality/cost/speed for conversation |
| Photo validation (content) | gpt-4o-mini with vision | Cheap, accurate, no infra to manage |
| Shopping list search | Exa API | Real-time product search, no catalog needed |
| Shopping list parsing | Claude Sonnet 4.5 | Structured extraction from search results |
| Async pattern | Temporal activities + client polls `GET /projects/:id` | Simpler than WebSocket; Temporal handles retry/timeout; works through CDNs |
| Chat pattern | WebSocket (or REST fallback) for intake | Bidirectional conversation; intake turns are Temporal signals internally |
| Data lifecycle | Temporal timers (24h grace, 48h abandon) | No external cron; lifecycle tied to workflow execution |
| Estimated cost per session | ~$0.50 typical | Well within viable range |

---

*End of analysis.*
