# Remo iOS Frontend Architecture Analysis

> **Prepared for**: Hackathon MVP planning
> **Date**: 2026-02-10
> **Scope**: iOS native app — iPhone Pro / iPad Pro (full), non-Pro (degraded)
> **Key architectural dependency**: Temporal for workflow orchestration and durable execution

---

## 1. UI Framework: SwiftUI vs UIKit

### Recommendation: SwiftUI-primary with targeted UIKit bridges

For a hackathon MVP, SwiftUI is the correct choice as the primary framework. However, several features in this spec require UIKit or lower-level frameworks, so the architecture must plan for clean bridging from the start.

### Trade-off Analysis

| Criterion | SwiftUI | UIKit | Verdict |
|-----------|---------|-------|---------|
| **Development speed** | Faster for standard screens (home, lists, forms, chat) | Slower boilerplate | SwiftUI wins |
| **Camera integration** | Requires `UIImagePickerController` or `AVCaptureSession` via `UIViewControllerRepresentable` | Native | Bridged — either way |
| **LiDAR / RoomPlan** | `RoomCaptureView` has a SwiftUI wrapper (`RoomCaptureViewRepresentable` pattern) | Native UIKit view | Bridged — either way |
| **Chat UI** | Doable with `ScrollViewReader` + `LazyVStack`; can be tricky with keyboard avoidance | `UICollectionView` with compositional layout is battle-tested | UIKit slightly better for edge cases, but SwiftUI is adequate for MVP |
| **Lasso annotation (freehand drawing)** | `Canvas` view (iOS 15+) supports custom drawing with excellent performance | Custom `UIView` with `CAShapeLayer` / Core Graphics | **SwiftUI Canvas is sufficient** for this use case — it supports touch drawing, path rendering, and compositing over images |
| **Side-by-side / swipeable comparison** | `TabView` with `.page` style for swipe; `HStack` for side-by-side | `UIPageViewController` / custom | SwiftUI is simpler |
| **Drag-to-reorder list** | Native `.onMove` modifier on `List`/`ForEach` | `UITableView` with drag delegates | SwiftUI wins |
| **Swipe-to-delete** | Native `.onDelete` modifier | `UITableView` trailing swipe | SwiftUI wins |
| **Navigation / deep linking** | `NavigationStack` with `NavigationPath` (iOS 16+) supports programmatic, state-driven navigation | `UINavigationController` with coordinator pattern | SwiftUI `NavigationStack` is cleaner for state-driven resume |
| **Image zoom/pan** | No native SwiftUI zoom view; requires `MagnifyGesture` + `DragGesture` composition or `UIScrollView` bridge | `UIScrollView` with `viewForZooming` is trivial | UIKit bridge recommended |
| **Performance (image-heavy views)** | Adequate for MVP; `AsyncImage` and caching libraries work well | More control over memory | Acceptable for MVP |

### Specific UIKit bridges needed

1. **Camera capture**: `UIImagePickerController` via `UIViewControllerRepresentable` (or PHPicker for camera roll). Standard pattern, low effort.
2. **RoomPlan scanning**: `RoomCaptureView` wrapped in `UIViewControllerRepresentable`. Apple provides sample code for this.
3. **Zoomable image viewer**: Wrap `UIScrollView` for the lasso annotation base image. SwiftUI gestures for zoom/pan are workable but `UIScrollView` is more reliable for pinch-to-zoom with content insets.

### Why not UIKit-primary?

- The majority of Remo's screens are standard UI: lists, cards, forms, navigation, sheets. SwiftUI makes these 2-3x faster to build.
- State-driven navigation (for resume capability) maps naturally to SwiftUI's `NavigationStack` + persisted `NavigationPath`.
- The few UIKit bridges are well-established patterns with minimal friction.

---

## 2. Navigation Architecture

### Pattern: `NavigationStack` with Temporal-Driven State

The Remo workflow is a linear multi-step flow with resume capability. The backend uses **Temporal** for durable workflow orchestration, which fundamentally simplifies the iOS navigation story: the Temporal workflow is the authoritative state machine, and the iOS app is a thin UI layer that queries workflow state and sends signals.

This maps to `NavigationStack` where each step is a destination in a `NavigationPath` derived from the Temporal workflow's current position.

### Flow Diagram

```
HomeScreen
    |
    +--> [New Design] --> PhotoUploadScreen
    |                         |
    +--> [Resume Project] ----+--> InspirationUploadScreen
                              |         |
                              |         +--> RoomScanScreen
                              |                   |
                              |                   +--> IntakeChatScreen
                              |                            |
                              |                            +--> DesignGenerationScreen
                              |                                       |
                              |                                       +--> IterationScreen
                              |                                       |       (Lasso / Regenerate)
                              |                                       |       [loops up to 5x]
                              |                                       |
                              |                                       +--> ApprovalScreen
                              |                                                 |
                              |                                                 +--> OutputScreen
                              |                                                       (Design Image + Shopping List)
```

