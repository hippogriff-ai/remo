# Remo MVP — Final Implementation Plan

> **Version**: 2.0 (refined from v1.0 via 5-specialist review)
> **Date**: 2026-02-10
> **Input**: `specs/PRODUCT_SPEC.md` v1.0, `specs/PLAN_0210.md` v1.0 (draft), 5 specialist analyses
> **Team structure**: 4 teams — parallel execution
> **Target**: Hackathon MVP (~12 calendar days)

---

## 1. Executive Summary

Remo is an AI-powered room redesign app: users photograph their room, describe their style, and receive photorealistic redesign options they can iteratively refine, culminating in a downloadable design image and a shoppable product list with real purchase links.

---

## 2. Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **iOS UI** | SwiftUI (iOS 17+) | `@Observable`, `NavigationStack`, native list gestures |
| **iOS UIKit bridges** | `UIImagePickerController`, `RoomCaptureView`, `UIScrollView`, `UIActivityViewController` | Camera, LiDAR, zoomable image, share sheet |
| **iOS networking** | `URLSession` + `async/await` | Native, no dependencies |
| **iOS module system** | Local SPM packages | Avoids Xcode project file merge conflicts in parallel dev |
| **Backend framework** | Python 3.12+ / FastAPI | Best AI SDK ecosystem; auto OpenAPI docs |
| **Workflow orchestration** | Temporal (Python SDK `temporalio`) | Durable execution, resume, retry, timers |
| **Image generation** | Gemini 3 Pro Image (`gemini-3-pro-image-preview`) or Gemini 2.5 Flash Image (`gemini-2.5-flash-preview-image-generation`) | P0 spike tests both; winner used for all activities. Same Google AI API key/SDK — only model ID differs |
| **Intake chat agent** | Claude Opus 4.6 (`claude-opus-4-6`) | Most capable for adaptive conversation; structured tool-use output |
| **Photo validation** | On-device (blur + resolution) + server-side Claude Haiku 4.5 (content classification) | Instant feedback for basic checks; cheap VLM for room/people detection |
| **Product search** | Exa search API | Real-time product search from major retailers |
| **Product scoring** | Claude Opus 4.6 with rubric-based scoring | Structured rubric > holistic confidence; better calibrated |
| **Object storage** | Cloudflare R2 | Free egress, S3-compatible, lifecycle rules |
| **Metadata database** | Railway PostgreSQL |  |
| **Hosting** | Railway (2 services: API + Worker) | Fast deploys, private networking between services |
| **Temporal hosting** | Temporal Cloud ($1K free credits, then $100/mo Essentials) | Zero ops; managed UI; shared dev namespace for all teams |
| **CI/CD** | GitHub Actions → Railway auto-deploy | Push-to-deploy on `main` |

---

## 3. Architecture Overview

### System Diagram

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
                        │                          │
                        │  Photo validation runs   │
                        │  synchronously in handler │
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
                        │   ├── activity: generate  │
                        │   ├── wait: select/restart│
                        │   ├── activity: iterate   │ (loop ×5)
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
                        │   ├── run_intake_chat    │ → Claude Opus 4.6
                        │   ├── generate_designs   │ → Gemini 3 Pro Image
                        │   ├── generate_inpaint   │ → Gemini 3 Pro Image
                        │   ├── generate_regen     │ → Gemini 3 Pro Image
                        │   ├── generate_shopping  │ → Claude + Exa
                        │   └── purge_project      │ → R2 + DB cleanup
                        └──┬──────┬──────┬─────────┘
                           │      │      │
                    ┌──────┘  ┌───┘  ┌───┘
                    ▼         ▼      ▼
               Google AI   Anthropic    Exa     Cloudflare R2    Railway PG
               (Gemini 3   (Claude)     API     (images)         (metadata)
               Pro Image)
```

**Key architectural decision**: Photo validation runs synchronously in the FastAPI handler (not as a Temporal activity) because it's fast (<3s) and needs immediate user feedback. The Claude Haiku 4.5 call (image input) is made directly from the API handler.

### Temporal Workflow Design (Corrected)

```python
@workflow.defn
class DesignProjectWorkflow:
    """One instance per design project. Workflow ID = project_id."""

    def __init__(self):
        self.step = "photos"
        self.photos: list[PhotoData] = []
        self.scan_data: ScanData | None = None
        self.scan_skipped = False          # FIX: was missing in draft
        self.intake_skipped = False         # FIX: was missing in draft
        self.design_brief: DesignBrief | None = None
        self.generated_options: list[DesignOption] = []
        self.selected_option: int | None = None
        self.current_image: str | None = None
        self.revision_history: list[RevisionRecord] = []
        self.iteration_count = 0
        self.shopping_list: ShoppingListOutput | None = None
        self.approved = False
        self.error: WorkflowError | None = None  # NEW: error state for client
        self._action_queue: list[tuple] = []      # NEW: queue pattern for edits
        self._restart_requested = False            # NEW: "Start Over" support
        self._last_activity_at = workflow.now()    # NEW: abandonment tracking

    @workflow.run
    async def run(self, project_id: str):
        # --- Phase: Photos ---
        await self._wait_with_abandonment(
            lambda: len(self.photos) >= 2 and self._all_valid()
        )

        # --- Phase: Scan ---
        self.step = "scan"
        await self._wait_with_abandonment(
            lambda: self.scan_data is not None or self.scan_skipped
        )

        # --- Phase: Intake (with Start Over loop) ---
        while True:
            self.step = "intake"
            self._restart_requested = False
            await self._wait_with_abandonment(
                lambda: self.design_brief is not None or self.intake_skipped
            )

            # --- Phase: Generation ---
            self.step = "generation"
            try:
                self.generated_options = await workflow.execute_activity(
                    generate_designs, args=[self._generation_context()],
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=RetryPolicy(maximum_attempts=2)
                )
                self.error = None
            except ActivityError as e:
                self.error = WorkflowError(message="Generation failed", retryable=True)
                await self._wait_with_abandonment(lambda: self.error is None)
                continue

            # --- Phase: Selection ---
            self.step = "selection"
            await self._wait_with_abandonment(
                lambda: self.selected_option is not None or self._restart_requested
            )
            if self._restart_requested:
                # Reset for Start Over
                self.generated_options = []
                self.selected_option = None
                self.design_brief = None
                self.intake_skipped = False
                continue  # Back to intake

            self.current_image = self.generated_options[self.selected_option].image_url
            break  # Exit the intake/generation/selection loop

        # --- Phase: Iteration (up to 5 rounds) ---
        self.step = "iteration"
        while self.iteration_count < 5 and not self.approved:
            await self._wait_with_abandonment(
                lambda: len(self._action_queue) > 0 or self.approved
            )
            if self.approved:
                break

            action_type, payload = self._action_queue.pop(0)
            try:
                if action_type == "lasso":
                    result = await workflow.execute_activity(
                        generate_inpaint, args=[self._inpaint_context(payload)],
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=RetryPolicy(maximum_attempts=2)
                    )
                elif action_type == "regen":
                    result = await workflow.execute_activity(
                        generate_regen, args=[self._regen_context(payload)],
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=RetryPolicy(maximum_attempts=2)
                    )
                self.current_image = result.revised_image_url
                self.iteration_count += 1
                self.revision_history.append(result)
                self.error = None
            except ActivityError:
                self.error = WorkflowError(message="Revision failed", retryable=True)

        # --- Phase: Approval ---
        if not self.approved:
            self.step = "approval"
            await self._wait_with_abandonment(lambda: self.approved)

        # --- Phase: Shopping List ---
        self.step = "shopping"
        try:
            self.shopping_list = await workflow.execute_activity(
                generate_shopping_list, args=[self._shopping_context()],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=2)
            )
        except ActivityError:
            self.error = WorkflowError(message="Shopping list failed", retryable=True)
            await self._wait_with_abandonment(
                lambda: self.shopping_list is not None
            )

        # --- Phase: Completed + 24h purge timer ---
        self.step = "completed"
        await workflow.sleep(timedelta(hours=24))  # FIX: workflow.sleep, NOT asyncio.sleep
        await workflow.execute_activity(purge_project_data, args=[project_id])

    async def _wait_with_abandonment(self, condition, timeout_hours=48):
        """Wait for condition OR 48h abandonment timeout."""
        try:
            await workflow.wait_condition(condition, timeout=timedelta(hours=timeout_hours))
            self._last_activity_at = workflow.now()
        except asyncio.TimeoutError:
            # Abandoned — purge and terminate
            await workflow.execute_activity(
                purge_project_data, args=[self.project_id]
            )
            return  # Workflow ends

    # --- Signals ---
    @workflow.signal
    async def add_photo(self, photo: PhotoData):
        self.photos.append(photo)

    @workflow.signal
    async def complete_scan(self, scan: ScanData):
        self.scan_data = scan

    @workflow.signal
    async def skip_scan(self):
        self.scan_skipped = True

    @workflow.signal
    async def complete_intake(self, brief: DesignBrief):
        self.design_brief = brief

    @workflow.signal
    async def skip_intake(self):
        self.intake_skipped = True

    @workflow.signal
    async def select_option(self, index: int):
        self.selected_option = index

    @workflow.signal
    async def start_over(self):                    # NEW: required by product spec
        self._restart_requested = True

    @workflow.signal
    async def submit_lasso_edit(self, edit: LassoEdit):
        self._action_queue.append(("lasso", edit))  # FIX: queue pattern

    @workflow.signal
    async def submit_regenerate(self, feedback: str):
        self._action_queue.append(("regen", feedback))  # FIX: queue pattern

    @workflow.signal
    async def approve_design(self):
        self.approved = True

    @workflow.signal
    async def retry_failed_step(self):             # NEW: clear error, re-attempt
        self.error = None

    @workflow.signal
    async def cancel_project(self):                # NEW: user-initiated cancel
        await workflow.execute_activity(purge_project_data, args=[self.project_id])

    # --- Queries ---
    @workflow.query
    def get_state(self) -> WorkflowState:
        return WorkflowState(
            step=self.step,
            photos=self.photos,
            scan_data=self.scan_data,
            design_brief=self.design_brief,
            generated_options=self.generated_options,
            selected_option=self.selected_option,
            current_image=self.current_image,
            revision_history=self.revision_history,
            iteration_count=self.iteration_count,
            shopping_list=self.shopping_list,
            approved=self.approved,
            error=self.error,                      # NEW: error state
        )
