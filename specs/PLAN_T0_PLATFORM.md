# T0: Platform & Backend Services — Implementation Sub-Plan

> **Extracted from**: `specs/PLAN_FINAL.md` v2.0
> **Date**: 2026-02-10
> **Team**: T0 — Platform & Backend Services
---

## 1. Big Picture

**Remo** is an AI-powered room redesign app: users photograph their room, describe their style, and receive photorealistic redesign options they can iteratively refine, culminating in a downloadable design image and a shoppable product list with real purchase links.

### System Architecture

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

### Where T0 Fits

**T0 is the foundation team.** You own infrastructure, contracts, workflow, API, and later lead integration. Every other team depends on your P0 outputs. Contracts frozen at P0 exit gate are THE critical path item — T1/T2/T3 cannot write production activity or UI code until contracts exist.

### 4-Team Structure

| Team | Focus |
|------|-------|
| **T0: Platform & Backend** | Contracts, Temporal, API, Photo validation, LiDAR, DB, R2, CI/CD, Integration lead |
| **T1: iOS App** | All SwiftUI/UIKit work |
| **T2: Image Generation** | Gemini gen/inpaint/regen pipeline |
| **T3: AI Agents** | Intake chat + Shopping list |

### Phase Overview

| Phase | Focus | Gate to Exit |
|-------|-------|-------------|
| **P0: Foundation** | Contracts, scaffold, infra, Gemini spike | Contracts frozen + mock API works + Gemini go/no-go decided |
| **P1: Independent Build** | All teams build in parallel against contracts | Each team's deliverables pass their own tests |
| **P2: Integration** | Wire real activities, connect iOS to real API | End-to-end flow works with real AI |
| **P3: Stabilization** | Bugs, edge cases, resume testing, polish | Demo-ready |

---

## 2. Your Team

- **Worktree**: Main repo at `/Hanalei/remo`
- **Branch prefix**: `team/platform/*`

You work from the main repo. Other teams use git worktrees:
```bash
# T1: git worktree add /Hanalei/remo-ios team/ios/scaffold
# T2: git worktree add /Hanalei/remo-gen team/gen/gemini-spike
# T3: git worktree add /Hanalei/remo-ai team/ai/intake-agent
```

---

## 3. What You Own

### Files & Directories (Exclusive Ownership)

```
remo/
  backend/
    app/
      models/
        contracts.py       # ALL Pydantic contract models
        db.py              # SQLAlchemy ORM models
      api/
        routes/            # All FastAPI endpoints
      workflows/
        design_project.py  # Temporal workflow definition
      activities/
        validation.py      # Photo validation activity
        purge.py           # Purge project data activity
      utils/
        r2.py              # R2 client wrapper
        image.py           # Shared with T2 — mask rendering, image processing
    migrations/            # Alembic migrations (exclusive)
    tests/
    pyproject.toml
    Dockerfile
  docker-compose.yml       # Local dev stack
  .github/workflows/       # CI/CD pipeline (exclusive)
  specs/                   # Shared documentation
```

### Exclusive Ownership Rules

- **Contracts** (`contracts.py`): Only T0 creates, modifies, or merges changes. Other teams propose changes; T0 reviews and implements.
- **DB migrations** (`migrations/`): Only T0 creates migrations. Other teams request schema changes via T0.
- **CI/CD** (`.github/workflows/`): Only T0 modifies. Changes are rare after initial setup.

### Shared Files

- `backend/app/utils/image.py` — shared with T2 (mask rendering, image processing)

### Files T0 Does NOT Own

- `backend/app/activities/generate.py` — T2
- `backend/app/activities/inpaint.py` — T2
- `backend/app/activities/regen.py` — T2
- `backend/app/activities/intake.py` — T3
- `backend/app/activities/shopping.py` — T3
- `backend/app/prompts/` — T2/T3
- `ios/` — T1

---

## 4. Deliverables by Phase