### Navigation State Model

```swift
enum ProjectStep: Codable, Hashable {
    case photoUpload
    case inspirationUpload
    case roomScan
    case intakeChat
    case designGeneration
    case iteration(round: Int)  // 1...5
    case approval
    case output
}

struct ProjectNavigationState: Codable {
    var projectId: String
    var currentStep: ProjectStep
    var completedSteps: Set<ProjectStep>
}
```

### Resume Implementation (Temporal-Backed)

The Temporal workflow is the single source of truth for "where is this project?" The iOS app does not need to maintain its own step-tracking state machine.

1. On "New Design": the iOS app calls the API to start a new Temporal workflow. The API returns a `projectId` (which is the Temporal workflow ID). The app saves this locally.
2. On each step transition: the iOS app sends a **Temporal signal** (via the API) to advance the workflow (e.g., `signal: photosUploaded`, `signal: scanCompleted`). The workflow progresses to the next activity.
3. On app launch (resume): the app reads local `projectId`s, then **queries each Temporal workflow** for its current state. The query response contains the current step, all accumulated data, and whether any activity is in-flight.
4. The app reconstructs `NavigationPath` from the Temporal query response and pushes the user directly to the correct screen with the correct data.

```swift
// API call: query Temporal workflow state
struct WorkflowStateResponse: Codable {
    var projectId: String
    var currentStep: ProjectStep
    var isActivityInFlight: Bool       // e.g., generation running
    var activityResult: ActivityResult? // e.g., generated images if done
    var projectData: ProjectSnapshot   // all accumulated data for this step
}
```

**Why this is better than custom state persistence:**
- No local state machine to keep in sync with the server — Temporal IS the state machine
- Crash recovery is free: Temporal remembers exactly where the workflow paused, including mid-activity state
- The 24-hour grace period and 48-hour abandonment purge are Temporal timers — no custom cron jobs or local timers needed
- If the app is deleted and reinstalled, project IDs are lost, but server-side cleanup happens automatically via Temporal's abandonment timer

### Deep Resume Details

| Step resumed at | Temporal query returns | iOS displays |
|-----------------|----------------------|--------------|
| Photo Upload | Which photos are uploaded, validation results | Upload screen with filled/empty slots |
| Inspiration Upload | Which inspiration photos + notes | Inspiration screen with existing entries |
| Room Scan | Whether scan was completed or skipped | Scan screen or next step accordingly |
| Intake Chat | Full conversation history + partial brief | Chat screen with message history restored |
| Design Generation | Whether generation activity is running/completed/failed + result images | Loading state, result display, or retry |
| Iteration (round N) | Current design image, all revision history, iteration count, in-flight status | Iteration screen at correct round |
| Approval | Final design image | Approval confirmation |
| Output | Shopping list data (if generation activity completed) | Output screen with products |

### Key Decision: No `TabView` / tab bar

Remo is a single-flow app with no parallel top-level sections. A tab bar would be wrong. The entire app is one `NavigationStack` rooted at `HomeScreen`.

---

## 3. Key UI Components

### 3.1 Photo Upload + Validation Feedback

**Complexity: Low-Medium**

Components:
- `PhotoUploadView`: Grid of upload slots (2 required room photos + up to 3 inspiration slots)
- Each slot: empty state with camera/gallery icon, filled state with thumbnail + delete button
- `PhotoValidationOverlay`: Inline error message per photo (blur, resolution, content)
- Source picker: `PHPickerViewController` (camera roll) or `UIImagePickerController` (camera capture)

Implementation approach:
- Use `PhotosUI` framework's `PhotosPicker` (SwiftUI-native since iOS 16) for camera roll.
- Use `UIImagePickerController` bridge for live camera capture.
- Validation runs asynchronously after selection via a `PhotoValidator` service (calls server API or on-device Vision framework for blur/content detection).
- Each photo slot is a state machine: `.empty` -> `.validating` -> `.valid` / `.invalid(reason)`

Effort estimate: **1-2 days**

### 3.2 LiDAR Room Scanning (RoomPlan API)

**Complexity: Medium** (see Section 4 for deep dive)

Components:
- `RoomScanView`: Wraps `RoomCaptureView` in `UIViewControllerRepresentable`
- Pre-scan info screen explaining benefits
- Post-scan confirmation with dimension summary
- Skip flow with trade-off notification