```

**Bugs fixed from draft**:
1. `asyncio.sleep` → `workflow.sleep` for 24h purge timer
2. Added `scan_skipped` and `intake_skipped` initialization
3. Added 48h abandonment timeout at every wait point via `_wait_with_abandonment`
4. Added `start_over` signal (required by product spec Section 4.6)
5. Added `cancel_project` signal
6. Separated `generate_inpaint` and `generate_regen` activities (different input contracts)
7. Used queue pattern for edit actions (prevents race condition)
8. Added error state tracking and `retry_failed_step` signal
9. Added `WorkflowError` to query response

---

## 4. Team Structure (4 Teams)

```
                    ┌─────────────────────────────────┐
                    │  T0: Platform & Backend Services │
                    │  Contracts, Temporal, API,       │
                    │  Photo validation, LiDAR,        │
                    │  DB schema, R2, CI/CD            │
                    │  THEN: Integration lead          │
                    └──────────────┬──────────────────┘
                                   │ publishes contracts (P0)
            ┌──────────────────────┼──────────────────────┐
            ▼                      ▼                      ▼
      ┌──────────────┐  ┌──────────────────┐  ┌────────────────┐
      │ T1: iOS App  │  │ T2: Image Gen    │  │ T3: AI Agents  │
      │ All SwiftUI/ │  │ Pipeline         │  │ (Intake +      │
      │ UIKit work   │  │ Gemini gen/      │  │  Shopping)     │
      └──────────────┘  │ inpaint/regen    │  └────────────────┘
                        └──────────────────┘