### P0: Foundation (Critical — Blocks Everyone)

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 1 | Temporal Cloud namespace operational | `tctl namespace describe remo-dev` succeeds; all teams can connect |
| 2 | `docker-compose.yml` for local dev | `docker compose up` gives a working stack in <5 min | **DONE** — scaffold + docker-compose + 5 tests |
| 3 | All Pydantic contract models (`backend/models/contracts.py`) | All `*Input/*Output` models importable; validation tests pass | **DONE** — 37 models, 41 validation tests |
| 4 | Database schema (Alembic migration) | `alembic upgrade head` creates all tables; CASCADE verified | **DONE** — 9 tables, 21 DB model tests, initial Alembic migration (001_initial_schema.py), 10 migration tests |
| 5 | R2 bucket + pre-signed URL generation | Upload/download test object succeeds | **DONE** — r2.py client (upload, presigned URL, head, delete, prefix delete), 14 tests pass |
| 6 | FastAPI gateway (all endpoints, stub responses) | All 13 endpoints return correct status codes and response shapes | **DONE** — 17 endpoints, 20 API tests, 87 total tests pass |
| 7 | `DesignProjectWorkflow` skeleton (signals, queries, mock activities) | Workflow transitions through all steps with test signals | **DONE** — 12 signals, 1 query, 5 mock activities, 13 workflow tests, 100 total tests pass |
| 8 | Mock API operational for iOS team | iOS app can create project → query state → send signals → see transitions | **DONE** — satisfied by P0 #6 (17 mock endpoints) + P0 #7 (workflow with all signals/queries); happy-path e2e test proves full flow |
| 9 | Swift API models (mirrors Pydantic) | All models decode mock JSON responses without errors |
| 10 | CI pipeline (ruff + mypy + pytest) | Green on every PR to main | **DONE** — `.github/workflows/ci.yml`, mypy config with per-module overrides for Temporal SDK false positives |

**P0 EXIT GATES** (must all be true):
- Item 3: Contracts frozen (hard freeze)
- Item 8: Mock API operational
- T2 has made Gemini go/no-go decision (T2 does this, but T0 needs to know the result)

### P1: Independent Build

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 11 | Photo validation activity (blur + resolution + Claude Haiku 4.5) | Correctly rejects blurry/low-res/non-room images; passes valid ones | **DONE** — `activities/validation.py` (156 lines), 25 tests |
| 12 | LiDAR dimension parser | Parses RoomPlan JSON into RoomDimensions model | **DONE** — `utils/lidar.py` (76 lines), 19 tests |

### P2: Integration (T0 Leads)

| # | Deliverable | Success Metric |
|---|------------|----------------|
| 13 | Wire real activities into workflow | Each activity produces real results through the workflow |

### P3: Stabilization

- Bug fixes from integration testing
- Performance tuning (image loading, polling intervals)
- Resume flow testing support

---

## 5. P0 Exit Gate (Critical)

Before P1 can start, ALL of these must be true:

1. **Contracts frozen**: All Pydantic models in `contracts.py` importable; validation tests pass. After freeze, only additive (new optional fields) changes are fast-tracked. Breaking changes require formal process.

2. **Mock API operational**: iOS app (T1) can:
   - `POST /api/v1/projects` → get a project_id
   - `GET /api/v1/projects/{id}` → see WorkflowState
   - Send all signals → see state transitions
   - The mock API returns realistic response shapes

3. **Gemini model selected** (T2 does this): T2 runs a quality spike testing both Gemini 3 Pro Image and Gemini 2.5 Flash Image head-to-head on identical test cases. T2 picks the winner. If neither passes, T2 escalates. T0 needs to know the decision but doesn't do the evaluation.

---

## 6. Technical Details

### Temporal Workflow Design

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
1. `asyncio.sleep` -> `workflow.sleep` for 24h purge timer
2. Added `scan_skipped` and `intake_skipped` initialization
3. Added 48h abandonment timeout at every wait point via `_wait_with_abandonment`
4. Added `start_over` signal (required by product spec Section 4.6)
5. Added `cancel_project` signal
6. Separated `generate_inpaint` and `generate_regen` activities (different input contracts)
7. Used queue pattern for edit actions (prevents race condition)
8. Added error state tracking and `retry_failed_step` signal
9. Added `WorkflowError` to query response

### Database Schema