Implementation approach:
- Use Apple's `RoomPlan` framework (`RoomCaptureSession`, `RoomCaptureView`)
- The `RoomCaptureView` provides the full scanning UI out of the box (AR overlay, wall/floor detection visualization)
- On completion, extract `CapturedRoom` data (walls, openings, floors, objects with dimensions)
- Serialize the `CapturedRoom` to send to the server

Effort estimate: **1-2 days** (RoomPlan does the heavy lifting)

### 3.3 Chat Interface with Quick-Reply Buttons

**Complexity: Medium**

Components:
- `IntakeChatView`: Scrollable message list
- `ChatBubble`: Agent messages (left-aligned) and user messages (right-aligned)
- `QuickReplyBar`: Vertical list of numbered tappable chips + "Something else" option
- `ChatInputBar`: Text field with send button (appears for free-text questions or "Something else")
- `ProgressIndicator`: "Question 2 of 3" header

Implementation approach:
- `ScrollViewReader` + `LazyVStack` for the message list, with auto-scroll to bottom on new messages
- Quick-reply buttons as a `VStack` of styled buttons, inserted as the last item in the message list
- State machine per question: `.waitingForReply(options: [QuickReply])` / `.waitingForFreeText` / `.answered`
- Conversation state persisted for resume (message history + current question index)

Key considerations:
- Keyboard avoidance: Use `.scrollDismissesKeyboard(.interactively)` and content insets. SwiftUI handles this adequately in iOS 16+.
- The "Something else" button toggles the text input visible. Standard iOS keyboard dictation handles voice input.
- Progress indicator animates between steps.

Effort estimate: **2-3 days**

### 3.4 Side-by-Side / Swipeable Design Comparison

**Complexity: Low**

Components:
- `DesignComparisonView`: Container that switches between two modes
- `SideBySideView`: `HStack` of two `DesignOptionCard` views
- `SwipeableView`: `TabView(selection:)` with `.tabViewStyle(.page)` containing two `DesignOptionCard` views
- `DesignOptionCard`: Image + caption + select button
- View toggle icon in toolbar

Implementation approach:
- Detect screen orientation/size class to set default mode
- User can override via toggle; store preference in `@AppStorage`
- `TabView` with `.page` style gives native swipe with page indicators

Effort estimate: **0.5-1 day**

### 3.5 Lasso Annotation Tool

**Complexity: HIGH — This is the most complex UI component in the app**

Components:
- `LassoAnnotationView`: Full-screen image with overlay drawing canvas
- `LassoDrawingCanvas`: SwiftUI `Canvas` or custom `UIView` for freehand path input
- `RegionOutlineRenderer`: Renders closed paths with high-contrast adaptive outline + number chips
- `RegionEditor`: Modal/sheet form (action, instruction, avoid, style nudges)
- `EditListPanel`: Side panel (iPad) / bottom sheet (iPhone) with reorderable, deletable region list
- `ChipPlacementEngine`: Calculates number chip positions (outside bounding box, no overlap, no off-canvas)

#### Sub-component Breakdown

**A. Freehand Drawing Engine**

```swift
struct LassoPath {
    var points: [CGPoint]
    var isClosed: Bool
    var boundingBox: CGRect
}
```

- Capture touch/drag points via `DragGesture` (SwiftUI) or `touchesBegan/Moved/Ended` (UIKit)
- On finger lift: auto-close by connecting last point to first point
- Validate: minimum area (>= 2% of image area), no self-intersection, no overlap with existing regions

**B. Validation Logic**

- **Self-intersection detection**: Check if any segment of the path crosses any other non-adjacent segment. Algorithm: sweep-line or brute-force O(n^2) segment-segment intersection (acceptable for hand-drawn paths with ~100-500 points).
- **Overlap detection**: Check if any point of the new region falls inside an existing region polygon (point-in-polygon test), or if any edges cross.
- **Minimum area**: Compute area using the shoelace formula on the polygon vertices, compare to 2% of image pixel area.

**C. Region Rendering**

- Render closed path as a stroked outline with adaptive contrast (white stroke + dark shadow, or invert based on underlying image luminance sampled at path midpoint).
- Number chips: Circle with number text, placed outside the bounding box at top-right + 12pt offset. Collision avoidance between chips.

**D. Image Zoom/Pan with Drawing**

- This is the trickiest interaction design: the image must be zoomable and pannable, but when the lasso tool is active, finger drags must draw instead of pan.
- Solution: **Mode toggle**. When lasso tool is active, single-finger drag = draw. Pinch = zoom. Two-finger drag = pan. This is a standard pattern in annotation apps.
- Implementation: Use `UIScrollView` bridge for zoom/pan, overlay a transparent drawing view that captures single-finger touches.

**E. Edit List Panel**

