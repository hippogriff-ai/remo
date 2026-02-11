# T1: iOS App — Implementation Sub-Plan

> **Extracted from**: `specs/PLAN_FINAL.md` v2.0
> **Date**: 2026-02-10
> **Team**: T1 (iOS App)

---

## 1. Big Picture

Remo is an AI-powered room redesign app: users photograph their room, describe their style, and receive photorealistic redesign options they can iteratively refine, culminating in a downloadable design image and a shoppable product list with real purchase links.

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
                    │   ├── edit_design        │ → Gemini 3 Pro Image (multi-turn chat)
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

### Where T1 Fits

T1 builds the **entire iOS user experience**. You consume T0's contracts and mock API, build all screens against mocks in P1, then swap to the real API in P2. T1 does NOT own any backend code, contracts, or AI pipeline logic.

### 4-Team Structure

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
  └──────────────┘  │                  │  └────────────────┘
                    └──────────────────┘
```

### Phase Overview

| Phase | Focus | T1 Role |
|-------|-------|---------|
| **P0: Foundation** | Contracts, scaffold, infra, Gemini spike | Xcode project + SPM packages + navigation skeleton + mock client |
| **P1: Independent Build** | All teams build in parallel against contracts | Build ALL UI screens against mock API |
| **P2: Integration** | Wire real activities, connect iOS to real API | Swap mock for real API, polish annotation tool |
| **P3: Stabilization** | Bugs, edge cases, resume testing, polish | Polish, loading states, animations, error edge cases |

---

## 2. Your Team

- **Worktree**: `/Hanalei/remo-ios`
- **Branch prefix**: `team/ios/*`
- **Setup command**:
  ```bash
  git worktree add /Hanalei/remo-ios team/ios/scaffold
  ```

---

## 3. What You Own

T1 owns the entire `ios/` directory:

```
ios/
  Remo.xcodeproj       # Thin shell, imports SPM packages
  Packages/
    RemoModels/         # Shared models + protocols
    RemoNetworking/     # API client, mock client
    RemoPhotoUpload/    # Photo upload UI + validation display
    RemoChatUI/         # Chat interface, quick-reply chips
    RemoAnnotation/     # Annotation region picker, region editor
    RemoDesignViews/    # Comparison, iteration, approval, output
    RemoShoppingList/   # Product cards, grouped lists
    RemoLiDAR/          # RoomPlan wrapper, scan screens
```

**T1 does NOT own**:
- `backend/models/contracts.py` (Pydantic models) — T0 owns
- Any backend code — T0/T2/T3 own
- CI/CD configuration — T0 owns

T1 **does** own the Swift mirrors of the Pydantic contracts (in `Packages/RemoModels/`), but these must match T0's published Pydantic models exactly.

---

## 4. Deliverables by Phase

### P0: Foundation

| Deliverable | Success Metric |
|------------|----------------|
| Xcode project + local SPM package structure | All packages build; empty placeholder screens for every step |
| `WorkflowClientProtocol` + `MockWorkflowClient` | Mock client simulates step transitions with realistic delays |

### P1: Independent Build

| Deliverable | Success Metric |
|------------|----------------|
| Photo Upload UI (camera + gallery + validation feedback) | 2 room photos required; 3 inspiration optional; validation messages shown |
| Chat Interface (bubbles, quick-reply chips, text input) | Mock conversation renders correctly; progress indicator works |
| Design Comparison (swipeable + side-by-side toggle) | 2 mock images swipeable; selection highlighting works |
| Output Screen (save to photos, share) | Image saves to camera roll; share sheet opens |
| Home Screen (pending projects, resume) | Mock projects list renders; tap resumes at correct step |
| Navigation + Router (full flow) | Push to any step via WorkflowState; back navigation works |
| Annotation Tool (tap to place circles, instruction editor) | Place region -> edit instruction -> "Generate Revision" enabled |
| Shopping List UI (grouped cards, buy links, fit badges) | 8 mock products render in 4 groups; total cost displayed |
| LiDAR Scan UI (RoomPlan wrapper, skip flow) | Device check works; skip flow shows trade-off notification |

### P2: Integration

| Deliverable | Success Metric |
|------------|----------------|
| Swap mock API for real API | Full flow works against real backend |
| Annotation tool polish (undo, snap guides, haptics) | Undo works; size snap guides visible; haptic on placement |

### P3: Stabilization

| Deliverable | Success Metric |
|------------|----------------|
| Resume flow testing | Kill app at every step, verify recovery |
| Error state handling | Network loss, model error, scan failure all handled |
| Polish | Loading states, animations, edge cases |

---

## 5. Dependencies on T0

### What T1 Needs Before P1 Can Start

| Artifact | Description | Delivered By |
|----------|-------------|-------------|
| Pydantic contract models | `backend/models/contracts.py` — frozen at P0 exit gate | T0, P0 |
| Mock API operational | iOS app can create project -> query state -> send signals -> see transitions | T0, P0 |
| Swift API models | Swift mirrors of all Pydantic models that decode mock JSON without errors | T0, P0 |

All three are **P0 exit gates**. T1 cannot begin production UI work until these exist.

### Polling Pattern for Async Operations

The iOS app handles long-running activities (generation, shopping list) via **polling, not SSE**:

1. Send the signal (e.g., `select_option`) which triggers the activity
2. Poll `GET /projects/{id}` every 2-3 seconds
3. When `step` changes or `current_image` / `shopping_list` is populated -> activity completed
4. If `error` is populated -> show retry UI

SSE is a post-MVP enhancement. Polling is simpler, works through all CDNs/proxies, and eliminates the need for Redis.

---

## 6. Technical Details

### Navigation Architecture

#### `NavigationStack` + `ProjectStep` Enum

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

#### `ProjectState` Observable

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

Generation status is tracked **separately** from navigation step. This allows showing a loading spinner on the generation screen while the navigation step doesn't change.

### SPM Package Structure

```
Remo/
  Remo.xcodeproj (thin shell, imports packages)
  Packages/
    RemoModels/         # Shared models + protocols (T1-lead owns)
    RemoNetworking/     # API client, mock client (T1-lead owns)
    RemoPhotoUpload/    # Photo upload UI + validation display
    RemoChatUI/         # Chat interface, quick-reply chips
    RemoAnnotation/     # Annotation region picker, region editor
    RemoDesignViews/    # Comparison, iteration, approval, output
    RemoShoppingList/   # Product cards, grouped lists
    RemoLiDAR/          # RoomPlan wrapper, scan screens
```

Each package has its own `Package.swift` -- no `.pbxproj` conflicts between packages. This is the single most impactful decision for parallel iOS development.

If two developers work on T1, one can own photo/chat/LiDAR packages while the other owns design/annotation/shopping packages. The Xcode project file rarely changes.

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

In P2, `MockWorkflowClient` is swapped for `RealWorkflowClient` — the views and view models don't change.

### Polling Strategy

How the iOS app handles long-running activities:

1. **User triggers action** (e.g., selects a design option)
2. **iOS sends signal** via `POST /projects/{id}/select`
3. **iOS starts polling** `GET /projects/{id}` every 2-3 seconds
4. **UI shows loading** (spinner, progress indicator, "Generating your designs...")
5. **Poll checks**:
   - `step` changed -> activity completed, navigate to next screen
   - `current_image` or `shopping_list` is populated -> data ready
   - `error` is populated -> show retry UI with "Tap to retry" button
6. **Stop polling** when step changes or error is detected

Implement polling as a reusable utility (e.g., `AsyncPollingSequence` or a simple `Task` with `Task.sleep`). Cancel polling when the view disappears.

### All UI Screens (P1 Deliverables)

#### Photo Upload UI
- Camera capture via `UIImagePickerController` bridge
- Photo library picker
- 2 room photos **required** before proceeding
- Up to 3 inspiration photos **optional**
- Validation feedback displayed per-photo (blur, resolution, content issues)
- Photo thumbnails with delete/retake actions

#### Chat Interface
- Bubble-style messages (user right, assistant left)
- Quick-reply chips (numbered options)
- Free-text input field
- Progress indicator ("3 of 10 domains covered")
- Summary card when intake completes
- Handles `is_open_ended` flag for text input vs chips

#### Design Comparison
- Swipeable horizontal view (2 options)
- Side-by-side toggle mode
- Selection highlighting (border/checkmark)
- "Start Over" button -> resets to intake
- Loading state while generation runs

#### Annotation Tool (Circle-Based Region Marking)
- Tap to place circle region on design image
- Drag to adjust position, pinch to resize radius
- Up to 3 numbered regions (colored: red #FF0000, blue #0000FF, green #00FF00)
- Each region has a text instruction (min 10 chars)
- Region editor: tap region badge to edit instruction
- "Generate Revision" button (enabled when at least 1 region + instruction saved)
- Also supports pure text feedback (no annotations needed)

#### Shopping List UI
- Products grouped by category (e.g., "Sofas", "Lighting", "Rugs", "Decor")
- Product cards with: image, name, retailer, price, confidence badge
- Fit badges: "fits" / "tight" (if LiDAR data available)
- Confidence labels: normal (>=0.8), "Close match" (0.5-0.79)
- Total estimated cost displayed
- "Buy" link opens retailer URL in Safari
- Google Shopping fallback link for unmatched items

#### LiDAR Scan UI
- Device capability check (`ARWorldTrackingConfiguration.supportsSceneReconstruction`)
- `RoomCaptureView` (RoomPlan framework) wrapper
- Skip flow: "Skip Scan" button with trade-off notification ("Furniture sizing won't be available")
- Scan progress indicator
- Scan result preview

#### Output Screen
- Final design image (zoomable via `UIScrollView` bridge)
- "Save to Photos" button (writes to camera roll)
- Share sheet (`UIActivityViewController` bridge)
- "View Shopping List" button
- "Start New Project" button

#### Home Screen
- List of pending/completed projects
- Project cards with: thumbnail, status, last updated
- Tap to resume at correct step
- "New Project" button

#### Navigation + Router
- `NavigationStack` with path driven by `ProjectStep` enum
- Router maps `WorkflowState.step` to correct `ProjectStep`
- Back navigation works (but some steps are one-way, e.g., can't go back from generation)
- Deep resume: build `NavigationPath` from any `WorkflowState`

### Annotation Tool Details

**Key decision**: Replaces freehand lasso with numbered circle annotations. Simpler to build (no polygon geometry, no self-intersection detection), aligns with Gemini's intended interaction pattern.

#### P1: Annotation MVP

- Tap to place circle region on design image (normalized 0-1 coordinates)
- Drag to adjust position, pinch to resize radius
- Up to 3 numbered regions with distinct colors (red, blue, green)
- Region editor: tap badge to edit instruction text
- "Generate Revision" button sends `AnnotationRegion` list to backend
- Also supports pure text feedback mode (no visual annotations)
- Region data model: `AnnotationRegion(region_id, center_x, center_y, radius, instruction)`

#### P2: Polish

- Undo last region
- Region size snap guides
- Haptic feedback on placement
- Animation on region placement

---

## 7. Contracts You Consume

These are the Pydantic contract models from T0's `backend/models/contracts.py`. T1 must create Swift mirrors of all these types. The shapes below are the canonical source of truth.

### Shared Types

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

class AnnotationRegion(BaseModel):
    region_id: int                       # 1-3
    center_x: float                      # normalized 0-1
    center_y: float                      # normalized 0-1
    radius: float                        # normalized 0-1
    instruction: str                     # min 10 chars

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
    type: str                            # "annotation" or "feedback"
    base_image_url: str
    revised_image_url: str
    instructions: list[str] = []         # annotation instructions or text feedback
```

### Activity Input/Output Models

```python
class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None

class GenerateDesignsOutput(BaseModel):
    options: list[DesignOption]          # exactly 2

class EditDesignInput(BaseModel):
    project_id: str
    base_image_url: str
    room_photo_urls: list[str] = []
    inspiration_photo_urls: list[str] = []
    design_brief: DesignBrief | None = None
    annotations: list[AnnotationRegion] = []  # empty for text-only feedback
    feedback: str | None = None               # text feedback (optional)
    chat_history_key: str | None = None       # None = first edit (bootstraps chat)

class EditDesignOutput(BaseModel):
    revised_image_url: str
    chat_history_key: str                     # R2 key for serialized Gemini chat

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
```

### WorkflowState (Drives Navigation)

This is the most important model for T1. Polling `GET /projects/{id}` returns this. Your navigation router maps `step` to a `ProjectStep`, and all UI data comes from the other fields.

```python
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
    chat_history_key: str | None = None      # R2 key for Gemini chat session
    shopping_list: GenerateShoppingListOutput | None = None
    approved: bool = False
    error: WorkflowError | None = None
```

**Step values** (from the Temporal workflow): `"photos"`, `"scan"`, `"intake"`, `"generation"`, `"selection"`, `"iteration"`, `"approval"`, `"shopping"`, `"completed"`.

---

## 8. API Endpoints You Call

### Full Endpoint Table

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
| `POST` | `/api/v1/projects/{id}/iterate/annotate` | Signal submit_annotation_edit | 200 |
| `POST` | `/api/v1/projects/{id}/iterate/feedback` | Signal submit_text_feedback | 200 |
| `POST` | `/api/v1/projects/{id}/approve` | Signal approve_design | 200 |
| `POST` | `/api/v1/projects/{id}/retry` | Signal retry_failed_step | 200 |
| `GET` | `/health` | Health check | `{ postgres, temporal, r2 }` |

### Error Response Model

All API errors return a consistent shape:

```python
class ErrorResponse(BaseModel):
    error: str           # machine-readable code (e.g., "workflow_not_found")
    message: str         # human-readable message
    retryable: bool
    detail: str | None = None
```

**Status codes**:
- `400` -- Invalid input (Pydantic validation failure)
- `404` -- Project/workflow not found (purged or never existed)
- `409` -- Conflict (signal sent to wrong workflow step)
- `429` -- Rate limited (`Retry-After` header)
- `500` -- Unexpected server error
- `502` -- Upstream API error (Gemini, Claude, Exa down)

### Photo Validation Flow

Photo validation is **synchronous** in the API handler (not a Temporal activity):

```
POST /projects/{id}/photos
  → Upload to R2
  → Blur check (Laplacian variance, threshold ~60-80) — <50ms
  → Resolution check (min 1024px shortest side) — <10ms
  → Content classification (Claude Haiku 4.5, image input) — ~1-2s
  → Return { photo_id, validation: { passed, failures[] } }
  → If passed: signal Temporal workflow
```

T1 receives the `ValidatePhotoOutput` response immediately and displays validation results to the user. If validation fails, the user can retake/replace the photo.

---

## 9. Git & Collaboration

### Worktree Setup

```bash
# From the main remo repo
git worktree add /Hanalei/remo-ios team/ios/scaffold
```

This creates a separate working directory at `/Hanalei/remo-ios` so T1 can work without interfering with T0's work in `/Hanalei/remo`.

### Branch Naming

```
team/ios/scaffold         # P0: Xcode project + SPM packages
team/ios/photo-upload     # P1: Photo upload UI
team/ios/chat-ui          # P1: Chat interface
team/ios/design-views     # P1: Design comparison + output
team/ios/annotation       # P1: Annotation tool
team/ios/shopping-ui      # P1: Shopping list UI
team/ios/lidar-scan       # P1: LiDAR scan UI
team/ios/navigation       # P1: Navigation + router
team/ios/integration      # P2: Swap mock for real API
team/ios/annotation-polish # P2: Annotation tool polish
```

### PR Merge Order

T1's PRs merge in this order:

1. `team/ios/scaffold` -> main (P0) -- Xcode project + SPM packages + navigation skeleton
2. UI feature PRs (P1, any order) -- each screen is an independent package
3. `team/ios/integration` -> main (P2) -- swap mock for real API
4. `team/ios/annotation-polish` -> main (P2) -- annotation tool polish

### Reviewing T0 Contract PRs

T1 should review T0's contract PRs during P0:
- `team/platform/contracts` -- verify Pydantic models match iOS needs
- `team/platform/swift-models` -- verify Swift mirrors decode correctly

One person from T1 should approve these PRs before merge.

---

## 10. Success Metrics

| Metric | Verification |
|--------|-------------|
| All screens navigable | Programmatic push to every ProjectStep succeeds |
| Photo upload works | Camera + gallery return photos; 2 required enforced |
| Chat renders correctly | Mock 3-question conversation displays properly |
| Design comparison works | 2 images swipeable; selection highlighting |
| Annotation tool functional | Place circle -> edit instruction -> "Generate Revision" enabled |
| Shopping list renders | 8 mock products in 4 groups; total cost correct |
| Navigation restores | Build NavigationPath from any WorkflowState; correct screen shown |
| No memory leaks | Navigate full flow and back; Instruments shows no leaks |
| All views have previews | Every SwiftUI view has at least 1 working `#Preview` |

---

## 11. Code Quality

### Testing Requirements

| Area | Tests | Tool |
|------|-------|------|
| iOS ViewModels | Logic tests for state management | XCTest |
| iOS Views | Preview-based visual verification | `#Preview` |
| iOS Navigation | Programmatic push to all steps | XCTest |
| iOS Annotation | Region placement, coordinate normalization | XCTest |

### Preview-Driven Development

Every SwiftUI view must have at least one working `#Preview`. Use `MockWorkflowClient` in all previews:

```swift
#Preview {
    IterationScreen(client: MockWorkflowClient())
}
```

This serves as both documentation and a manual visual test.

### Error Handling Patterns

- **Network errors** -> retry button, don't consume iteration count
- **4xx responses** -> show message from response body (`ErrorResponse.message`)
- **5xx responses** -> generic "Something went wrong. Tap to retry."
- **Polling timeout** -> "Still working..." with cancel option
- **Workflow error** (`WorkflowState.error` populated) -> show retry UI based on `retryable` flag

### Memory Leak Verification

Navigate the full flow (home -> photo -> scan -> intake -> generation -> selection -> iteration -> approval -> shopping -> output) and back. Run Instruments (Leaks template) to verify no leaks. Pay special attention to:
- Polling `Task` cancellation when views disappear
- `UIImagePickerController` / `RoomCaptureView` bridge cleanup
- Image caching (don't hold full-resolution images in memory unnecessarily)

---

## 12. Risks & Open Questions

### Risks Relevant to T1

| # | Risk | Severity | Mitigation |
|---|------|----------|-----------|
| 1 | Annotation tool interaction feels awkward on small screens | Medium | Test on iPhone SE early; adjust minimum region size if needed |
| 2 | Xcode project file merge conflicts | Medium | SPM local packages; single iOS project owner |
| 3 | RoomPlan struggles with unusual rooms | Low | LiDAR is optional; "without scan" path is fully specified |

### Open Questions

| # | Question | Decision Needed By |
|---|----------|-------------------|
| 1 | RoomPlan data: extract JSON on-device or send USDZ? | P0 end |
| 2 | Annotation circle sizing UX — pinch vs. drag handle? | Early P1 (test both in prototype) |

---

*For full context, see the master plan at `specs/PLAN_FINAL.md`.*