```

### Team Responsibilities

#### T0: Platform & Backend Services

| Deliverable | Phase | Success Metric |
|------------|-------|----------------|
| Temporal Cloud namespace operational | P0 | `tctl namespace describe quickstart-remo-tempo` succeeds; all teams can connect |
| `docker-compose.yml` for local dev (API + Worker + PG) | P0 | `docker compose up` gives a working local stack in <5 min |
| All Pydantic contract models (`backend/models/contracts.py`) | P0 (GATE) | All `*Input/*Output` models importable; validation tests pass |
| Database schema (Alembic migration) | P0 | `alembic upgrade head` creates all tables; CASCADE verified |
| R2 bucket + pre-signed URL generation | P0 | Upload/download test object succeeds |
| FastAPI gateway (all endpoints, stub responses) | P0 | All 13 endpoints return correct status codes and response shapes |
| `DesignProjectWorkflow` skeleton (signals, queries, mock activities) | P0 | Workflow transitions through all steps with test signals |
| Mock API operational for iOS team | P0 (GATE) | iOS app can create project → query state → send signals → see transitions |
| Swift API models (mirrors Pydantic) | P0 | All models decode mock JSON responses without errors |
| CI pipeline (ruff + mypy + pytest) | P0 | Green on every PR to main |
| Photo validation activity (blur + resolution + Claude Haiku 4.5) | P1 | Correctly rejects blurry/low-res/non-room images; passes valid ones |
| LiDAR dimension parser | P1 | Parses RoomPlan JSON into RoomDimensions model |
| Integration: wire real activities into workflow | P2 | Each activity produces real results through the workflow |

#### T1: iOS App

| Deliverable | Phase | Success Metric |
|------------|-------|----------------|
| Xcode project + local SPM package structure | P0 | All packages build; empty placeholder screens for every step |
| `WorkflowClientProtocol` + `MockWorkflowClient` | P0 | Mock client simulates step transitions with realistic delays |
| Photo Upload UI (camera + gallery + validation feedback) | P1 | 2 room photos required; 3 inspiration optional; validation messages shown |
| Chat Interface (bubbles, quick-reply chips, text input) | P1 | Mock conversation renders correctly; progress indicator works |
| Design Comparison (swipeable + side-by-side toggle) | P1 | 2 mock images swipeable; selection highlighting works |
| Output Screen (save to photos, share) | P1 | Image saves to camera roll; share sheet opens |
| Home Screen (pending projects, resume) | P1 | Mock projects list renders; tap resumes at correct step |
| Navigation + Router (full flow) | P1 | Push to any step via WorkflowState; back navigation works |
| Lasso Tool MVP (1 region, freehand, auto-close, editor) | P1 | Draw region → editor opens → save → "Generate Revision" enabled |
| Shopping List UI (grouped cards, buy links, fit badges) | P1 | 8 mock products render in 4 groups; total cost displayed |
| LiDAR Scan UI (RoomPlan wrapper, skip flow) | P1 | Device check works; skip flow shows trade-off notification |
| Swap mock API for real API | P2 | Full flow works against real backend |
| Lasso multi-region (overlap detection, edit list, reorder) | P2 | Up to 3 regions; overlap blocked; renumbering works |

#### T2: Image Generation Pipeline

| Deliverable | Phase | Success Metric |
|------------|-------|----------------|
| Gemini quality spike (MUST be first task) | P0 | Both models tested on 3 room photos; mask precision, photorealism, architecture preservation scored per model |
| Model selection decision document | P0 | Side-by-side results; winning model chosen with rationale; escalation plan if neither passes |
| `generate_designs` activity | P1 | Takes room photos + brief → returns 2 design image URLs in R2 |
| Mask generation utility (polygon → binary mask) | P1 | Renders 1-3 polygon regions into a correctly scaled binary mask |
| Prompt template library | P1 | `prompts/` directory with versioned templates for each mode |
| `generate_inpaint` activity | P1 | Takes base image + mask + instructions → returns revised image URL; SSIM > 0.98 outside mask |
| `generate_regen` activity | P1 | Takes context + feedback → returns new design URL; visibly different from input |
| Quality test suite | P2 | 5+ test cases per activity with scored results; 70%+ meet quality bar |

#### T3: AI Agents — Intake + Shopping

| Deliverable | Phase | Success Metric |
|------------|-------|----------------|
| Claude system prompt for Quick Intake | P1 | ~3-turn adaptive flow produces valid DesignBrief JSON 100% of the time |
| Structured output via tool use (DesignBrief) | P1 | Every response calls `update_design_brief` + `respond_to_user` tools |
| `run_intake_chat` activity (Quick mode) | P1 | Temporal activity completes in <60s per turn; output matches IntakeChatOutput contract |
| Intake eval harness (DesignBrief Quality Rubric) | P1 | Automated rubric scoring: ≥ 85/100 across 8 golden test conversations |
| Shopping list: anchored item extraction | P1 | Extracts 6+ items using brief + iteration history + image; correct source tagging ≥ 90% |
| Shopping list: Exa search integration | P1 | Parallelized search returns product pages for 80%+ of items |
| Shopping list: rubric-based scoring | P1 | Scores products on 5 criteria; produces calibrated 0-1 scores |
| Shopping pipeline eval suite | P1 | Automated eval for extraction, search, and scoring criteria (see Section 7) |
| `generate_shopping_list` activity | P1 | Takes image + brief + iterations → returns structured ProductMatch list; 5+ items with working URLs |
| Golden test suite for intake | P1 | 8 scripted conversations; brief quality ≥ 70/100; adaptive behavior verified |
| Full Intake mode (~10 turns, adaptive) | P2 | Domain notepad tracking works; agent adapts question order based on responses; brief quality ≥ 80/100 |
| Open Conversation mode | P3 | Free-form conversation with domain notepad; caps at ~15 turns; gracefully wraps up |

---

## 5. Data Model

### Entity Relationships

```
Project (Temporal Workflow) 1──N Photo
Project 1──0..1 LidarScan
Project 1──0..1 DesignBrief
Project 1──N GeneratedImage
Project 1──N Revision
Revision 1──N LassoRegion
Project 1──0..1 ShoppingList
ShoppingList 1──N ProductMatch
```

### PostgreSQL Schema (Lean — Temporal owns workflow state)

The database stores **data artifacts only**. Workflow state (step, iteration_count, approved status) lives exclusively in Temporal. The database does NOT duplicate Temporal state.

| Entity | Key Fields |
|--------|-----------|
| **Project** | `id` (UUID = Temporal workflow ID), `device_fingerprint`, `has_lidar`, `created_at`, `updated_at` |
| **Photo** | `id`, `project_id` (FK CASCADE), `type` (room/inspiration), `storage_key`, `note`, `validation_passed`, `validation_error` |
| **LidarScan** | `id`, `project_id` (FK CASCADE, UNIQUE), `storage_key`, `room_dimensions` (JSONB) |
| **DesignBrief** | `id`, `project_id` (FK CASCADE, UNIQUE), `intake_mode`, `brief_data` (JSONB), `conversation_history` (JSONB) |
| **GeneratedImage** | `id`, `project_id` (FK CASCADE), `type` (initial/revision/overlay), `storage_key`, `selected`, `is_final`, `generation_model` |
| **Revision** | `id`, `project_id` (FK CASCADE), `revision_number` (1-5), `type` (lasso/regen), `base_image_id` (FK), `result_image_id` (FK), `edit_payload` (JSONB) |
| **LassoRegion** | `id`, `revision_id` (FK CASCADE), `region_number` (1-3), `path_points` (JSONB, normalized 0-1), `action`, `instruction`, `avoid_tokens`, `style_nudges` |
| **ShoppingList** | `id`, `project_id` (FK CASCADE, UNIQUE), `generated_image_id` (FK), `total_estimated_cost_cents` (INT) |
| **ProductMatch** | `id`, `shopping_list_id` (FK CASCADE), `category_group`, `product_name`, `retailer`, `price_cents` (INT), `product_url`, `image_url`, `confidence_score`, `why_matched`, `fit_status`, `fit_detail` |

### Required Indexes

```sql
CREATE INDEX idx_photos_project_type ON photos(project_id, type);
CREATE INDEX idx_generated_images_project ON generated_images(project_id, type);
CREATE INDEX idx_revisions_project ON revisions(project_id, revision_number);
CREATE INDEX idx_product_matches_list ON product_matches(shopping_list_id);
```

### Key Design Decisions

- **Integer cents for all monetary values** — avoids floating-point money issues
- **All FKs use `ON DELETE CASCADE`** — purge activity deletes from `projects`, children cascade
- **Schema frozen in Phase 0** — only T0 creates migrations; other teams request changes via T0
- **JSONB for semi-structured data** — write-once/read-many blobs that don't need relational querying

### Storage Layout (Cloudflare R2)

```
Bucket: remo-assets (dev: remo-assets-dev)

/projects/{project_id}/
  photos/room_0.jpg, room_1.jpg
  photos/inspiration_0.jpg ... inspiration_2.jpg
  lidar/dimensions.json
  lidar/raw.usdz                    # backup only
  generated/option_0.png, option_1.png
  generated/revision_1.png, revision_1_overlay.png ...
  generated/final.png
```

**R2 lifecycle rule: 120h (5 days)** — safety net behind Temporal-driven purge. Set to 120h (not 72h) to prevent premature deletion of early-uploaded photos in long-running projects.

**Upload flow**: iOS → FastAPI handler → R2 (server-side upload). Keeps R2 credentials server-side.
**Download flow**: FastAPI generates 1-hour pre-signed GET URLs. No CDN for MVP.

---

## 6. API Design

### FastAPI → Temporal Gateway

| Method | Endpoint | Action | Response |
|--------|----------|--------|----------|
| `POST` | `/api/v1/projects` | Start workflow | `{ project_id }` |
| `GET` | `/api/v1/projects/{id}` | Query state | Full `WorkflowState` |
| `DELETE` | `/api/v1/projects/{id}` | Cancel + purge | 204 |
| `POST` | `/api/v1/projects/{id}/photos` | Upload → validate → signal | `{ photo_id, validation }` |
| `POST` | `/api/v1/projects/{id}/scan` | Upload → signal | 200 |
| `POST` | `/api/v1/projects/{id}/scan/skip` | Signal skip_scan | 200 |
| `POST` | `/api/v1/projects/{id}/intake/start` | Begin intake with mode | `{ agent_message, options }` |
| `POST` | `/api/v1/projects/{id}/intake/message` | Send user message | `{ agent_message, options, progress, is_summary, partial_brief }` |
| `POST` | `/api/v1/projects/{id}/intake/confirm` | Signal complete_intake | 200 |
| `POST` | `/api/v1/projects/{id}/intake/skip` | Signal skip_intake | 200 |
| `POST` | `/api/v1/projects/{id}/select` | Signal select_option | 200 |
| `POST` | `/api/v1/projects/{id}/start-over` | Signal start_over | 200 |
| `POST` | `/api/v1/projects/{id}/iterate/lasso` | Signal submit_lasso_edit | 200 |
| `POST` | `/api/v1/projects/{id}/iterate/regenerate` | Signal submit_regenerate | 200 |
| `POST` | `/api/v1/projects/{id}/approve` | Signal approve_design | 200 |
| `POST` | `/api/v1/projects/{id}/retry` | Signal retry_failed_step | 200 |
| `GET` | `/health` | Health check | `{ postgres, temporal, r2 }` |

### Async Job Pattern (Polling, Not SSE)

For MVP, the iOS app handles long-running activities (generation, shopping list) via polling:

1. Send the signal (e.g., `select_option`) which triggers the activity
2. Poll `GET /projects/{id}` every 2-3 seconds
3. When `step` changes or `current_image` / `shopping_list` is populated → activity completed
4. If `error` is populated → show retry UI

**SSE is a post-MVP enhancement.** Polling is simpler, works through all CDNs/proxies, and eliminates the need for Redis or server-side state for SSE connections.

### Error Response Model (Consistent)

```python
class ErrorResponse(BaseModel):
    error: str           # machine-readable code (e.g., "workflow_not_found")
    message: str         # human-readable message
    retryable: bool
    detail: str | None = None

# Status codes:
# 400 — Invalid input (Pydantic validation failure)
# 404 — Project/workflow not found (purged or never existed)
# 409 — Conflict (signal sent to wrong workflow step)
# 429 — Rate limited (Retry-After header)
# 500 — Unexpected server error
# 502 — Upstream API error (Gemini, Claude, Exa down)
```

### Photo Validation (Synchronous in Handler)

```
POST /projects/{id}/photos
  → Upload to R2
  → Blur check (Laplacian variance on normalized 1024px image, threshold ~60-80) — <50ms
  → Resolution check (Pillow, min 1024px shortest side) — <10ms
  → Content classification (Claude Haiku 4.5, image input) — ~1-2s
  → Return { photo_id, validation: { passed, failures[] } }
  → If passed: signal Temporal workflow
```

Blur threshold should be calibrated with 20+ real room photos at normalized resolution. Start with 60 and adjust.

---

## 7. AI Pipeline Details

### P0 Gemini Quality Spike (MANDATORY — T2's first task)

**Why**: Both Gemini models are preview-stage. We've never tested either for room redesign with precise region masking. If T2 picks a model blind and builds 3 activities on it during P1, then discovers at P2 integration that masks bleed or room architecture distorts, that's an entire phase wasted. The spike is a 2-3 hour comparative test that picks the best model before any real code is written.

**What to test** (run identical tests on BOTH models):

1. Upload 3 real room photos to **both** Gemini 3 Pro Image and Gemini 2.5 Flash Image
2. Generate redesigns with a sample brief on each
3. Test inpainting with precise polygon masks on each
4. Score each model on: (a) mask boundary adherence, (b) photorealism, (c) room architecture preservation, (d) style consistency
5. Document results with side-by-side screenshots

**Decision gate**:

- **One or both models pass** (mask boundary bleeding ≤ ~5% of non-masked area in 4+ of 5 test cases) → Pick the higher-scoring model. Build all activities on it. Proceed to P1.
- **Neither model passes** → Escalate. Evaluate alternatives (dedicated inpainting models, hybrid approach, or adjusted quality bar). Do NOT proceed to P1 until resolved.

### Image Generation: All Modes

| Mode | Input | Output | Latency | Cost |
|------|-------|--------|---------|------|
| **Initial (2 options)** | Room photos + brief + inspiration | 2 redesign images (1K/2K) | ~15-30s parallel | ~$0.268 |
| **Lasso Inpaint** | Current image + binary mask + instructions | Revised image (masked regions changed) | ~15-30s | ~$0.134 |
| **Full Regenerate** | Room photos + brief + feedback + history | New full design image | ~15-30s | ~$0.134 |

### Prompt Template Strategy

Create a `prompts/` directory with versioned templates:

```
backend/prompts/
  generation.txt          # Initial 2-option generation
  inpaint.txt             # Lasso inpainting
  regeneration.txt        # Full regenerate
  room_preservation.txt   # Shared clause (camera angle, walls, architecture)
  intake_system.txt       # Intake agent system prompt
  item_extraction.txt     # Shopping list item extraction
  product_scoring.txt     # Shopping list product scoring
```

**Room structure preservation clause** (included in ALL generation calls):
```
Preserve the exact camera angle, room geometry, walls, ceiling, windows,
doors, and floor plane from the reference photo. Do not modify the room
architecture or viewing perspective.
```

### Mask Generation

Polygon coordinates arrive as normalized 0-1 values from the iOS app. The mask generation utility:
1. Receives `path_points: list[tuple[float, float]]` per region
2. Scales to actual image dimensions
3. Renders filled polygons onto a blank image using Pillow/OpenCV
4. Applies small Gaussian feather (2-3px) at mask boundaries for better blending
5. Composites multiple regions into a single mask

This utility must be built in P0/P1, not deferred to P2.

### Intake Chat Agent

**System prompt structure** (3 sections):
1. **Identity**: "You are a friendly interior design consultant helping a homeowner redesign their room..."
2. **Behavioral rules**: Adaptive questioning, domain tracking, skip covered domains, follow-up on unexpected topics
3. **Output format**: Must call `update_design_brief` and `respond_to_user` tools on every turn

**Mode differentiation** (guiding principle: the agent has a **notepad** of 10 design domains that keeps it on track, but it uses its intelligence to react to user responses and decide what to ask next — NOT a fixed questionnaire):

- Quick: "You have a notepad of 10 design domains. Select the 3 most impactful for {room_type}. Pre-plan 3 questions, but adapt — if the user's answer covers multiple domains, skip ahead. Target ~3 turns, then summarize."
- Full: "You have a notepad of 10 design domains. Pre-plan 10 questions covering all domains in priority order. After each user response, re-evaluate: reorder remaining questions, merge or swap later ones based on what you've learned. Skip domains already covered. The notepad keeps you on track; your intelligence picks the best next question."
- Open: "Begin with an open prompt. Follow the user's lead. Track domains on your notepad internally. When conversation energy slows or the user seems done, steer toward uncovered domains. Cap at ~15 turns — gracefully wrap up and summarize."

**Turn counter**: Track server-side (not relying on model to count). Model reports domain coverage in `update_design_brief`; server increments turn counter. Quick mode terminates around 3 turns, Full around 10, Open caps at ~15.

### Intake Agent Eval: DesignBrief Quality Rubric (out of 100)

Every prompt change must be evaluated against golden test conversations using this rubric. A second Claude call scores the output brief + conversation transcript.

| Criterion | Weight | Full marks | Zero |
|-----------|--------|-----------|------|
| **Style Coherence** | 15 | Named style with 2-3 defining characteristics. No contradictions. | Conflicting styles, or vague ("nice, modern") |
| **Color Strategy** | 15 | Named palette with primary/accent distinction and complementary logic. e.g., "warm neutrals (sand, cream) with navy accents at 70/20/10" | Just color names with no relationship |
| **Lighting Design** | 10 | Addresses layers (ambient, task, accent), natural light optimization, color temperature. e.g., "warm ambient 2700K, task at desk, accent on gallery wall" | Missing entirely, or just "warm lighting" |
| **Space & Proportion** | 10 | Furniture scale vs room, traffic flow, focal point placement. References LiDAR dimensions if available. | No spatial awareness |
| **Material & Texture Specificity** | 15 | Precise descriptors: "weathered oak," "brushed brass," "boucle upholstery" | Generic ("wood," "metal," "nice fabric") |
| **Actionability** | 20 | Every field translates directly into Gemini generation prompt language. A prompt engineer needs zero guesswork. | Abstract feelings with no visual anchor |
| **Completeness** | 10 | Covers: room purpose, style, colors, lighting, textures, key furniture, constraints, keep_items. | Only 1-2 domains populated |
| **User Fidelity** | 5 | Every preference traces to a user statement. Agent-inferred preferences are marked. | Hallucinated preferences user never expressed |

**Thresholds**: ≥85 `PASS:EXCELLENT` | 70-84 `PASS` | 50-69 `FAIL:WEAK` | <50 `FAIL:POOR`

**Automated eval loop**: For each golden test conversation, run the intake agent → score the brief with this rubric → per-criterion breakdown tells the coding agent which dimension to improve in the system prompt (e.g., "color_strategy: 9/15 — no primary/accent distinction" → adjust prompt to push for color ratios).

### Shopping List Pipeline

**Three input sources** — avoids the "telephone game" (text → image → text) by anchoring search in what we already know:

| Source | What it gives us | Priority |
|--------|-----------------|----------|
| **DesignBrief** (from intake) | User's *intent* — style, colors, textures, specific requests, keep_items | Highest — user's own words |
| **Iteration history** (lasso/regen) | Amendments — what changed from original vision | Overrides brief for changed items |
| **Final approved image** | Ground truth — what's actually rendered, including AI additions | Fills gaps for items not in brief/iterations |

```
DesignBrief + Iteration History + Approved Image + Original Room Photos
    ↓
[1] Anchored Item Extraction (Claude Opus 4.6, image input)
    → Receives ALL three sources + original room photos
    → For each item, classifies source:
      (a) BRIEF-ANCHORED — explicitly in DesignBrief → use user's language for search
      (b) ITERATION-ANCHORED — changed during lasso/regen → use iteration instruction
      (c) IMAGE-ONLY — AI addition not in brief or iterations → vision-derived description
      (d) EXISTING — visible in original room photo AND keep_items → SKIP
    → 6-10 items with: category, style, material, color, proportions, source_tag
    ↓
[2] Exa Search (parallelized, queries differ by source)
    → BRIEF-ANCHORED: "{user's own words from brief} + {style_profile}"
       e.g., "mid-century walnut coffee table" (specific, high confidence)
    → ITERATION-ANCHORED: "{iteration instruction keywords}"
       e.g., "marble coffee table modern" (from lasso "replace with marble")
    → IMAGE-ONLY: "{AI-described category} {material} {color}"
       e.g., "brass arc floor lamp" (less specific, lower baseline confidence)
    → 2-3 query variants per item; dimension-aware if LiDAR available
    ↓
[3] Rubric-Based Scoring (Claude Opus 4.6, parallelized)
    → Category match: +0.3
    → Material match: +0.2
    → Color match: +0.2
    → Style match: +0.2
    → Dimensions match (if LiDAR): +0.1
    → Sum scores for calibrated 0-1 confidence
    ↓
[4] Dimension Filtering (if LiDAR)
    → Cross-reference product dimensions against room geometry
    → Assign fit badge: "fits" / "tight" / filter out
    ↓
[5] Confidence Filtering
    → ≥0.8: show normally
    → 0.5-0.79: show with "Close match" label
    → <0.5: hide; show Google Shopping fallback link
```

### Shopping Pipeline Eval Criteria

**Item Extraction Eval** (run against 5+ test cases with known briefs + iteration histories):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| Brief coverage | % of DesignBrief items found | ≥ 80% | Compare extracted items against brief fields |
| No hallucinations | % of extracted items visible in image | 100% | Second Claude call to verify |
| keep_items respected | None of keep_items in extraction | 100% | String match |
| Source tagging | Items correctly tagged brief/iteration/image-only | ≥ 90% | Compare against brief + iteration content |
| Structured output | Every item has all required fields | 100% | Pydantic validation |

**Search Query Eval** (run against 20+ extracted items):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| URL validity | product_urls returning HTTP 200 | ≥ 90% | Automated HEAD requests |
| Result relevance | Searches returning ≥1 product scoring ≥ 0.5 | ≥ 80% | Check rubric scores |
| Query specificity | Brief-anchored queries use user's terminology | 100% | Check query contains brief keywords |

**Scoring Eval** (run against 30+ scored products):

| Criterion | Metric | Pass | How to test |
|-----------|--------|------|-------------|
| Calibration | ≥0.8 products visually better than 0.5-0.7 products | ≥ 85% pairwise | Second Claude call |
| Rubric compliance | Each sub-score independently correct | ≥ 90% per criterion | Second Claude call to verify each sub-score |
| Discrimination | Score spread uses full 0-1 range | Std dev > 0.15 | Statistical check |

### Cost Per Session (Updated)

| Component | Cost |
|-----------|------|
| Photo validation (4 images × Claude Haiku 4.5) | $0.006 |
| Intake chat (Claude Opus 4.6, full mode, ~8 turns) | $0.15 |
| Initial generation (2 × Gemini 3 Pro Image) | $0.268 |
| Lasso iterations (3 × Gemini 3 Pro Image) | $0.402 |
| Full regenerate (1 × Gemini 3 Pro Image) | $0.134 |
| Shopping list extraction (Claude Opus 4.6, image input) | $0.03 |
| Exa search (~8 queries) | $0.04 |
| Shopping list scoring (~8 Claude calls) | $0.10 |
| **Typical session total** | **~$1.13** |

| Scenario | Cost |
|----------|------|
| Minimal (quick intake, pick first, approve) | ~$0.50 |
| Typical (full intake, 2-3 iterations, 8 items) | ~$1.13 |
| Maximum (open conversation, 5 iterations, 10+ items) | ~$2.00 |

---

## 8. iOS Frontend Architecture

### Navigation: `NavigationStack` + Temporal State

```swift
// Flat enum — no associated values (avoids NavigationPath serialization issues)
enum ProjectStep: String, Codable, Hashable {
    case photoUpload
    case inspirationUpload
    case roomScan
    case intakeChat
    case designGeneration
    case designSelection
    case iteration
    case approval
    case output
}
```

Generation status is tracked separately from navigation step:

```swift
@Observable
class ProjectState {
    var step: ProjectStep
    var generationStatus: GenerationStatus = .idle  // idle, generating, completed, failed
    var iterationRound: Int = 0
    var error: WorkflowError?
    // ... all workflow data
}

enum GenerationStatus: Codable {
    case idle, generating, completed, failed
}
```

### SPM Package Structure (For Parallel iOS Development)

```
Remo/
  Remo.xcodeproj (thin shell, imports packages)
  Packages/
    RemoModels/         # Shared models + protocols (T0 or T1-lead owns)
    RemoNetworking/     # API client, mock client (T1-lead owns)
    RemoPhotoUpload/    # Photo upload UI + validation display
    RemoChatUI/         # Chat interface, quick-reply chips
    RemoLasso/          # Lasso drawing, geometry, region editor
    RemoDesignViews/    # Comparison, iteration, approval, output
    RemoShoppingList/   # Product cards, grouped lists
    RemoLiDAR/          # RoomPlan wrapper, scan screens
```

Each package has its own `Package.swift` — no `.pbxproj` conflicts between packages. This is the single most impactful decision for parallel iOS development.

### Mock API Layer

```swift
protocol WorkflowClientProtocol {
    func createProject() async throws -> String
    func getState(projectId: String) async throws -> WorkflowState
    func sendSignal(_ signal: WorkflowSignal, projectId: String) async throws
    func uploadPhoto(_ data: Data, projectId: String, type: PhotoType) async throws -> PhotoValidationResult
}

// MockWorkflowClient: returns hardcoded responses, simulates delays
// RealWorkflowClient: calls FastAPI backend
```

Mock client ships during **P0** (before P1 begins). All iOS development uses protocol injection:

```swift
struct IterationScreen: View {
    let client: any WorkflowClientProtocol
}

#Preview {
    IterationScreen(client: MockWorkflowClient())
}
```

### Lasso Tool: MVP First, Multi-Region Second

**P1: 1-Region MVP**
- Freehand drawing with auto-close
- Self-intersection detection
- Minimum area validation (2% of image area)
- Fixed-color outline (skip adaptive contrast)
- Simple Region Editor (action + instruction only)
- Basic zoom/pan mode toggle (single-finger = draw, pinch = zoom)
- "Generate Revision" button

**P2: Multi-Region**
- Up to 3 regions per revision
- Overlap detection
- Edit List (bottom sheet / side panel)
- Reorder regions (drag)
- Full Region Editor (action, instruction, avoid, style nudges)
- Adaptive contrast outlines
- Rasterize finalized regions (performance optimization)

**Rectangle fallback**: If freehand proves too buggy mid-P1, switch to rectangle selection (saves significant time).

---

## 9. Contract-First Artifacts (P0 Deliverables)

These MUST exist before parallel work begins. T0 owns all of them.

### Pydantic Activity Contracts

```python
# backend/models/contracts.py — FROZEN at P0 exit gate

# === Shared Types ===
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

class ProductMatch(BaseModel):
    category_group: str
    product_name: str
    retailer: str
    price_cents: int
    product_url: str
    image_url: str | None = None
    confidence_score: float              # 0-1, rubric-based
    why_matched: str
    fit_status: str | None = None        # "fits" / "tight" / None
    fit_detail: str | None = None
    dimensions: str | None = None

class UnmatchedItem(BaseModel):
    category: str
    search_keywords: str
    google_shopping_url: str

class ChatMessage(BaseModel):
    role: str                            # "user" or "assistant"
    content: str

class QuickReplyOption(BaseModel):
    number: int
    label: str
    value: str

class WorkflowError(BaseModel):
    message: str
    retryable: bool

class RevisionRecord(BaseModel):
    revision_number: int
    type: str                            # "lasso" or "regen"
    base_image_url: str
    revised_image_url: str

# === Activity Input/Output ===
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

class GenerateShoppingListInput(BaseModel):
    design_image_url: str
    original_room_photo_urls: list[str]      # to identify existing items (skip)
    design_brief: DesignBrief | None = None
    revision_history: list[RevisionRecord] = []  # iteration amendments
    room_dimensions: RoomDimensions | None = None

class GenerateShoppingListOutput(BaseModel):
    items: list[ProductMatch]
    unmatched: list[UnmatchedItem] = []
    total_estimated_cost_cents: int

class IntakeChatInput(BaseModel):
    mode: Literal["quick", "full", "open"]
    project_context: dict                # room photos, inspiration notes, scan data
    conversation_history: list[ChatMessage]
    user_message: str

class IntakeChatOutput(BaseModel):
    agent_message: str
    options: list[QuickReplyOption] | None = None
    is_open_ended: bool = False
    progress: str | None = None          # "3 of 10 domains covered"
    is_summary: bool = False
    partial_brief: DesignBrief | None = None

class ValidatePhotoInput(BaseModel):
    image_data: bytes
    photo_type: Literal["room", "inspiration"]

class ValidatePhotoOutput(BaseModel):
    passed: bool
    failures: list[str]                  # machine-readable failure codes
    messages: list[str]                  # user-facing messages

class WorkflowState(BaseModel):
    step: str
    photos: list[dict] = []
    scan_data: dict | None = None
    design_brief: dict | None = None
    generated_options: list[DesignOption] = []
    selected_option: int | None = None
    current_image: str | None = None
    revision_history: list[RevisionRecord] = []
    iteration_count: int = 0
    shopping_list: GenerateShoppingListOutput | None = None
    approved: bool = False
    error: WorkflowError | None = None
```

### Contract Change Policy (After P0 Freeze)

1. **Additive** (new optional fields): T0 reviews + merges within hours. Non-breaking.
2. **Breaking** (renamed/removed/retyped fields): Requires discussion with all consuming teams. T0 creates a single PR updating both Pydantic and Swift models. All teams pull before next commit.
3. **Rule**: If you need to change a contract, message T0 immediately. Don't work around it.

---

## 10. Git Worktree & Collaboration Strategy

### Repository Structure

```
remo/
  backend/
    app/
      models/
        contracts.py     # T0 owns — ALL Pydantic models
        db.py            # T0 owns — SQLAlchemy models
      api/
        routes/          # T0 owns — FastAPI endpoints
      workflows/
        design_project.py  # T0 owns — Temporal workflow
      activities/
        generate.py      # T2 owns — generate_designs
        inpaint.py       # T2 owns — generate_inpaint
        regen.py         # T2 owns — generate_regen
        intake.py        # T3 owns — run_intake_chat
        shopping.py      # T3 owns — generate_shopping_list
        validation.py    # T0 owns — validate_photo
        purge.py         # T0 owns — purge_project_data
      utils/
        r2.py            # T0 owns — R2 client wrapper
        image.py         # T0/T2 — mask rendering, image processing
      prompts/           # T2/T3 own — versioned prompt templates
    migrations/          # T0 owns — Alembic only
    tests/
    pyproject.toml
    Dockerfile
  ios/
    Remo.xcodeproj       # T1 owns
    Packages/            # T1 owns (separate SPM packages)
  docker-compose.yml     # T0 owns
  .github/workflows/     # T0 owns
  specs/                 # Shared documentation
```

### Branch Naming Convention

```
team/platform/{feature}
team/ios/{feature}
team/gen/{feature}
team/ai/{feature}
```

### Worktree Setup (One Per Team)

```bash
# T0: Main repo
/Hanalei/remo                     # team/platform/* branches

# T1: iOS worktree
git worktree add /Hanalei/remo-ios team/ios/scaffold

# T2: Image Gen worktree
git worktree add /Hanalei/remo-gen team/gen/gemini-spike

# T3: AI Agents worktree
git worktree add /Hanalei/remo-ai team/ai/intake-agent
```

### PR Merge Order (Hard Dependencies)

```
1. team/platform/scaffold        → main (P0 start) — project structure, deps
2. team/platform/contracts       → main (P0 mid)   — Pydantic models
3. team/platform/temporal        → main (P0 end)   — workflow + mock activities
4. team/platform/api-gateway     → main (P0 end)   — all endpoints
5. team/platform/swift-models    → main (P0 end)   — Swift mirrors of contracts
   ──── All teams can work independently after this point (P1) ────
6. Activity PRs (any order, during P1):
   - team/gen/generate-designs   → main
   - team/gen/inpaint           → main
   - team/gen/regen             → main
   - team/ai/intake-quick       → main
   - team/ai/shopping-pipeline  → main
   - team/platform/validation   → main
7. team/platform/integration-*   → main (P2)       — wire activities into workflow
8. team/ios/integration          → main (P2)       — swap mock for real API
```

### PR Standards

- **Size**: 200-400 lines preferred; single-purpose
- **Review**: T0 contract PRs → reviewed by 1 person from each consuming team. Activity PRs → reviewed by T0 for contract compliance. Bug fixes → self-merge OK if tests pass
- **Merge strategy**: Squash merge to main (clean linear history)
- **Branch protection**: `main` requires 1 approval + passing CI. Team branches: no protection

### Avoiding Merge Conflicts

| Risk | Mitigation |
|------|-----------|
| Pydantic shared models | T0 owns exclusively; frozen at P0 exit gate |
| Xcode project files | Local SPM packages eliminate most `.pbxproj` conflicts |
| Database migrations | T0 owns exclusively; sequential numbering |
| CI/CD configuration | T0 owns; changes are rare |
| Activity files | Each team owns isolated files — no cross-team edits |

---

## 11. Build Phases & Dependency Graph

### Phase Overview

| Phase | Focus | Gate to Exit | Teams Active |
|-------|-------|-------------|-------------|
| **P0: Foundation** | Contracts, scaffold, infra, Gemini spike | Contracts frozen + mock API works + Gemini go/no-go decided | T0 (primary), T1/T2/T3 (setup only) |
| **P1: Independent Build** | All teams build in parallel against contracts | Each team's deliverables pass their own tests | All teams (fully parallel) |
| **P2: Integration** | Wire real activities, connect iOS to real API | End-to-end flow works with real AI | T0 (lead), all teams (support) |
| **P3: Stabilization** | Bugs, edge cases, resume testing, polish | Demo-ready | All teams |

### Phase Dependency Graph

```
P0: Foundation ──────────────────────────────────────────────────
│
│  T0: Temporal setup, docker-compose, project scaffold
│  T0: Pydantic contract models ← GATE (blocks all P1 work)
│  T0: DB schema, R2 bucket, FastAPI stubs, workflow skeleton
│  T0: Mock API ← GATE (blocks T1's real UI work)
│  T0: Swift API models, CI pipeline
│  T1: Xcode project + SPM packages + navigation skeleton
│  T2: Gemini quality spike ← GATE (go/no-go on model choice)
│  T3: Claude system prompt iteration (notebook, no deps)
│
│  EXIT: Contracts frozen, mock API works, Gemini decision made
│
├──────────────────────────────────────────────────────────────────
│
P1: Independent Build (all teams parallel, no cross-team deps)
│
│  T0: Photo validation activity, LiDAR parser, bug fixes
│  T1: All UI screens against mock API (photo, chat, design,
│      lasso MVP, shopping list, LiDAR, home, output, navigation)
│  T2: generate_designs, mask utility, generate_inpaint,
│      generate_regen, prompt templates
│  T3: Quick Intake activity, shopping list pipeline
│      (extraction → Exa → scoring), golden test suite
│
│  EXIT: Each team's deliverables pass their own success metrics
│
├──────────────────────────────────────────────────────────────────
│
P2: Integration (incremental — one activity at a time)
│
│  Step 1: T0 wires generate_designs into workflow (lowest risk)
│          T1 points iOS at real API
│          ✅ TEST: Upload photos → get 2 real generated designs
│
│  Step 2: T0 wires intake_chat + generate_inpaint + generate_regen
│          T1 tests real intake + lasso → inpaint flow
│          T1 builds multi-region lasso
│          ✅ TEST: Full flow through iteration with real AI
│
│  Step 3: T0 wires shopping list pipeline + Temporal timers
│          T1 connects shopping list UI to real data
│          T3 adds Full Intake + Open Conversation modes
│          ✅ TEST: Complete photo → design → iterate → approve → shopping
│
│  EXIT: End-to-end flow works with real AI responses
│
├──────────────────────────────────────────────────────────────────
│
P3: Stabilization
│
│  ALL: Bug fixes from integration testing
│  ALL: Resume flow testing (kill app at every step, verify recovery)
│  ALL: Error state handling (network loss, model error, scan failure)
│  T1: Polish (loading states, animations, edge cases)
│  T0: Performance (image loading, polling intervals)
│  ALL: Demo prep
│
│  EXIT: Demo-ready
```

### What Can Start Immediately (No Dependencies)

| Team | Work | Why No Dependency |
|------|------|-------------------|
| T0 | Temporal server setup, docker-compose, project scaffold | Infrastructure, no code deps |
| T1 | Xcode project, SPM package structure | Project setup, no backend needed |
| T2 | Gemini API exploration / quality spike | Just API calls with test images |
| T3 | Claude system prompt iteration in a notebook | Just prompt engineering |

### What's Blocked On P0 Contracts

Everything else. Contracts are THE critical path item. T1/T2/T3 cannot write production activity or UI code until contracts exist.

### Integration Sequence (Incremental, Not Big-Bang)

Each integration step adds one batch. If a real activity breaks the workflow, immediately revert to the mock activity so other integration work continues.

### Key Milestones

| Milestone | Phase | Verification |
|-----------|-------|-------------|
| Contracts frozen | P0 exit | All Pydantic models importable; validation tests pass |
| Mock API operational | P0 exit | iOS app can create project → send signals → see state transitions |
| Gemini go/no-go decided | P0 exit | Documented with screenshots and scores |
| First real generated image | P1 (T2) | Real room photo → Gemini → photorealistic redesign in R2 |
| First real product match | P1 (T3) | Design image → Claude extraction → Exa search → scored product |
| First E2E with real AI | P2 Step 1 | Upload photos → real generated designs via workflow |
| Full E2E demo | P2 Step 3 | Photo → intake → design → iterate → approve → shopping list |

---

## 12. Success Metrics (Per Team, Independently Verifiable)

### T0: Platform — "Infrastructure Ready"

| Metric | Verification |
|--------|-------------|
| PostgreSQL has all tables | `alembic upgrade head` + `SELECT count(*) FROM projects` returns 0 |
| R2 bucket works | Upload + download test object succeeds |
| Temporal namespace operational | Start and query a test workflow succeeds |
| Health endpoint passes | `GET /health` returns 200 with all checks green |
| CI pipeline works | Push triggers lint + test; green check on GitHub |
| Mock API works | iOS can call all endpoints and get valid responses |
| All contracts importable | `from app.models.contracts import *` succeeds |
| Workflow handles all signals | Send signals in sequence; verify state transitions |
| Photo validation works | Reject blurry (threshold test), low-res, non-room, people/animals; pass valid |

### T1: iOS — "App Functional"

| Metric | Verification |
|--------|-------------|
| All screens navigable | Programmatic push to every ProjectStep succeeds |
| Photo upload works | Camera + gallery return photos; 2 required enforced |
| Chat renders correctly | Mock 3-question conversation displays properly |
| Design comparison works | 2 images swipeable; selection highlighting |
| Lasso MVP functional | Draw → editor → save → "Generate Revision" enabled |
| Shopping list renders | 8 mock products in 4 groups; total cost correct |
| Navigation restores | Build NavigationPath from any WorkflowState; correct screen shown |
| No memory leaks | Navigate full flow and back; Instruments shows no leaks |
| All views have previews | Every SwiftUI view has at least 1 working `#Preview` |

### T2: Image Generation — "Images Are Good"

| Metric | Verification |
|--------|-------------|
| generate_designs produces 2 images | Activity returns 2 DesignOption with valid R2 URLs |
| Images are photorealistic | Human eval: 7/10+ score for 70%+ of generations |
| Room architecture preserved | Edge map correlation > 0.7 with original photo |
| Inpainting respects mask | SSIM > 0.98 for non-masked regions |
| Regeneration incorporates feedback | Human eval: feedback addressed in 80%+ of cases |
| All activities complete in time | < 3 minutes per activity |
| Prompt templates exist | `prompts/` directory with all generation modes |

### T3: AI Agents — "Brief and Products Are Useful"

| Metric | Verification |
|--------|-------------|
| Quick Intake: valid brief | 100% valid DesignBrief JSON across 5 test conversations |
| Brief quality score | ≥ 70/100 on DesignBrief Quality Rubric across golden test suite |
| Adaptive skipping works | Multi-domain answer → agent correctly skips covered domains |
| Brief elevates user input | Style coherence ≥ 12/15 and material specificity ≥ 12/15 on rubric |
| Shopping: 5+ matched products | Test design image + brief + iteration history → 5+ items with confidence >= 0.5 |
| Brief-anchored items use user language | Search queries for brief-anchored items contain brief keywords |
| keep_items excluded | None of the keep_items appear in shopping extraction |
| Product URLs work | HTTP HEAD on product_url returns 200 for 90%+ |
| Rubric scoring calibrated | Category match contributes 0.3; material 0.2; etc. |
| Scoring discrimination | Score std dev > 0.15 across test set |
| End-to-end latency | Shopping list pipeline < 20s |

---

## 13. Code Quality Standards

### Testing Requirements

| Layer | Tests | Tools |
|-------|-------|-------|
| Pydantic models | Validation tests for all contracts | pytest |
| FastAPI endpoints | Integration tests (start workflow → signals → query) | pytest + httpx |
| Temporal workflow | Signal/query transitions + time-skip for timers | Temporal test environment |
| Activities (unit) | Real API calls with test inputs | pytest + real API keys |
| Activities (contract) | Output validates against Pydantic output model | pytest + Pydantic |
| iOS ViewModels | Logic tests for state management | XCTest |
| iOS Views | Preview-based visual verification | #Preview |
| iOS Navigation | Programmatic push to all steps | XCTest |
| iOS Lasso | Geometry math (intersection, overlap, area) | XCTest |
| Integration | Full flow through real Temporal + real activities | pytest (P2+) |

### Error Handling Patterns

**Backend activities**:
```python
# Retryable (Temporal retries automatically)
raise ApplicationError("Gemini rate limited", non_retryable=False)
# Non-retryable (report to user)
raise ApplicationError("Content policy violation", non_retryable=True)
```

**FastAPI gateway**:
- Query workflow state before signaling; reject signals to wrong step with 409
- Return consistent ErrorResponse for all error cases

**iOS**:
- Network errors → retry button, don't consume iteration count
- 4xx → show message from response body
- 5xx → generic "Something went wrong. Tap to retry."
- Polling timeout → "Still working..." with cancel option

### Abstraction Boundaries

```
API Layer (FastAPI)  →  Workflow Layer (Temporal)  →  Activity Layer
├── Request validation    ├── State management         ├── External API calls
├── R2 upload handling    ├── Signal handlers          ├── Image processing
├── Temporal client       ├── Activity dispatch        ├── R2 read/write
└── Response formatting   ├── Timer management         └── DB read/write
                          └── Error tracking

Rules:
- API layer NEVER calls external AI APIs (except sync photo validation)
- Workflow layer NEVER does I/O (no HTTP, no file reads)
- Activity layer is stateless (receives inputs, produces outputs)
- Contracts are the ONLY shared dependency between layers
```

### Logging

- **Backend**: Structured JSON with `structlog`. Every activity logs: activity_name, project_id, duration_ms, status, error.
- **Temporal**: Use Temporal Web UI for workflow visibility. Configure search attributes: project_id, current_step.
- **iOS**: Console.log for debug builds.

---

## 14. Risk Register (Updated)

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| 1 | Neither Gemini model passes mask precision threshold | High | P0 spike tests both models head-to-head; if neither passes, escalate to evaluate alternatives before P1 |
| 2 | Gemini preview model instability (rate limits, deprecation) | High | Both models use same API key/SDK; can swap model ID instantly if one becomes unstable |
| 3 | Lasso tool slips past P1 end | High | Mid-P1 go/no-go; rectangle fallback saves significant time |
| 4 | Contract change needed after freeze | Medium | Additive = fast merge; Breaking = formal process; T0 owns all changes |
| 5 | Integration reveals incompatibilities | Medium | Incremental integration (P2); stub fallback; contract tests |
| 6 | Exa returns irrelevant products | Medium | Multi-query strategy; Google Shopping fallback from P0 |
| 7 | Xcode project file merge conflicts | Medium | SPM local packages; single iOS project owner |
| 8 | Claude Opus 4.6 intake costs higher than estimated | Low | Monitor per-session costs; downgrade to Sonnet 4.5 if needed |
| 9 | RoomPlan struggles with unusual rooms | Low | LiDAR is optional; "without scan" path is fully specified |

---

## 15. Infrastructure Costs (Monthly)

| Scale | Projects/mo | AI Costs | Infra (fixed) | Total |
|-------|-------------|----------|---------------|-------|
| **Hackathon** | 50 | $57 | $15 | **~$72** |
| **Soft launch** | 500 | $565 | $25 | **~$590** |
| **Growth** | 2,000 | $2,260 | $50 | **~$2,310** |

Fixed infra: Railway ($10-20 for 2 services + $5-10 for PG), Temporal Cloud ($100/mo Essentials after $1K credits), R2 (free).

---

## 16. Open Questions

| # | Question | Decision Needed By |
|---|----------|-------------------|
| 1 | Gemini 3 Pro mask quality — pass or pivot to Gemini 2.5 Flash? | P0 end |
| 2 | RoomPlan data: extract JSON on-device or send USDZ? | P0 end |
| 3 | Exa search quality: test 20+ furniture queries | Mid-P1 |
| 4 | Claude Opus 4.6 intake cost in practice vs estimate | Mid-P1 |

---

*End of plan.*