- iPad: side panel using `NavigationSplitView` or a custom `HStack` layout with fixed-width sidebar.
- iPhone: bottom sheet using `.sheet` or a custom draggable sheet (`presentationDetents`).
- Reordering: `List` with `.onMove` modifier. On reorder, update region numbers everywhere (chips + list).
- Deletion: `.onDelete` modifier or swipe action.

Effort estimate: **4-6 days** (the single largest UI effort)

### 3.6 Edit List Panel (Reorderable, Deletable)

**Complexity: Medium** (included in Lasso section above, broken out for clarity)

- Standard SwiftUI `List` with `ForEach` + `.onMove` + `.onDelete`
- Each row: region number, action badge, instruction preview (truncated to ~40 chars)
- Tapping a row selects the region (bidirectional: list tap highlights image region, image region tap highlights list row)
- Tapping a selected row opens the Region Editor as a sheet

Effort estimate: **1 day** (integrated with Lasso)

### 3.7 Shopping List with Grouped Product Cards

**Complexity: Medium**

Components:
- `ShoppingListView`: Scrollable list of groups
- `ProductGroupSection`: Collapsible section header (e.g., "Seating", "Lighting")
- `ProductCard`: Image thumbnail, name, retailer, price, dimensions, "why this match" caption, buy button, fit badge (LiDAR only)
- `TotalCostHeader`: Sticky header showing total estimated cost
- Action buttons: "Share Shopping List", "Copy All"

Implementation approach:
- `List` with `Section` views, each with a collapsible `DisclosureGroup`
- `ProductCard` as a custom view with `AsyncImage` for product thumbnails
- "Buy" button opens URL via `Link` (SwiftUI) or `UIApplication.shared.open`
- "Share" and "Copy" use `ShareLink` (SwiftUI) or `UIActivityViewController` bridge
- Fit badge: conditional green checkmark or yellow warning based on LiDAR data

Effort estimate: **1.5-2 days**

### 3.8 Revision History Viewer

**Complexity: Low**

Components:
- `RevisionHistoryView`: Horizontal scrollable strip of revision thumbnails, or swipeable full-screen viewer
- Each revision: thumbnail image, revision number, timestamp

Implementation approach:
- `TabView` with `.page` style, or `ScrollView(.horizontal)` with snapping
- View-only: no editing or branching from older revisions (per spec)
- Current revision highlighted; user can swipe back and forward

Effort estimate: **0.5 day**

---

## 4. LiDAR Integration

### Recommendation: RoomPlan API (not raw ARKit)

### RoomPlan API (iOS 16+)

**What it provides:**
- `RoomCaptureView`: A complete, Apple-provided scanning UI with real-time AR visualization of detected walls, floors, openings, and objects
- `RoomCaptureSession`: Manages the scanning session lifecycle
- `CapturedRoom`: Structured output with:
  - Walls (with dimensions, position, orientation)
  - Openings (doors, windows with dimensions and wall association)
  - Floors and ceilings (with dimensions)
  - Detected objects (furniture, fixtures with category, dimensions, position)

**What we extract for Remo:**
- Room dimensions (length x width x height)
- Wall lengths and positions
- Window and door positions and sizes
- Floor area
- Existing furniture dimensions and positions (helpful for "items to keep")

**Pros:**
- Turnkey UI — no custom AR experience needed (huge time saver for hackathon)
- Apple handles the complex SLAM, plane detection, and object recognition
- Structured output is exactly what we need for dimension-aware shopping
- Well-documented with sample code