**Design principle**: The database stores **data artifacts only**. Workflow state (step, iteration_count, approved status) lives exclusively in Temporal. The database does NOT duplicate Temporal state.

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

#### Required Indexes

```sql
CREATE INDEX idx_photos_project_type ON photos(project_id, type);
CREATE INDEX idx_generated_images_project ON generated_images(project_id, type);
CREATE INDEX idx_revisions_project ON revisions(project_id, revision_number);
CREATE INDEX idx_product_matches_list ON product_matches(shopping_list_id);
```

#### Key Design Decisions

- **Integer cents for all monetary values** — avoids floating-point money issues
- **All FKs use `ON DELETE CASCADE`** — purge activity deletes from `projects`, children cascade
- **Schema frozen in Phase 0** — only T0 creates migrations; other teams request changes via T0
- **JSONB for semi-structured data** — write-once/read-many blobs that don't need relational querying

### R2 Storage Layout

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

**Upload flow**: iOS -> FastAPI handler -> R2 (server-side upload). Keeps R2 credentials server-side.
**Download flow**: FastAPI generates 1-hour pre-signed GET URLs. No CDN for MVP.

### FastAPI API Endpoints

| Method | Endpoint | Action | Response |
|--------|----------|--------|----------|
| `POST` | `/api/v1/projects` | Start workflow | `{ project_id }` |
| `GET` | `/api/v1/projects/{id}` | Query state | Full `WorkflowState` |
| `DELETE` | `/api/v1/projects/{id}` | Cancel + purge | 204 |
| `POST` | `/api/v1/projects/{id}/photos` | Upload -> validate -> signal | `{ photo_id, validation }` |
| `POST` | `/api/v1/projects/{id}/scan` | Upload -> signal | 200 |
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

For MVP, the iOS app handles long-running activities via polling:

1. Send the signal (e.g., `select_option`) which triggers the activity
2. Poll `GET /projects/{id}` every 2-3 seconds
3. When `step` changes or `current_image` / `shopping_list` is populated -> activity completed
4. If `error` is populated -> show retry UI

**SSE is a post-MVP enhancement.** Polling is simpler, works through all CDNs/proxies, and eliminates the need for Redis.

### Photo Validation (Synchronous in Handler)

```
POST /projects/{id}/photos
  -> Upload to R2
  -> Blur check (Laplacian variance on normalized 1024px image, threshold ~60-80) — <50ms
  -> Resolution check (Pillow, min 1024px shortest side) — <10ms
  -> Content classification (Claude Haiku 4.5, image input) — ~1-2s
  -> Return { photo_id, validation: { passed, failures[] } }
  -> If passed: signal Temporal workflow
```

Blur threshold should be calibrated with 20+ real room photos at normalized resolution. Start with 60 and adjust.

**Key architectural decision**: Photo validation runs synchronously in the FastAPI handler (not as a Temporal activity) because it's fast (<3s) and needs immediate user feedback.

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

---

## 7. Contract Models (You Own These)

These MUST exist before parallel work begins. T0 owns all of them. They are frozen at P0 exit gate.

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
    progress: str | None = None          # "Question 2 of 3"
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

## 8. Git & Collaboration

### PR Merge Order (T0's PRs are 1-5, then 7 for integration)

```
1. team/platform/scaffold        → main (P0 start) — project structure, deps
2. team/platform/contracts       → main (P0 mid)   — Pydantic models
3. team/platform/temporal        → main (P0 end)   — workflow + mock activities
4. team/platform/api-gateway     → main (P0 end)   — all endpoints
5. team/platform/swift-models    → main (P0 end)   — Swift mirrors of contracts
   ──── All teams can work independently after this point (P1) ────
6. Activity PRs (any order, during P1) — owned by T1/T2/T3:
   - team/gen/generate-designs   → main
   - team/gen/inpaint           → main
   - team/gen/regen             → main
   - team/ai/intake-quick       → main
   - team/ai/shopping-pipeline  → main
   - team/platform/validation   → main  ← T0 owns this one
7. team/platform/integration-*   → main (P2) — wire activities into workflow
8. team/ios/integration          → main (P2) — swap mock for real API (T1 owns)
```

