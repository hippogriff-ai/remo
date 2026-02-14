# LiDAR Utilization Plan

Wire LiDAR room dimensions through the Designer Brain's `RoomContext` pipeline so generation, editing, and shopping all produce spatially-aware outputs. Establish a fixture-replay strategy so a single real-device scan can be reused by Maestro for all subsequent automated testing.

> **Last verified**: 2026-02-14 against `origin/main` (commit `d7f119f`, PR #10 merged).
> PR #10 added Designer Brain (`analyze_room_photos` → `RoomAnalysis` → `RoomContext`).
> G4 confirmed resolved. G7 confirmed intentional. All other gaps still apply.
> G2 is now broader: edit misses both `room_dimensions` AND `room_context`.

---

## Architecture: Designer Brain + LiDAR Fusion (PR #10)

PR #10 introduced a **Designer Brain** pipeline that runs in parallel with the user flow:

```
Photos uploaded
  │
  ├── analyze_room_photos (Claude Opus) ──→ RoomAnalysis
  │     • room_type, furniture observations, lighting, style signals
  │     • behavioral inferences, strengths, opportunities
  │     • runs eagerly after photo upload, before intake
  │
  └── LiDAR scan (optional) ──→ ScanData ──→ RoomDimensions
        • width_m, length_m, height_m, walls, openings, furniture, surfaces

Both merge via _build_room_context():
  ──→ RoomContext { photo_analysis: RoomAnalysis, room_dimensions: RoomDimensions, enrichment_sources: ["photos", "lidar"] }
```

**Data flow per pipeline step** (current state):

| Step | Gets `room_dimensions`? | Gets `room_context`? | Notes |
|------|------------------------|---------------------|-------|
| Intake (`IntakeChatInput`) | via `project_context` | Indirectly (via `room_analysis` in state) | Intake agent sees analysis in project context |
| Generation (`GenerateDesignsInput`) | Yes (G26) | Yes (G26) | Gets both dims + context; activity consumption T2 scope |
| Edit (`EditDesignInput`) | Yes (G2) | Yes (G2) | Both wired in Loop 1; activity consumption T2 scope |
| Shopping (`GenerateShoppingListInput`) | Yes | Yes | Full context — richest pipeline step |

## Current State

| Component | Status | Notes |
|-----------|--------|-------|
| Backend parser (`utils/lidar.py`) | Done | 77 tests (G11 unit validation, G12 upper bounds, G27 lower bounds, G28 whitespace tolerance, boundary tests) |
| Contracts (`RoomDimensions`, `ScanData`) | Frozen | `width_m`, `length_m`, `height_m`, `walls`, `openings`, `furniture`, `surfaces`, `floor_area_sqm` |
| Contracts (`RoomAnalysis`, `RoomContext`) | Done (PR #10) | Photo analysis + LiDAR fusion model |
| Contracts (`GenerateDesignsInput`) | Done (G26) | Added `room_context: RoomContext | None = None` (additive optional) |
| Contracts (`EditDesignInput`) | Done (G2) | Added `room_dimensions` + `room_context` (additive optional) |
| Designer Brain (`analyze_room_photos`) | Done (PR #10) | Claude Opus photo analysis, eager execution after photo upload |
| `_build_room_context()` | Done (PR #10 + Loop 13) | Merges photo analysis + LiDAR into `RoomContext` (consolidated single method) |
| API endpoints | Done | `POST /scan` (parse + signal + G8 size limit + G24 negative check), `POST /scan/skip` |
| Mock API fidelity | Done (G25) | `upload_scan` mirrors `_build_room_context()`, `start_over` clears analysis/context |
| Workflow signals | Done | `complete_scan(ScanData)`, `skip_scan()` (G6 step guard) |
| Generation prompt wiring | Done | `_format_room_context()` → `{room_context}` in template; `room_context` passed via G26 |
| Shopping dimension pass-through | Done | `_shopping_input()` passes both `room_dimensions` AND `room_context`; G21 source label fix; G22 pre-shopping analysis collection |
| Edit activity wiring | Done (G2) | `_edit_input()` passes `room_dimensions` + `room_context`; activity consumption T2 scope |
| iOS `LiDARScanScreen` | Partially done | G1 JSON key fixed; G9 real LiDAR detection; B2 fixture injection. Device-dependent work remaining (G16, G17) |
| iOS `RoomDimensions` model | Done | Swift Codable, mirrors backend exactly |
| Maestro flows | Done (Phase C) | `02-lidar-scan.yaml`, `happy-path-lidar.yaml`, `07-output-verify-lidar.yaml` |
| LangSmith tracing | Done (Phase D) | Zero-cost wrappers in 4 activities; ImportError + runtime guards |

**Backend input schema** (what iOS must POST to `/scan`):
```json
{
  "room": { "width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters" },
  "walls": [{ "id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0 }],
  "openings": [{ "type": "door", "wall_id": "wall_0", "width": 0.9, "height": 2.1, "position": { "x": 1.5 } }],
  "furniture": [{ "type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8 }],
  "surfaces": [{ "type": "floor", "material": "hardwood" }],
  "floor_area_sqm": 24.36
}
```

---

## Known Gaps (verified against main 2026-02-14)

> **Source**: 4 parallel agents scanned backend pipeline, iOS code, plan assumptions,
> and E2E test coverage. Verified against `origin/main` (`d7f119f`, includes PR #10).

### CRITICAL — Must Fix Before LiDAR Is Usable

| # | Gap | Location | Fix |
|---|-----|----------|-----|
| ~~G1~~ | **DONE** — iOS JSON key fixed from `"rooms"` (array) to `"room"` (object) in `LiDARScanScreen.swift` + `MockClientTests.swift`. Mock client (`MockWorkflowClient`) ignores payload (hardcodes dims) — correct for mock. | `LiDARScanScreen.swift:101-106`, `MockClientTests.swift:106-108` | Fixed: singular `"room"` dict with `unit`, `walls`, `openings`, `floor_area_sqm`. 47 Swift tests pass. |
| ~~G2~~ | **DONE** — `EditDesignInput` now has `room_dimensions` and `room_context` (additive optional). `_edit_input()` wired mirroring `_shopping_input()`. 8 tests verify (3 unit + 1 integration). | `contracts.py:309-310`, `workflow.py:_edit_input()` | Fixed: contract + workflow wiring. Edit activity consumption pending (T2 scope). |
| ~~G3~~ | **DONE** — Added `TestG3ScanDataFullPath` class with 6 E2E tests: scan data persistence at intake, through generation, through iteration, full path to completed (with shopping), furniture/opening parsing, and scan data surviving start-over. Uses `_advance_to_intake_with_scan()` helper with `_SCAN_DATA` reference fixture. | `test_e2e.py` | Fixed: 6 tests covering LiDAR-present E2E path. All use real scan JSON (4.2m×5.8m room with furniture+openings). |

### HIGH — Quality/Robustness

| # | Gap | Location | Fix |
|---|-----|----------|-----|
| ~~G5~~ | **DONE** — `_format_room_context()` now includes furniture bounding-box dimensions (e.g., "sofa (2.1m × 0.9m × h0.8m)") and opening dimensions (e.g., "door (0.9m × 2.1m)"). 3 new tests: `test_format_furniture_with_dimensions`, `test_format_openings_with_dimensions`, `test_format_furniture_partial_dimensions`. All 55 generate tests pass. | `generate.py:55-110` | Fixed: enriched prompt context for spatial reasoning. |
| ~~G6~~ | **DONE** — Added `if self.step != "scan": return` with warning log to `complete_scan` signal handler. Test `test_complete_scan_ignored_at_wrong_step` verifies. Fixed race condition in 3 EagerAnalysis tests exposed by the guard. | `workflow.py:346-352` | Fixed: step guard + warning log. 1173 backend tests pass. |
| ~~G8~~ | **DONE** — Added `MAX_SCAN_BYTES = 1 MB` content-length check to scan endpoint. Returns 413 for oversized payloads, 400 for malformed Content-Length. Best-effort (header-based). Tests: `test_upload_scan_oversized_payload_returns_413`, `test_upload_scan_malformed_content_length_returns_400`. | `projects.py` scan endpoint | Fixed: size limit + error responses documented (400/413 added to endpoint responses). |
| ~~G9~~ | **DONE** — Added `linkedFramework("ARKit")` and `linkedFramework("RoomPlan")` with `.when(platforms: [.iOS])` condition. Replaced hardcoded `hasLiDAR { true }` with real `ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)` check. Fixture mode override ensures Maestro testing works on simulator. `#if canImport(ARKit)` guards macOS builds. | `Package.swift`, `LiDARScanScreen.swift` | Fixed: framework linkage + real LiDAR detection. Compilation verified. |
| G10 | Apple RoomPlan `CapturedRoom` API assumptions may be wrong — no `.dimensions` property; wall orientation needs computation from transform matrix; floor material not detected | Plan Phase A2 mapping table | Verify actual API on physical device during Phase A; see "Apple RoomPlan Reality" section below |

### MEDIUM — Reliability/Completeness

| # | Gap | Location | Notes |
|---|-----|----------|-------|
| ~~G11~~ | **DONE** — Added unit validation: rejects non-meter units (`"feet"`, `"inches"`, etc.) with `LidarParseError`. Accepts `"meters"`, `"m"` (case-insensitive), or absent unit field. 6 tests in `TestUnitValidation`. | `lidar.py:59-62` | Fixed: defensive check at system boundary. |
| ~~G12~~ | **DONE** — Added `MAX_DIMENSION_M = 50.0` upper bound. Rejects any dimension exceeding 50m with `LidarParseError`. Covers width, length, and height individually. 6 tests in `TestUpperBounds`. | `lidar.py:79-83` | Fixed: prevents implausible dimensions from reaching generation/shopping. |
| ~~G13~~ | **DONE** — Opening dimensions now included in generation prompt via G5 enrichment of `_format_room_context()`. Outputs e.g., "door (0.9m × 2.1m)" instead of just "door". Test: `test_format_openings_with_dimensions`. | `generate.py:70-82` | Fixed: covered by G5 enrichment. |
| G14 | No RemoLiDAR test suite exists | `ios/Packages/RemoLiDAR/Tests/` | Create after Phase A implementation |
| ~~G15~~ | **DONE** — Created `02-lidar-scan.yaml` (fixture-based scan step), `happy-path-lidar.yaml` (full LiDAR happy path), and `07-output-verify-lidar.yaml` (LiDAR-specific output verification). | `.maestro/flows/` | Fixed: Phase C complete — 3 Maestro flows created. |
| G16 | Camera/AR permission handling not addressed | `LiDARScanScreen.swift` | Add `AVCaptureDevice.authorizationStatus` check to Phase A1 |
| G17 | App backgrounding during scan not handled | `LiDARScanScreen.swift` | Add `scenePhase` monitoring; transition to `.failed` on resign active |
| ~~G18~~ | **DONE** — Added `test_upload_scan_after_skip_returns_409` and `test_double_submit_scan_returns_409` in `TestScanEndpoints`. Both verify 409 with appropriate error codes. | `test_api_endpoints.py` | Fixed: race condition and double-submit tests. |
| G19 | Wall orientation needs computation from transform matrix | Plan Phase A2 | Not directly available from RoomPlan; document algorithm or mark as optional |
| G20 | Floor material not detected by RoomPlan | Plan Phase A2 | `surfaces[].material` will always be null unless custom heuristics are added |
| ~~G21~~ | **DONE** — Fixed source label bug in `_format_room_constraints_for_prompt`: when dims came from `room_context.room_dimensions` (fallback), label always said "photo analysis" even for LiDAR data. Now checks `enrichment_sources`. 7 new tests (5 source label + 1 tiny room + 1 zero-dim filter). | `shopping.py:221`, `test_shopping.py` | Fixed: accurate source attribution in shopping prompt. |
| ~~G22~~ | **DONE** — Added `await self._resolve_analysis()` before shopping phase. The `asyncio.shield()` pattern kept the activity alive after intake's 30s timeout, but nobody collected the late result. `_analysis_handle` now cleared after success/failure to prevent redundant re-awaiting. Warning log when shopping proceeds without analysis. 5 new tests. Also consolidated duplicate `_enrich_context()`/`_build_room_context()` into single method. | `design_project.py:258`, `test_workflow.py` | Fixed: slow analysis no longer silently lost before shopping. |
| ~~G23~~ | **DONE** — Fixed `_format_room_context()`: `type=None` or `material=None` in scan dicts now uses fallback labels ("opening", "item", "surface", "unknown") instead of literal `"None"`. Changed `.get("key", default)` to `.get("key") or default`. 4 new tests. | `generate.py:78,95,117`, `test_generate.py` | Fixed: LLM prompt no longer contains "None" as entity type. |
| ~~G24~~ | **DONE** — Scan endpoint rejects negative Content-Length with 400. Prevents nonsensical -1024 from bypassing the `> MAX_SCAN_BYTES` check. 1 new test. | `projects.py:566`, `test_api_endpoints.py` | Fixed: bounds check at system boundary. |
| ~~G25~~ | **DONE** — Mock API LiDAR fidelity: (1) `upload_scan` now mirrors `_build_room_context()` — builds `RoomContext` only when `room_analysis` is present, with `estimated_dimensions` mutation. (2) `start_over` clears `room_analysis`/`room_context` matching real workflow. 4 new tests. | `projects.py` mock paths, `test_api_endpoints.py` | Fixed: mock now matches real workflow for context building and start_over cleanup. |
| ~~G26~~ | **DONE** — `GenerateDesignsInput` now has `room_context: RoomContext | None = None` (additive optional). `_generation_input()` wired with `room_context=self.room_context`, matching `_edit_input()` and `_shopping_input()`. Generation activity consumption pending T2 scope. 2 existing tests updated with room_context assertions (photos-only and photos+LiDAR). | `contracts.py:294`, `design_project.py:506`, `test_workflow.py` | Fixed: all 4 pipeline steps now receive room_context. |
| ~~G27~~ | **DONE** — Added `MIN_DIMENSION_M = 0.3` lower bound. Rejects dimensions below 30cm (no real room is that small). Matches G12 upper bound pattern. 7 new tests in `TestLowerBounds`. Also added `logger.warning` for non-list field fallbacks (walls/openings/furniture/surfaces) and floor_area discrepancy warning (>5x or <0.2x of computed). 17 new tests total. | `lidar.py:48,84-87,97-115,132-140`, `test_lidar.py` | Fixed: lower bound + logging at system boundary. |
| ~~G28~~ | **DONE** — Added `.strip()` to unit validation before comparison. Whitespace-padded units (`"  m  "`, `" meters "`) now accepted; whitespace-only (`"   "`) still rejected. 3 new tests. | `lidar.py:67` | Fixed: tolerant of RoomPlan whitespace in unit field. |

### Resolved / Not Applicable

| # | Original Claim | Actual Status |
|---|----------------|---------------|
| ~~G4~~ | `_shopping_input()` doesn't pass `room_dimensions` | **Already wired** at `workflow.py:530`. PR #10 further enriched shopping: now passes `room_context=self.room_context` (line 531) giving shopping both LiDAR dims AND photo analysis |
| ~~G7~~ | `start_over` doesn't reset `scan_skipped` | **Intentional design choice** — code comment says "photos, scan_data, and scan_skipped are preserved across restarts since re-scanning is expensive and photos are reusable". Note: `start_over` DOES reset `room_analysis` and `room_context` (lines 413-414) |

### Resolution Order

1. **G1**: Fix immediately — change `"rooms"` → `"room"` in `LiDARScanScreen.swift` and `MockWorkflowClient.swift`
2. **G2**: Add `room_dimensions` to `EditDesignInput` contract + wire in `_edit_input()`
3. **G3**: Add E2E test with scan data (after G1+G2)
4. **G5, G6, G8**: Backend hardening — parallel with Phase A
5. **G9**: Covered by Phase A4
6. **G10, G19, G20**: Verify on physical device during Phase A
7. **G11-G18**: Address during relevant phase implementation

---

## Apple RoomPlan Reality (G10, G19, G20)

> The Phase A2 mapping table below was written speculatively. These items need
> verification on a physical device with Xcode autocomplete. Known discrepancies:

| Plan Assumption | Apple Reality | Impact |
|-----------------|---------------|--------|
| `CapturedRoom` has `.dimensions` (simd_float3) for room W×L×H | **No such property.** Room dimensions must be computed from wall bounding boxes or the overall model extent. | Phase A2 exporter needs a `computeRoomDimensions()` method |
| Walls have `orientation` field (0°, 90°, 180°, 270°) | **Not directly available.** `CapturedRoom.Surface` has a `transform` matrix (simd_float4x4). Orientation must be computed from the matrix. | Mark orientation as optional in JSON; compute if feasible |
| `surfaces[].material` can be "hardwood" | **RoomPlan does not detect materials.** Surface has `category`, `dimensions`, `confidence` only. | `material` field will be null. Remove from reference fixture or mark as "unknown" |
| `openings` have `wall_id` linking to parent wall | **No direct reference.** Spatial relationships must be computed from adjacency/edge overlap. | Implement spatial association algorithm or omit `wall_id` |
| `floor_area_sqm` from "sum of floor polygon areas" | **Floor not exposed as polygonal surface.** Compute from bounding box or wall perimeter. | Use `width * length` fallback (backend parser already does this) |

**Action**: During Phase A2, build `RoomPlanExporter.export()` iteratively on a real device. Start with the fields that are directly available (objects, surface categories, dimensions) and add computed fields as feasible. The backend parser gracefully handles missing optional fields — `walls`, `openings`, `furniture`, `surfaces` all default to `[]`.

---

## Phase A: Real LiDAR Integration (P7 — Requires Physical iPhone Pro)

### A0: Pre-Requisite Fixes (No Device Needed)

> Fix G1 and G2 before any device work. These are code-only changes testable with existing unit tests.

**G1 fix** — `LiDARScanScreen.swift:101-103`:
```swift
// BEFORE (broken):
try await client.uploadScan(projectId: projectId, scanData: [
    "rooms": [["width": 4.2, "length": 5.8, "height": 2.7]],
])

// AFTER (correct — matches backend parser schema):
try await client.uploadScan(projectId: projectId, scanData: [
    "room": ["width": 4.2, "length": 5.8, "height": 2.7],
    "walls": [],
    "openings": [],
    "floor_area_sqm": 24.36,
])
```

Same fix in `MockWorkflowClient.swift` (line 102-108) — the mock internally creates correct `RoomDimensions`, but the `scanData` parameter it receives should also match the real schema for consistency.

**G2 fix** — `contracts.py` (match shopping's contract which already has both):
```python
class EditDesignInput(BaseModel):
    project_id: str
    base_image_url: str
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    design_brief: DesignBrief | None = None
    annotations: list[AnnotationRegion] = []
    feedback: str | None = None
    chat_history_key: str | None = None
    room_dimensions: RoomDimensions | None = None  # ← ADD (matches GenerateShoppingListInput)
    room_context: RoomContext | None = None         # ← ADD (matches GenerateShoppingListInput)
```

**G2 fix** — `workflow.py:_edit_input()` (mirror `_shopping_input()` pattern):
```python
base = EditDesignInput(
    project_id=self._project_id,
    base_image_url=self.current_image,
    room_photo_urls=[...],
    inspiration_photo_urls=[...],
    design_brief=self.design_brief,
    chat_history_key=self.chat_history_key,
    room_dimensions=self.scan_data.room_dimensions if self.scan_data else None,  # ← ADD
    room_context=self.room_context,  # ← ADD (includes photo analysis + LiDAR fusion)
)
```

> **Why both fields?** `room_dimensions` gives the edit activity raw LiDAR measurements
> for spatial constraints. `room_context` adds the Designer Brain's photo analysis
> (furniture observations, lighting, style signals) for richer edit reasoning.
> This matches `GenerateShoppingListInput` which already has both.

### A1: Device Capability Check + Camera Permission (G16)

**File**: `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift` (lines 19-24)

Replace the hardcoded `hasLiDAR { true }` with:
```swift
import ARKit

private var hasLiDAR: Bool {
    ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)
}
```

**Camera permission check** (G16):
```swift
private func checkCameraPermission() async -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized: return true
    case .notDetermined:
        return await AVCaptureDevice.requestAccess(for: .video)
    case .denied, .restricted:
        // Show alert directing user to Settings
        errorMessage = "Camera access is required for room scanning. Enable it in Settings."
        return false
    @unknown default: return false
    }
}
```

**Behavior change**: Non-LiDAR devices (iPhone 15, SE, etc.) show "LiDAR is not available on this device" + "Skip Scan" only. LiDAR devices (iPhone 12 Pro+, iPad Pro) show "Start Scanning".

**Test**: Run on both a non-LiDAR iPhone and an iPhone 12 Pro (or later Pro model) to verify correct branching.

### A2: RoomPlan Data Pipeline

**New files** in `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/`:

#### `RoomScanResult.swift`
Value type holding the capture output:
```swift
struct RoomScanResult {
    let capturedRoom: CapturedRoom  // Apple's output object
    let scanDuration: TimeInterval
}
```

#### `RoomPlanExporter.swift`
Converts Apple's `CapturedRoom` into the JSON schema the backend expects:
```swift
struct RoomPlanExporter {
    /// Convert CapturedRoom → [String: Any] matching backend schema
    static func export(_ room: CapturedRoom) -> [String: Any]
}
```

Mapping logic (verified fields marked with checkmark, speculative with question mark):

| CapturedRoom property | → JSON field | Confidence |
|-----------------------|-------------|------------|
| Computed from wall extents / model bounding box | `room.width`, `room.length`, `room.height` | ? — needs device verification (G10) |
| `walls` (Surface array) with `.dimensions` | `walls[]` with width, height | OK — dimensions available |
| Wall `.transform` matrix | `walls[].orientation` | ? — needs computation from matrix (G19) |
| `doors` + `windows` (Surface arrays) | `openings[]` with type, dimensions | OK |
| Spatial relationship computation | `openings[].wall_id` | ? — no direct API, needs adjacency algorithm |
| `objects` array with `.category` + `.dimensions` | `furniture[]` with type, bounding box dims | OK — 16 categories available |
| Floor surfaces | `surfaces[]` with type=floor | OK — category available |
| N/A | `surfaces[].material` | NO — RoomPlan doesn't detect materials (G20) |
| `width * length` or bounding box area | `floor_area_sqm` | OK — computed |

**Unit tests**: Test the dict → JSON serialization with known inputs. The backend parser already has 77 tests for the receiving end.

#### `ScanUploader.swift`
Thin wrapper: serialize → call `client.uploadScan()`:
```swift
struct ScanUploader {
    let client: any WorkflowClientProtocol

    func upload(projectId: String, result: RoomScanResult) async throws {
        let scanData = RoomPlanExporter.export(result.capturedRoom)
        try await client.uploadScan(projectId: projectId, scanData: scanData)
    }
}
```

### A3: Scan UI State Machine

**File**: `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift`

Replace the single `isScanning` bool with a proper state machine:

```swift
enum ScanState {
    case ready              // "Start Scanning" button visible
    case scanning           // RoomCaptureView active, progress animation
    case captured           // Scan complete, preview with "Upload" / "Rescan"
    case uploading          // Uploading to backend, spinner
    case uploaded           // Success, auto-advances to next step
    case failed(String)     // Error message, "Retry" button
}
```

**UI per state**:
- `ready`: Current layout (cube icon + description + buttons)
- `scanning`: Full-screen `RoomCaptureView` overlay with "Done" button
- `captured`: Show room dimensions summary (e.g., "4.2m × 5.8m, 2.7m ceiling"), "Upload" and "Rescan" buttons
- `uploading`: Spinner + "Uploading scan data…"
- `uploaded`: Brief checkmark animation → auto-polls state → transitions
- `failed`: Error text + "Retry" button

**App backgrounding (G17)**: Monitor `scenePhase` via `@Environment(\.scenePhase)`. If app resigns active during `.scanning`, transition to `.failed("Scan interrupted. Please try again.")`. ARKit sessions cannot reliably resume after backgrounding.

### A4: RoomCaptureView Integration

**New files**:
- `RoomCaptureCoordinator.swift` — `NSObject` conforming to `RoomCaptureViewDelegate` + `RoomCaptureSessionDelegate`
- `RoomCaptureViewWrapper.swift` — `UIViewRepresentable` wrapping Apple's `RoomCaptureView`

**Coordinator flow**:
1. `captureSession.run(configuration:)` starts scanning
2. `captureView(_:didPresent:)` — real-time room model updates
3. User taps "Done" → `captureSession.stop()`
4. `captureSession(_:didEndWith:error:)`:
   - If `error == nil` → extract `CapturedRoom` → state = `.captured`
   - If `error != nil` → state = `.failed(error.localizedDescription)` — user sees "Retry" or "Skip"
5. User taps "Upload" → `ScanUploader.upload()` → state = `.uploaded`

**Error classification**:
- Retryable: tracking lost, insufficient coverage → show "Retry" button
- Fatal: ARKit unavailable, hardware failure → show "Skip Scan" only

**Required entitlements**: Camera access (`NSCameraUsageDescription` — already in Info.plist for photo capture).

**Required `Package.swift` change** (G9):
```swift
targets: [
    .target(
        name: "RemoLiDAR",
        dependencies: ["RemoModels", "RemoNetworking"],
        linkerSettings: [
            .linkedFramework("ARKit"),
            .linkedFramework("RoomPlan"),
        ]
    ),
]
```

### A5: Manual Device Test Protocol

Run on **iPhone Pro (12 Pro or later)**:

1. Launch app pointed at real backend (`-real-backend true -backend-url http://localhost:8000`)
2. Create project → upload 2 room photos → arrive at Scan screen
3. Tap "Start Scanning" → walk around the room slowly until RoomPlan shows coverage
4. Tap "Done" → verify dimensions summary shows reasonable values
5. Tap "Upload" → verify backend logs `lidar_parsed` with correct dimensions
6. Verify generation prompt in Temporal UI includes room context
7. Complete full flow → verify shopping list shows LiDAR badge (no "non-LiDAR" banner)

**Acceptance criteria**:
- Scanned dimensions within **±10%** of physical measurement (tape measure)
- Furniture detected in `furniture[]` matches what's actually in the room
- Generation prompt includes room context string
- Shopping `has_lidar=true` in output

---

## Phase B: Fixture Capture & Replay (Testing Only — Demo Once, Automate Forever)

> **This entire phase is for automated testing only.** Real users always get the real
> RoomCaptureView (Phase A). The fixture mechanism exists so Maestro and CI can exercise
> the LiDAR-present code path on simulators without physical hardware.
>
> **Core idea**: You run a real LiDAR scan once on a physical device. The app saves the
> raw RoomPlan JSON as a fixture file. All subsequent Maestro runs use that fixture — no
> LiDAR hardware needed. The fixture launch argument is NEVER set in production builds.

### File location flowchart

```
Source of truth (committed):
  ios/.maestro/fixtures/reference_room.json    ← hand-written, valid backend schema
  ios/.maestro/fixtures/captured_room.json     ← real device capture (after Phase B1)

Bundled into app (Debug only, via project.yml):
  ios/Remo/Resources/reference_room.json       ← copy of source of truth

Runtime:
  App bundle → loadFixture(named:) reads from Bundle.main
  Launch arg: -lidar-fixture "reference_room" → loads reference_room.json from bundle
```

### B1: Fixture Capture (One-Time, on Real Device)

> **Purpose**: Record real RoomPlan output once so Maestro can replay it forever.
> This is NOT part of the production flow — it's a developer tool for generating test data.

**What to add**: When a debug/dev launch argument is present, save the exported scan JSON to a known location after a successful scan.

**File**: `LiDARScanScreen.swift` — after `RoomPlanExporter.export()` succeeds:

```swift
// In startScan(), after export:
let scanData = RoomPlanExporter.export(result.capturedRoom)

// DEBUG-ONLY: Save fixture for Maestro test replay.
// This launch arg is only set by developers who want to capture test data.
// It is never set in production, CI, or Maestro flows.
#if DEBUG
if UserDefaults.standard.bool(forKey: "capture-lidar-fixture") {
    if let jsonData = try? JSONSerialization.data(withJSONObject: scanData, options: .prettyPrinted) {
        let docsDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first!
        let fixturePath = docsDir.appendingPathComponent("lidar_fixture.json")
        try? jsonData.write(to: fixturePath)
        print("LiDAR fixture saved to: \(fixturePath.path)")
    }
}
#endif
```

**Capture steps (you do this once)**:
1. Build to physical iPhone Pro with `-capture-lidar-fixture true`
2. Run through flow → do real scan → app saves `lidar_fixture.json` to Documents
3. Pull fixture off device:
   ```bash
   # For real device — use Xcode → Window → Devices → Download Container
   # Navigate to AppData/Documents/lidar_fixture.json
   ```
4. Copy the JSON file to `ios/.maestro/fixtures/captured_room.json`
5. Also copy to `ios/Remo/Resources/captured_room.json` (bundle resource)
6. Commit both — it's just room dimensions, no PII

### B2: Fixture Injection via Launch Argument ✅ DONE

> **DONE** — Added `fixtureMode` computed property, `#if DEBUG` fixture branch in `startScan()`,
> and `loadFixture(named:)` static helper. When `-lidar-fixture "reference_room"` launch arg is set,
> "Start Scanning" loads bundled fixture JSON instead of using mock data. `#if DEBUG` guard strips
> fixture code from release builds. Swift compilation verified, 1192 backend tests pass.

**File**: `LiDARScanScreen.swift`

Add a new launch argument `lidar-fixture` that, when set, makes "Start Scanning" load the bundled fixture instead of launching RoomCaptureView:

```swift
// IMPORTANT: fixtureMode is for TESTING ONLY.
// In production, lidar-fixture is never set — users always get real RoomCaptureView.
// The #if DEBUG guard ensures fixture code is stripped from release builds.
private var fixtureMode: Bool {
    #if DEBUG
    return UserDefaults.standard.string(forKey: "lidar-fixture") != nil
    #else
    return false  // Never use fixtures in production — always real scan
    #endif
}

private func startScan() async {
    guard let projectId = projectState.projectId else { ... }
    isScanning = true
    defer { isScanning = false }

    do {
        let scanData: [String: Any]

        #if DEBUG
        if let fixtureName = UserDefaults.standard.string(forKey: "lidar-fixture") {
            // TEST-ONLY: Load saved fixture JSON for Maestro/CI automated testing.
            // This bypasses RoomCaptureView entirely — the fixture is a snapshot
            // of real RoomPlan output captured once from a physical device (Phase B1)
            // or a hand-written reference (Phase B3).
            scanData = try loadFixture(named: fixtureName)
        } else {
            // PRODUCTION PATH: Real RoomCaptureView flow (Phase A)
            let result = try await presentRoomCapture()
            scanData = RoomPlanExporter.export(result.capturedRoom)
        }
        #else
        // RELEASE BUILDS: Always use real RoomCaptureView — no fixture bypass
        let result = try await presentRoomCapture()
        scanData = RoomPlanExporter.export(result.capturedRoom)
        #endif

        try await client.uploadScan(projectId: projectId, scanData: scanData)
        let newState = try await client.getState(projectId: projectId)
        projectState.apply(newState)
    } catch {
        errorMessage = error.localizedDescription
    }
}

#if DEBUG
private func loadFixture(named name: String) throws -> [String: Any] {
    // TEST-ONLY: Look in app bundle for fixture JSON
    guard let url = Bundle.main.url(forResource: name, withExtension: "json"),
          let data = try? Data(contentsOf: url),
          let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        throw NSError(domain: "LiDAR", code: 1,
                      userInfo: [NSLocalizedDescriptionKey: "Fixture '\(name)' not found in bundle"])
    }
    return json
}
#endif
```

**Safety**: The `#if DEBUG` compile-time guard means fixture code is completely stripped from release/App Store builds. Even if someone set the `lidar-fixture` launch argument on a production build, it would be ignored — `fixtureMode` returns `false` unconditionally in release.

**Bundle resource setup** — add to `ios/project.yml` (uses xcodegen):
```yaml
targets:
  Remo:
    sources:
      - path: Remo/Resources/reference_room.json
        buildPhase: resources
        configurations: [Debug]
```

This includes the fixture in Debug builds only. Release/App Store builds exclude it.

### B3: Fallback — Hardcoded Reference Fixture ✅ DONE

> **DONE** — Created `ios/.maestro/fixtures/reference_room.json` and `ios/Remo/Resources/reference_room.json`.
> Updated `ios/project.yml` to include resource. Backend test `TestReferenceFixture::test_reference_fixture_parses`
> validates the fixture against the parser (1192 tests pass). Fixture matches exact schema from plan.

Before you do a real scan (or if the device isn't available), create a **reference fixture** by hand that matches the backend's expected schema. This lets Maestro test the LiDAR-present path immediately without real hardware:

**File**: `ios/.maestro/fixtures/reference_room.json` (and copy to `ios/Remo/Resources/reference_room.json`)

```json
{
  "room": { "width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters" },
  "walls": [
    { "id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0.0 },
    { "id": "wall_1", "width": 5.8, "height": 2.7, "orientation": 90.0 },
    { "id": "wall_2", "width": 4.2, "height": 2.7, "orientation": 180.0 },
    { "id": "wall_3", "width": 5.8, "height": 2.7, "orientation": 270.0 }
  ],
  "openings": [
    { "type": "door", "wall_id": "wall_0", "width": 0.9, "height": 2.1, "position": { "x": 1.5 } },
    { "type": "window", "wall_id": "wall_1", "width": 1.5, "height": 1.2, "position": { "x": 2.0 } }
  ],
  "furniture": [
    { "type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8 },
    { "type": "table", "width": 1.2, "depth": 0.8, "height": 0.75 },
    { "type": "chair", "width": 0.5, "depth": 0.5, "height": 0.9 }
  ],
  "surfaces": [
    { "type": "floor" }
  ],
  "floor_area_sqm": 24.36
}
```

Note: `surfaces[].material` omitted (RoomPlan doesn't detect materials — G20). The backend parser handles missing fields gracefully.

This is valid input — the backend parser already handles exactly this shape (77 tests confirm it).

---

## Phase C: Maestro Automated Testing with LiDAR Data ✅ DONE

> **DONE** — Created all three Maestro flows: `02-lidar-scan.yaml` (fixture-based scan step),
> `happy-path-lidar.yaml` (full LiDAR happy path with `-lidar-fixture "reference_room"` launch arg),
> and `07-output-verify-lidar.yaml` (LiDAR-specific output verification with `assertNotVisible` for
> non-LiDAR banner). Note: `happy-path-lidar.yaml` omits `real-backend` / `backend-url` args since
> it works with the mock client like the standard happy path. Flows are ready to run once
> the app is installed on a simulator.

### C1: New Maestro Flow — `02-lidar-scan.yaml`

Replaces `02-skip-scan.yaml` in the LiDAR happy path. Uses the fixture injection launch argument so no real hardware is needed:

**File**: `ios/.maestro/flows/02-lidar-scan.yaml`

```yaml
appId: com.hippogriff.remo
---
# LiDARScanScreen: upload fixture scan data (no real LiDAR needed)
- assertVisible: "Scan Your Room"
- tapOn:
    id: "scan_start"
# Fixture data auto-submitted via -lidar-fixture launch arg
# Wait for the scan to upload and state to transition
- extendedWaitUntil:
    visible: "Design Style"
    timeout: 10000
- takeScreenshot: 02-lidar-scan
```

### C2: New Happy Path Variant — `happy-path-lidar.yaml`

A second happy path that uses LiDAR scan instead of skip:

**File**: `ios/.maestro/flows/happy-path-lidar.yaml`

```yaml
appId: com.hippogriff.remo
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
      real-backend: "true"
      backend-url: "http://localhost:8000"
      lidar-fixture: "reference_room"
    permissions:
      photos: allow
      camera: allow
- runFlow: 01-create-project.yaml
- runFlow: 02-lidar-scan.yaml          # <-- LiDAR instead of skip
- runFlow: 03-intake-chat.yaml
- runFlow: 04-select-design.yaml
- runFlow: 05-iterate-text.yaml
- runFlow: 06-approve-design.yaml
- runFlow: 07-output-verify-lidar.yaml  # <-- Verify LiDAR-specific UI
```

### C3: LiDAR-Specific Output Verification — `07-output-verify-lidar.yaml`

Verifies that the shopping list shows LiDAR data indicators (no "non-LiDAR" banner):

**File**: `ios/.maestro/flows/07-output-verify-lidar.yaml`

```yaml
appId: com.hippogriff.remo
---
# On output/shopping screen: verify LiDAR badge is present, no non-LiDAR banner
- assertNotVisible: "Scan your room with LiDAR"   # non-LiDAR banner should NOT appear
- takeScreenshot: 07-output-lidar
```

### C4: Maestro Commands

```bash
# Standard happy path (skip scan, no LiDAR)
maestro test ios/.maestro/flows/happy-path.yaml

# LiDAR happy path (fixture-based, needs real backend running)
maestro test ios/.maestro/flows/happy-path-lidar.yaml

# Just the scan step (for quick iteration)
maestro test ios/.maestro/flows/02-lidar-scan.yaml
```

### C5: CI Integration

Both happy paths should run in CI. The standard path uses mock backend; the LiDAR path uses real backend + mock activities:

```yaml
# In .github/workflows/ci.yml (Maestro job)
- name: Maestro — Standard Happy Path
  run: maestro test ios/.maestro/flows/happy-path.yaml

- name: Maestro — LiDAR Happy Path (fixture)
  env:
    USE_MOCK_ACTIVITIES: "true"
  run: maestro test ios/.maestro/flows/happy-path-lidar.yaml
```

---

## Phase D: LangSmith Tracing (Observability) ✅ DONE

> **DONE** — Created `backend/app/utils/tracing.py` with zero-cost wrappers (`wrap_anthropic` + `traceable`).
> Wrapped Anthropic clients in 4 activity files: `shopping.py`, `intake.py`, `analyze_room.py`, `validation.py`.
> Added `langsmith>=0.2,<1` to dev dependencies in `pyproject.toml`. 5 tests in `test_tracing.py` verify
> no-op behavior when disabled AND graceful fallback when `LANGSMITH_API_KEY` is set but langsmith is not
> installed (ImportError guard). 1197 backend tests pass, lint/format/mypy clean.
> Review fix: rewrote tracing.py with lazy evaluation (env var checked per-call, not at import time)
> + `try/except` ImportError guard. Prevents production crash if LANGSMITH_API_KEY set without langsmith installed.
> Note: Pipeline step tagging (Step 4) deferred — requires deeper function extraction that's out of scope
> for this branch. The client wrapping (Steps 1-3) provides automatic LLM call tracing.

### Step 1: Add dependency

**File**: `backend/pyproject.toml` — add to dev dependencies:
```toml
[project.optional-dependencies]
dev = [
    # ... existing deps ...
    "langsmith>=0.2,<1",
]
```

### Step 2: Add tracing wrapper module

**File**: `backend/app/utils/tracing.py` (new, T0 scope)

```python
"""LangSmith tracing for LLM calls — zero-cost when LANGSMITH_API_KEY is unset."""
from __future__ import annotations

import os

_ENABLED = bool(os.environ.get("LANGSMITH_API_KEY"))

if _ENABLED:
    from langsmith import traceable as _traceable
    from langsmith.wrappers import wrap_anthropic as _wrap_anthropic
else:
    def _traceable(**kwargs):
        def decorator(fn):
            return fn
        return decorator

    def _wrap_anthropic(client):
        return client


def wrap_anthropic(client):
    """Wrap Anthropic client for auto-tracing. No-op without LANGSMITH_API_KEY."""
    return _wrap_anthropic(client)


def traceable(**kwargs):
    """Decorator for tracing arbitrary functions. No-op without LANGSMITH_API_KEY."""
    return _traceable(**kwargs)
```

### Step 3: Wrap LLM clients at call sites

Each LLM call site creates its own client. Wrap with the tracing wrapper:

| Call site | Change | Notes |
|-----------|--------|-------|
| `shopping.py` extraction (~line 200) | `client = wrap_anthropic(anthropic.AsyncAnthropic(...))` | Item extraction from design image |
| `shopping.py` scoring (~line 565) | `client = wrap_anthropic(anthropic.AsyncAnthropic(...))` | Product scoring with room constraints |
| `generate.py` Gemini (~line 200) | `@traceable(name="gemini_generate_design", run_type="llm")` | Image generation |
| `intake.py` intake agent | `client = wrap_anthropic(anthropic.AsyncAnthropic(...))` | Design brief conversation |
| `analyze_room.py` room analysis | `client = wrap_anthropic(anthropic.AsyncAnthropic(...))` | **Designer Brain** — eager photo analysis (PR #10) |
| `validation.py` photo validation | `client = wrap_anthropic(anthropic.Anthropic(...))` | Sync photo quality check |

### Step 4: Add pipeline step tagging

```python
@traceable(name="shopping_step1_extraction", run_type="chain")
async def extract_items(...): ...

@traceable(name="shopping_step2_search", run_type="chain")
async def search_all_items(...): ...

@traceable(name="shopping_step3_scoring", run_type="chain")
async def score_all_products(...): ...

@traceable(name="shopping_step4_dimension_filter", run_type="chain")
def filter_by_dimensions(...): ...

@traceable(name="shopping_step5_confidence_filter", run_type="chain")
def apply_confidence_filtering(...): ...
```

Creates hierarchy in LangSmith:
```
generate_shopping_list (root)
  ├── shopping_step1_extraction
  │     └── claude_messages_create (auto-traced)
  ├── shopping_step2_search
  ├── shopping_step3_scoring
  │     └── claude_messages_create × N
  ├── shopping_step4_dimension_filter
  └── shopping_step5_confidence_filter
```

---

## Execution Order

```
Phase A0 — Fix G1 (JSON key) + G2 (EditDesignInput) — no device needed
  │
Phase B3 — Create reference fixture JSON (no device needed, unblocks Maestro)
  │
  ├── Phase B2 — Fixture injection launch argument in LiDARScanScreen
  │     │
  │     └── Phase C1-C4 — Maestro flows (testable on simulator immediately)
  │
Phase A1-A4 — Real RoomPlan integration (needs iPhone Pro, parallel with B/C)
  │
  └── Phase B1 — Fixture capture from real device (replaces B3 reference fixture)
  │
  └── Phase A5 — Manual device test protocol
  │
Phase D — LangSmith tracing (independent, do anytime)
```

**Key insight**: A0 + B3 + B2 + C can be done *before* any real LiDAR hardware work. The reference fixture gives Maestro a valid LiDAR-present path to test against. Once you do a real scan (Phase A + B1), replace the reference fixture with real captured data.

**Production vs Testing boundary**:
- Phase A = real user code path. Always active. Uses real RoomCaptureView.
- Phase B + C = testing infrastructure only. Guarded by `#if DEBUG`. Stripped from release builds.
- The two paths share the same backend pipeline (`POST /scan` → parser → workflow → generation/shopping). The only difference is where the JSON comes from: real ARKit vs fixture file.

---

## Files to Create/Modify

| File | Action | Phase |
|------|--------|-------|
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift` | Modify (fix G1 JSON key) | A0 |
| `ios/Packages/RemoNetworking/Sources/RemoNetworking/MockWorkflowClient.swift` | Modify (fix G1 JSON key) | A0 |
| `backend/app/models/contracts.py` | Modify (G2: add `room_dimensions` to `EditDesignInput`) | A0 |
| `backend/app/workflows/design_project.py` | Modify (G2: wire `room_dimensions` in `_edit_input()`) | A0 |
| `backend/tests/test_e2e.py` | Modify (G3: add scan data E2E test) | A0 |
| `ios/.maestro/fixtures/reference_room.json` | Create | B3 |
| `ios/Remo/Resources/reference_room.json` | Create (bundle resource, Debug only) | B3 |
| `ios/project.yml` | Modify (Debug-only resource) | B3 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift` | Modify (fixture loading + real scan) | A1, A3, B2 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomScanResult.swift` | Create | A2 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomPlanExporter.swift` | Create | A2 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/ScanUploader.swift` | Create | A2 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureCoordinator.swift` | Create | A4 |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureViewWrapper.swift` | Create | A4 |
| `ios/Packages/RemoLiDAR/Package.swift` | Modify (G9: add ARKit/RoomPlan deps) | A4 |
| `ios/.maestro/flows/02-lidar-scan.yaml` | Create | C1 |
| `ios/.maestro/flows/07-output-verify-lidar.yaml` | Create | C3 |
| `ios/.maestro/flows/happy-path-lidar.yaml` | Create | C2 |
| `backend/app/utils/tracing.py` | Create | D |
| `backend/pyproject.toml` | Modify | D |