**Cons:**
- Only available on LiDAR-equipped devices (iPhone Pro, iPad Pro)
- Limited customization of the scanning UI (but we don't need much)
- Requires iOS 16+ (acceptable — we're targeting modern devices)

### Raw ARKit Approach

**What it provides:**
- `ARWorldTrackingConfiguration` with `.sceneReconstruction` for mesh generation
- `ARPlaneAnchor` for detected planes
- Full control over the AR experience

**Why we don't use it:**
- Requires building custom scanning UI from scratch (walls, floors, progress)
- Need to implement our own room geometry extraction from raw mesh/planes
- Significantly more development time for the same outcome
- RoomPlan is literally Apple's productized version of this

### Non-Pro Device Handling

```swift
func checkLiDARAvailability() -> Bool {
    return ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
}
```

| Device capability | User experience |
|-------------------|----------------|
| LiDAR available | "Scan Your Room" option with explanation of benefits |
| No LiDAR | Informational message about device limitation; "Continue without scan" button; one-time notification about trade-offs |

The app gracefully degrades: all features work without LiDAR, but shopping list products lack dimension verification and fit badges.

### Data Serialization

```swift
// CapturedRoom can be exported to USDZ
let exporter = RoomCaptureSession()
// Or serialize key dimensions manually:
struct RoomDimensions: Codable {
    var walls: [Wall]
    var openings: [Opening]
    var floorArea: Double  // sq meters
    var ceilingHeight: Double  // meters

    struct Wall: Codable {
        var length: Double
        var height: Double
        var position: SIMD3<Float>
        var normal: SIMD3<Float>
    }

    struct Opening: Codable {
        var type: OpeningType  // door, window
        var width: Double
        var height: Double
    }
}
```

Send serialized dimensions to the server for design generation and product matching.

---

## 5. State Management

### Architecture: Temporal as source of truth + Observable in-memory views

The key insight with Temporal: **the iOS app does not own workflow state.** Temporal owns the workflow state machine. The iOS app is a rendering layer that:
1. Queries Temporal for current state (via the API)
2. Sends signals to advance the workflow (via the API)
3. Holds an in-memory projection of the workflow state for UI rendering

This eliminates an entire class of bugs around state synchronization and crash recovery.

### Layer 1: In-Memory State (UI projection of Temporal workflow)

```swift
@Observable
class ProjectState {
    var projectId: String  // == Temporal workflow ID
    var currentStep: ProjectStep = .photoUpload
    var isActivityInFlight: Bool = false  // Temporal activity running

    // Photo upload
    var roomPhotos: [ValidatedPhoto] = []
    var inspirationPhotos: [InspirationPhoto] = []

    // Room scan
    var roomDimensions: RoomDimensions?
    var scanSkipped: Bool = false

    // Intake
    var designBrief: DesignBrief?
    var chatHistory: [ChatMessage] = []
    var intakeSkipped: Bool = false

    // Generation
    var designOptions: [DesignOption] = []
    var selectedOptionIndex: Int?

    // Iteration
    var currentDesignImage: DesignImage?
    var revisionHistory: [Revision] = []
    var iterationCount: Int = 0
    var maxIterations: Int = 5
    var currentAnnotations: [LassoRegion] = []  // 0-3 (local only, not persisted until submitted)

    // Output
    var isApproved: Bool = false
    var shoppingList: ShoppingList?

    /// Populate from a Temporal workflow query response
    func hydrate(from response: WorkflowStateResponse) {
        self.currentStep = response.currentStep
        self.isActivityInFlight = response.isActivityInFlight
        // ... map all fields from response.projectData
    }
}
```

**Important distinction**: `currentAnnotations` (the lasso regions the user is drawing right now) is local-only UI state. It is not sent to Temporal until the user taps "Generate Revision", at which point the app sends a signal with the annotation payload and the workflow kicks off the generation activity.

### Layer 2: Local Persistence (minimal — just project IDs)

```swift
struct LocalProjectReference: Codable {
    var projectId: String         // == Temporal workflow ID
    var lastUpdated: Date
    var thumbnailData: Data?      // small thumbnail for home screen list
    var roomLabel: String?
}
```

Stored in `UserDefaults`. Note: `lastStep` is no longer stored locally — it comes from querying Temporal.

On resume:
1. Read local `projectId` references
2. For each, query the Temporal workflow via API: `GET /api/projects/{projectId}/state`
3. If workflow is still alive: populate `ProjectState.hydrate(from:)` and show as resumable
4. If workflow has completed or been purged: remove the local reference

### Layer 3: Navigation State

```swift
@Observable
class AppRouter {
    var path = NavigationPath()
    var activeProjectId: String?

    func resumeProject(_ projectId: String, currentStep: ProjectStep) {
        // Query Temporal for full project state
        // Build NavigationPath from step 1 up to currentStep
        // Push all destinations onto the path
    }
}
```

### iOS-to-Temporal Interaction Pattern

The iOS app communicates with Temporal workflows through a thin API layer:

```
iOS App                        API Server                    Temporal
  |                               |                            |
  |-- POST /projects ------------>|-- StartWorkflow ---------->|
  |<-- { projectId } -------------|                            |
  |                               |                            |
  |-- POST /projects/{id}/photos->|-- Signal(photosUploaded) ->|
  |<-- { validation results } ----|                            |
  |                               |                            |
  |-- POST /projects/{id}/scan -->|-- Signal(scanCompleted) -->|
  |<-- { ack } -------------------|                            |
  |                               |                            |
  |-- POST /projects/{id}/brief ->|-- Signal(briefConfirmed) ->|
  |<-- { ack } -------------------|-- Activity(generateDesign) |
  |                               |                        ... |
  |-- GET  /projects/{id}/state ->|-- Query(getState) -------->|
  |<-- { step, data, inFlight } --|<-- { workflowState } ------|
  |                               |                            |
  |-- POST /projects/{id}/revise->|-- Signal(revisionReq) ---->|
  |<-- { ack } -------------------|-- Activity(generateRevision)
  |                               |                            |
  |-- POST /projects/{id}/approve>|-- Signal(approved) ------->|
  |<-- { ack } -------------------|-- Activity(genShoppingList) |
  |                               |   Timer(24h grace) ------->|
  |                               |   Timer(48h purge) ------->|
```

**Key patterns:**
- **Signals** for user actions that advance the workflow (photos uploaded, scan done, brief confirmed, revision requested, approved)
- **Queries** for reading current state without side effects (resume, polling for activity completion)
- **Activities** for long-running server-side work (image generation, shopping list generation, photo validation)
- **Timers** for time-based lifecycle events (24h grace period, 48h abandonment purge)

### In-Flight Activity Handling (Temporal-Powered)

| State | Behavior |
|-------|----------|
| Activity started, user stays | Poll workflow query every 2-3s; show loading animation; on completion, display results |
| Activity started, app backgrounded | Activity continues on Temporal (durable execution); on return, query for result |
| Activity started, app killed | Activity continues on Temporal; on resume, query returns completed result or still-running status |
| Activity failed | Temporal retries per activity retry policy; if all retries exhausted, query returns failure; app shows retry button; iteration count NOT incremented |
| Activity timed out | Same as failure — Temporal handles timeout; app shows retry |

**This is a major simplification vs. custom state management.** The iOS app never needs to track whether a background task was interrupted. Temporal guarantees the activity will complete (or exhaust retries) regardless of what happens on the client.

### Polling vs. Push for Activity Completion

For MVP, **polling the Temporal query** is simplest:
- While an activity is in-flight, poll `GET /api/projects/{id}/state` every 2-3 seconds
- When `isActivityInFlight` flips to `false`, display the result

Post-MVP enhancement: use server-sent events (SSE) or WebSocket to push activity completion to the app, eliminating polling.

### State Flow for Iteration

```
[IterationScreen]
    |
    +--> [Annotate] --> LassoMode (local UI state only)
    |                      |
    |                      +--> Draw regions (0-3, local)
    |                      +--> Fill Region Editors (local)
    |                      +--> [Generate Revision]
    |                             |
    |                             +--> Signal(revisionRequested, payload: annotations)
    |                             +--> Temporal Activity: generateRevision
    |                             +--> Poll query until complete
    |                             +--> Display new image
    |                             +--> Clear local annotations
    |
    +--> [Regenerate] --> TextInput
    |                      |
    |                      +--> Signal(regenerateRequested, payload: feedback)
    |                      +--> (same Temporal activity flow)
    |
    +--> [Approve]
           |
           +--> Signal(approved)
           +--> Temporal Activity: generateShoppingList
           +--> Poll query until complete
           +--> Display OutputScreen
           +--> Temporal starts 24h grace timer
```

---

## 6. Risk Areas

### RISK 1: Lasso Annotation Tool (HIGH RISK)

**What's hard:**
- Freehand drawing with simultaneous zoom/pan gesture disambiguation
- Self-intersection detection for complex paths
- Overlap detection between regions (computational geometry)
- Adaptive high-contrast outlines on varied image backgrounds
- Number chip placement that avoids overlap and stays on-canvas
- Bidirectional selection sync between image regions and edit list

**What might not work well:**
- Touch accuracy on small phone screens — users may struggle to draw precise regions on a 6.1" screen
- Performance of validation checks on complex paths during drawing

**Fallbacks:**
- Simplify to rectangular selection instead of freehand lasso (much easier, covers 80% of use cases)
- Reduce to a single region per revision instead of 3 (eliminates overlap detection)
- Skip adaptive outline color — use a fixed bright color with thick white border (simpler, still readable)

### RISK 2: Photo Validation (MEDIUM RISK)

**What's hard:**
- "Not a room" detection and "too many people" detection require ML models
- On-device inference may be slow; server-side adds latency
- False positives will frustrate users

**Fallbacks:**
- MVP: Do validation server-side with a simple VLM call (send image, get pass/fail)
- Skip content validation for inspiration photos (they're inherently diverse)
- Start with only blur + resolution checks (deterministic), add content checks later

### RISK 3: RoomPlan Scanning Reliability (MEDIUM RISK)

**What's hard:**
- RoomPlan can struggle with unusual room shapes, glass walls, mirrors, very dark rooms
- Users may not understand how to scan properly (move too fast, not enough coverage)

**What might not work well:**
- Scan quality varies significantly by room type and user behavior

**Fallbacks:**
- Strong onboarding instructions for scanning ("Walk slowly around the perimeter")
- If scan data seems incomplete, prompt user to retry or skip
- Treat LiDAR data as "best effort" — design generation and shopping still work without it

### RISK 4: Chat UI Keyboard Management (LOW-MEDIUM RISK)

**What's hard:**
- SwiftUI keyboard avoidance can be inconsistent, especially with custom chat layouts
- Quick-reply buttons need to stay visible above the keyboard when text input is active

**Fallbacks:**
- Use `ScrollViewReader` to auto-scroll to the input area
- If SwiftUI keyboard handling is buggy, wrap the chat input in a UIKit `UITextField` bridge

### RISK 5: Generation Latency (LOW-MEDIUM RISK — mitigated by Temporal)

**What's hard:**
- Image generation takes 10-60+ seconds
- Users may abandon during loading

**Mitigated by Temporal:**
- Generation runs as a Temporal activity with durable execution — if the user leaves, the activity completes server-side regardless
- On return, the app queries the workflow and gets the completed result immediately
- No need for `BGProcessingTask` or push notifications for MVP — the result is just "there" when the user comes back
- Temporal's activity retry policy handles transient failures automatically

**Remaining frontend concern:**
- UX during the wait: show engaging loading state (progress animation, "design tips" carousel)
- Poll interval trade-off: too frequent = unnecessary API calls, too infrequent = user sees stale loading state after completion

### RISK 6: Shopping List Product Matching Quality (LOW RISK — frontend only)

**What's hard (backend):**
- Exa search may not always return great matches

**Frontend implication:**
- Must handle empty/poor results gracefully
- The "low confidence" fallback (Google Shopping link) must not look broken

**Fallback:**
- Design the product card to degrade gracefully: missing image uses a category icon placeholder, missing price shows "Check price", missing dimensions are simply omitted

---

## 7. Recommendations for Hackathon MVP

### Architecture Choices

| Decision | Choice | Rationale |
|----------|--------|-----------|
| UI Framework | SwiftUI-primary, UIKit bridges where needed | Fastest development for 80% of screens |
| Min iOS version | iOS 17 | Gives us `@Observable`, improved `NavigationStack`, `SwiftData` if needed, `presentationDetents` improvements. It is a hackathon — no legacy support needed |
| Navigation | `NavigationStack` with Temporal-derived path | Temporal workflow state drives navigation; no local state machine needed |
| State management | `@Observable` classes hydrated from Temporal queries | Simplest approach; Temporal is the source of truth, not the app |
| Workflow orchestration | **Temporal** (server-side) | Durable execution handles crash recovery, resume, background generation, and lifecycle timers — eliminates custom state persistence |
| Persistence | `UserDefaults` for project IDs only; Temporal owns all workflow state | Minimal local footprint; state never drifts |
| LiDAR | RoomPlan API | Turnkey; no custom AR experience needed |
| Image loading | `AsyncImage` + `URLCache` (or `Kingfisher` if needed) | Adequate for MVP |
| Networking | `async/await` with `URLSession` | No need for Alamofire at this scale |
| Activity polling | Poll Temporal query every 2-3s during in-flight activities | Simple for MVP; upgrade to SSE/WebSocket post-MVP |

### Build Order (Priority-Sequenced)

| Order | Component | Est. Effort | Dependencies |
|-------|-----------|-------------|--------------|
| 1 | Project skeleton + Navigation + Home Screen + `WorkflowClient` | 0.5 day | API server with Temporal must be running |
| 2 | Photo Upload + Camera/Gallery integration | 1 day | Navigation |
| 3 | Photo Validation (blur + resolution only for day 1) | 0.5 day | Photo Upload |
| 4 | LiDAR Room Scan (RoomPlan wrapper) | 1 day | Navigation |
| 5 | Intake Chat UI (messages + quick-reply buttons) | 2 days | Navigation |
| 6 | Design Generation display (comparison view) + polling | 1 day | Navigation, WorkflowClient |
| 7 | Iteration screen (basic: full regenerate text input) | 1 day | Design Generation |
| 8 | Lasso Annotation (freehand drawing + region editor) | 3-4 days | Iteration screen |
| 9 | Edit List panel (reorder, delete, sync with image) | 1 day | Lasso Annotation |
| 10 | Approval + Output screen | 0.5 day | Iteration |
| 11 | Shopping List display (product cards, groups) | 1.5 days | Output screen |
| 12 | Resume flow (query Temporal + reconstruct navigation) | 0.5 day | All above (simpler with Temporal) |
| 13 | Polish: validation feedback, error states, loading states | 1 day | All above |
| **Total** | | **~12.5-14.5 days** | |

**Note**: Resume flow (item 12) is reduced from 1 day to 0.5 day because Temporal handles all the hard parts (state persistence, crash recovery, step tracking). The iOS work is just: query workflow state, build `NavigationPath`, push.

### Simplification Opportunities (If Time-Pressed)

| Simplification | Time Saved | Trade-off |
|----------------|-----------|-----------|
| Rectangle selection instead of freehand lasso | 2-3 days | Less precise, but much simpler. Covers most use cases. |
| 1 region per revision instead of 3 | 1 day | Eliminates overlap detection, reordering, multi-region UI |
| Skip photo content validation (keep blur + resolution only) | 0.5 day | Users might upload non-room photos; server catches it |
| Skip side-by-side view; swipeable only | 0.5 day | Minor UX reduction |
| Skip revision history viewer | 0.5 day | Users can't compare old revisions (they can still iterate forward) |
| Use `List` for chat instead of custom layout | 0.5 day | Less polished but functional |

### Key Libraries / Frameworks

| Need | Framework | Notes |
|------|-----------|-------|
| UI | SwiftUI | Primary |
| Camera | AVFoundation / PhotosUI | `PHPickerViewController` for gallery, `UIImagePickerController` for camera |
| LiDAR | RoomPlan | `RoomCaptureView` + `CapturedRoom` |
| AR check | ARKit | Only for `ARWorldTrackingConfiguration.supportsSceneReconstruction` check |
| Image blur detection | Vision | `VNImageRequestHandler` with `VNDetectFacesRequest` or custom sharpness check via `CIFilter` Laplacian |
| Networking | URLSession | `async/await` pattern |
| Local storage | UserDefaults | For project references and preferences |
| Image caching | URLCache or Kingfisher | Avoid re-downloading generated images |

### File Structure (Recommended)

```
Remo/
  App/
    RemoApp.swift
    AppRouter.swift
  Models/
    ProjectState.swift
    DesignBrief.swift
    LassoRegion.swift
    ShoppingList.swift
    RoomDimensions.swift
    WorkflowModels.swift     // WorkflowStateResponse, signal payloads, query types
  Views/
    Home/
      HomeScreen.swift
      ProjectCard.swift
    PhotoUpload/
      PhotoUploadScreen.swift
      PhotoSlot.swift
      PhotoValidator.swift
    RoomScan/
      RoomScanScreen.swift
      RoomCaptureViewWrapper.swift
    Intake/
      IntakeChatScreen.swift
      ChatBubble.swift
      QuickReplyBar.swift
    Generation/
      DesignComparisonScreen.swift
      DesignOptionCard.swift
    Iteration/
      IterationScreen.swift
      LassoAnnotationView.swift
      LassoDrawingCanvas.swift
      RegionEditor.swift
      EditListPanel.swift
      RegenerateSheet.swift
      RevisionHistoryView.swift
    Approval/
      ApprovalScreen.swift
    Output/
      OutputScreen.swift
      ShoppingListView.swift
      ProductCard.swift
  Services/
    APIClient.swift              // low-level HTTP layer
    WorkflowClient.swift         // Temporal-specific: start workflow, send signals, run queries, poll activities
    PhotoValidationService.swift
    ProjectPersistence.swift     // local project ID storage only (UserDefaults)
    ActivityPoller.swift         // polls Temporal query during in-flight activities; publishes completion
  Utilities/
    GeometryHelpers.swift    // polygon area, intersection, point-in-polygon
    ImageHelpers.swift       // blur detection, resolution check
```

---

## Summary

The Remo iOS frontend is **feasible for a hackathon MVP** with the right scoping choices:

1. **SwiftUI-primary** is correct — it accelerates the 80% of standard UI while the 20% needing UIKit bridges (camera, RoomPlan, zoom) is well-understood.

2. **RoomPlan** is the clear choice for LiDAR — it eliminates the need to build custom AR scanning UI.

3. **The lasso annotation tool is the critical path** — it's the most complex component and the most likely to slip. Scope it carefully: start with rectangle selection and upgrade to freehand if time permits.

4. **Temporal as the orchestration layer** is a major simplification for the iOS app. The app becomes a thin UI client that sends signals and queries state — no custom state machine, no crash recovery logic, no background task management, no lifecycle timers. This is the biggest architectural win for hackathon velocity.

5. **State-driven navigation** with `NavigationStack` maps naturally to Temporal workflow state. Resume is trivial: query the workflow, build the path, push.

6. **The iOS app stores almost nothing locally** — just project IDs in UserDefaults. All workflow state, project data, generated images, and revision history live in Temporal + server storage.

Total estimated effort: **12.5-14.5 days** for a full implementation, reducible to **8-10 days** with the simplification options above. For a hackathon, prioritize the happy path end-to-end first, then add polish and edge cases.