### PR Standards

- **Size**: 200-400 lines preferred; single-purpose
- **Review**: T0 contract PRs -> reviewed by 1 person from each consuming team. Activity PRs -> reviewed by T0 for contract compliance. Bug fixes -> self-merge OK if tests pass.
- **Merge strategy**: Squash merge to main (clean linear history)
- **Branch protection**: `main` requires 1 approval + passing CI. Team branches: no protection.

### How T0 Reviews Other Teams' PRs

T0 reviews all activity PRs for **contract compliance**:
- Activity inputs/outputs match the frozen Pydantic contracts exactly
- Activity raises `ApplicationError` correctly (retryable vs non-retryable)
- Activity is stateless (receives inputs, produces outputs, no side-channel state)
- R2 storage keys follow the layout convention (`/projects/{project_id}/...`)

---

## 9. Integration Lead Role (P2)

T0 leads integration in P2. The strategy is **incremental, not big-bang** — wire one activity at a time.

### Integration Sequence (3 Steps)

**Step 1**: Wire `generate_designs` into workflow (lowest risk)
- T1 points iOS at real API
- TEST: Upload photos -> get 2 real generated designs

**Step 2**: Wire `intake_chat` + `generate_inpaint` + `generate_regen`
- T1 tests real intake + lasso -> inpaint flow
- T1 builds multi-region lasso
- TEST: Full flow through iteration with real AI

**Step 3**: Wire shopping list pipeline + Temporal timers
- T1 connects shopping list UI to real data
- T3 adds Full Intake + Open Conversation modes
- TEST: Complete photo -> design -> iterate -> approve -> shopping

### Revert-to-Mock Strategy

If a real activity breaks the workflow during integration, **immediately revert to the mock activity** so other integration work continues. The mock activity file stays in the codebase until P2 is complete.

---

## 10. Success Metrics

All T0 success metrics from the master plan:

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

---

## 11. Code Quality

### Testing Requirements (T0-Relevant)

| Layer | Tests | Tools |
|-------|-------|-------|
| Pydantic models | Validation tests for all contracts | pytest |
| FastAPI endpoints | Integration tests (start workflow -> signals -> query) | pytest + httpx |
| Temporal workflow | Signal/query transitions + time-skip for timers | Temporal test environment |
| Activities (contract) | Output validates against Pydantic output model | pytest + Pydantic |
| Integration | Full flow through real Temporal + real activities | pytest (P2+) |

### Error Handling Patterns for Backend

**Activities**:
```python
# Retryable (Temporal retries automatically)
raise ApplicationError("Gemini rate limited", non_retryable=False)
# Non-retryable (report to user)
raise ApplicationError("Content policy violation", non_retryable=True)
```

**FastAPI gateway**:
- Query workflow state before signaling; reject signals to wrong step with 409
- Return consistent `ErrorResponse` for all error cases

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

### Logging Standards

- **Backend**: Structured JSON with `structlog`. Every activity logs: `activity_name`, `project_id`, `duration_ms`, `status`, `error`.
- **Temporal**: Use Temporal Web UI for workflow visibility. Configure search attributes: `project_id`, `current_step`.

---

## 12. Risks & Open Questions

### Risks (T0-Relevant)

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| 4 | Contract change needed after freeze | Medium | Additive = fast merge; Breaking = formal process; T0 owns all changes |
| 5 | Integration reveals incompatibilities | Medium | Incremental integration (P2); stub fallback; contract tests |
| 7 | Xcode project file merge conflicts | Medium | SPM local packages; single iOS project owner |

### Open Questions (T0-Relevant)

| # | Question | Decision Needed By |
|---|----------|-------------------|
| 1 | RoomPlan data: extract JSON on-device or send USDZ? | P0 end |

### Dependencies on Other Teams

- **T2** decides Gemini go/no-go at P0 end — T0 needs to know result but doesn't do the evaluation
- **T1** consumes Swift API models and mock API — T0 must deliver these before P1
- **T2/T3** deliver activity implementations during P1 — T0 wires them in during P2

---

*For full context, see the master plan at `specs/PLAN_FINAL.md`.*
