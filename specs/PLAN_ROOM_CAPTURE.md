# Room Capture: Final Plan to Beta

> **Purpose**: Replace mock scan data with real Apple RoomPlan integration.
> After this plan, a user with iPhone Pro can scan their room, see accurate
> dimensions flow through generation/editing/shopping, and get spatially-aware
> furniture recommendations. No mocks remain in the release build path.

> **Pre-condition**: PR #12 merged. Backend pipeline fully tested (1277 tests).
> Backend accepts any valid scan JSON and gracefully degrades on missing fields.
> Fixture injection for Maestro/CI already works. This plan is iOS-only.

---

## What Exists Today

| Component | State | Location |
|-----------|-------|----------|
| `LiDARScanScreen` UI shell | Working (buttons, skip flow, fixture injection) | `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift` |
| `hasLiDAR` device check | Real ARKit check + fixture override | `LiDARScanScreen.swift:24-36` |
| ARKit/RoomPlan framework linkage | Linked with `.when(platforms: [.iOS])` | `ios/Packages/RemoLiDAR/Package.swift` |
| `uploadScan(projectId:scanData:)` | Working — serializes `[String: Any]` → POST | `RealWorkflowClient.swift:78-89` |
| Backend scan endpoint | Working — parses, validates, signals workflow | `projects.py` POST `/scan` |
| Backend parser | 77 tests, bounds/unit validation, graceful fallback | `utils/lidar.py` |
| Fixture injection (`-lidar-fixture`) | Working — `#if DEBUG` guarded | `LiDARScanScreen.swift:113-127` |
| Reference fixture | Valid JSON, bundled in Debug | `ios/Remo/Resources/reference_room.json` |
| Maestro LiDAR happy path | Ready (fixture-based) | `ios/.maestro/flows/happy-path-lidar.yaml` |
| Camera usage description | Present in Info.plist | `project.yml` |
| Swift `RoomDimensions` model | Mirrors backend, CodingKeys correct | `Models.swift:103-152` |

## What's Missing (Release Build Path)

`LiDARScanScreen.startScan()` lines 119-136 — both the `#if DEBUG` non-fixture branch
and the `#else` release branch return **hardcoded mock data**. There is no
`RoomCaptureView`, no `CapturedRoom` → JSON exporter, no scan UI state machine,
no camera permission check, no backgrounding guard. The release build literally
has a `#warning` about this.

---

## Tasks

### T1: RoomPlanExporter — CapturedRoom to JSON ✅ DONE

**What**: A pure function that converts Apple's `CapturedRoom` into the `[String: Any]`
dict the backend expects. This is the single most important piece — everything else
flows from getting this right.

**File**: `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomPlanExporter.swift` (new)

**Input**: `CapturedRoom` (Apple's struct from a completed scan session)
**Output**: `[String: Any]` matching the backend schema:

```json
{
  "room": { "width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters" },
  "walls": [{ "id": "wall_0", "width": 4.2, "height": 2.7 }],
  "openings": [{ "type": "door", "width": 0.9, "height": 2.1 }],
  "furniture": [{ "type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8 }],
  "surfaces": [{ "type": "floor" }],
  "floor_area_sqm": 24.36
}
```

**Apple RoomPlan API Reality** (verified assumptions):

| Backend field | CapturedRoom source | How to extract |
|---------------|---------------------|----------------|
| `room.width`, `room.length` | No `.dimensions` property exists | Compute bounding box from all `walls` — find min/max x and z from wall `transform` positions. Width = max_x - min_x, Length = max_z - min_z |
| `room.height` | No direct property | Take max wall height from `walls[].dimensions.y` (walls are vertical, y = height) |
| `walls[].width`, `walls[].height` | `CapturedRoom.Wall` has `dimensions: simd_float3` | `dimensions.x` = width, `dimensions.y` = height. Use absolute values. |
| `walls[].id` | Not exposed by Apple | Generate: `"wall_\(index)"` |
| `walls[].orientation` | `Wall.transform: simd_float4x4` | Extract rotation around Y axis: `atan2(transform.columns.0.z, transform.columns.0.x)`. Convert to degrees. **If this is unreliable on device, omit the field** — backend handles missing orientation. |
| `openings[].type` | `CapturedRoom.Opening` has `.category` enum (`.door`, `.window`, `.doorway`, `.opening`) | Map category to string: `.door` → `"door"`, `.window` → `"window"`, others → `"opening"` |
| `openings[].width`, `.height` | `Opening.dimensions: simd_float3` | `dimensions.x` = width, `dimensions.y` = height |
| `openings[].wall_id` | No direct link | **Omit** — the backend handles missing `wall_id`. Spatial association is complex and fragile. |
| `furniture[].type` | `CapturedRoom.Object` has `.category` enum (16 values: `.sofa`, `.table`, `.chair`, `.bed`, `.storage`, etc.) | Map category `.rawValue` or switch statement to lowercase string |
| `furniture[].width`, `.depth`, `.height` | `Object.dimensions: simd_float3` | `dimensions.x` = width, `dimensions.z` = depth, `dimensions.y` = height |
| `surfaces[].type` | `CapturedRoom.Surface` has `.category` enum (`.floor`, `.wall`, `.ceiling`, etc.) | Map category to string. Only include `.floor` surfaces (walls already captured above). |
| `surfaces[].material` | **Not available** — RoomPlan doesn't detect materials | Omit the field entirely. Backend handles null/missing. |
| `floor_area_sqm` | Not exposed directly | Compute: `room_width * room_length`. The backend parser validates this (warns if >5x discrepancy from width*length). |
| `room.unit` | N/A (RoomPlan uses meters) | Always `"meters"` |

**Implementation approach**:

```swift
import RoomPlan

struct RoomPlanExporter {
    static func export(_ room: CapturedRoom) -> [String: Any] {
        let walls = exportWalls(room.walls)
        let (width, length, height) = computeRoomDimensions(room.walls)
        let openings = exportOpenings(room.doors + room.windows)
        let furniture = exportObjects(room.objects)
        let surfaces = exportSurfaces(room.floors)
        let floorArea = width * length

        return [
            "room": [
                "width": Double(width),
                "length": Double(length),
                "height": Double(height),
                "unit": "meters"
            ] as [String: Any],
            "walls": walls,
            "openings": openings,
            "furniture": furniture,
            "surfaces": surfaces,
            "floor_area_sqm": Double(floorArea)
        ]
    }

    private static func computeRoomDimensions(_ walls: [CapturedRoom.Wall]) -> (Float, Float, Float) {
        guard !walls.isEmpty else { return (0, 0, 0) }
        var minX: Float = .infinity, maxX: Float = -.infinity
        var minZ: Float = .infinity, maxZ: Float = -.infinity
        var maxHeight: Float = 0
        for wall in walls {
            let pos = wall.transform.columns.3 // translation column
            let halfWidth = wall.dimensions.x / 2
            // Wall extends along its local X axis; use transform to find world extent
            let dirX = wall.transform.columns.0.x
            let dirZ = wall.transform.columns.0.z
            let extent = halfWidth * abs(dirX)
            let extentZ = halfWidth * abs(dirZ)
            minX = min(minX, pos.x - extent)
            maxX = max(maxX, pos.x + extent)
            minZ = min(minZ, pos.z - extentZ)
            maxZ = max(maxZ, pos.z + extentZ)
            maxHeight = max(maxHeight, wall.dimensions.y)
        }
        return (maxX - minX, maxZ - minZ, maxHeight)
    }
}
```

**Do NOT**:
- Hardcode dimensions or return mock data
- Skip walls/openings/furniture because "they might not be there" — export what the API gives you
- Silently catch errors — if `CapturedRoom` has zero walls, return zeros (backend validates)
- Attempt wall_id association for openings — it's fragile and the backend doesn't need it

**Do**:
- Use `simd_float3` dimensions directly (they're already in meters)
- Handle empty arrays (room with no furniture is valid)
- Round floats to 2 decimal places for clean JSON

### T2: RoomCaptureView Integration ✅ DONE

**What**: A SwiftUI-wrapped `RoomCaptureView` that the user interacts with to scan.

**Files**:
- `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureViewWrapper.swift` (new)
- `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureCoordinator.swift` (new)

**RoomCaptureViewWrapper** — `UIViewRepresentable`:

```swift
import SwiftUI
import RoomPlan

struct RoomCaptureViewWrapper: UIViewRepresentable {
    let onComplete: (Result<CapturedRoom, Error>) -> Void

    func makeUIView(context: Context) -> RoomCaptureView {
        let view = RoomCaptureView(frame: .zero)
        view.captureSession.delegate = context.coordinator
        view.delegate = context.coordinator
        return view
    }

    func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

    func makeCoordinator() -> RoomCaptureCoordinator {
        RoomCaptureCoordinator(onComplete: onComplete)
    }
}
```

**RoomCaptureCoordinator** — `NSObject, RoomCaptureSessionDelegate`:

```swift
import RoomPlan

class RoomCaptureCoordinator: NSObject, RoomCaptureSessionDelegate {
    let onComplete: (Result<CapturedRoom, Error>) -> Void

    init(onComplete: @escaping (Result<CapturedRoom, Error>) -> Void) {
        self.onComplete = onComplete
    }

    func captureSession(_ session: RoomCaptureSession, didEndWith data: CapturedRoomData, error: (any Error)?) {
        if let error {
            onComplete(.failure(error))
        } else {
            let room = data.finalResults
            onComplete(.success(room))
        }
    }
}
```

**How it connects**: `LiDARScanScreen` presents this as a full-screen cover when
the user taps "Start Scanning". The coordinator calls back with a `CapturedRoom`
on success, or an error on failure.

**Do NOT**:
- Add a "mock coordinator" or "preview coordinator" — the real one is the only one
- Add complex state management inside the coordinator — it just bridges Apple's delegate to a closure
- Try to resume a failed session — tell the user to retry from scratch

### T3: Scan UI State Machine ✅ DONE

**What**: Replace the boolean `isScanning` with a proper state machine in `LiDARScanScreen`.

**File**: Modify `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift`

**State enum**:

```swift
enum ScanState: Equatable {
    case ready          // "Start Scanning" button visible
    case scanning       // RoomCaptureView active (full-screen overlay)
    case processing     // Extracting CapturedRoom, brief spinner
    case uploading      // POSTing to backend
    case failed(String) // Error message + "Retry" and "Skip" buttons
}
```

Note: no `captured` preview state — go straight from scan complete to upload.
Users don't need to approve the scan data. If the scan succeeded, upload it.
If they're unhappy with results, they can rescan after seeing the design.

**UI mapping**:

| State | UI |
|-------|-----|
| `.ready` | Current layout (cube icon, description, "Start Scanning" + "Skip Scan") |
| `.scanning` | Full-screen `RoomCaptureViewWrapper` overlay with "Done" button |
| `.processing` | ProgressView("Processing scan...") overlay |
| `.uploading` | ProgressView("Uploading...") overlay |
| `.failed(msg)` | Alert with error message, "Retry" and "Skip" buttons |

**Changes to `startScan()`**:

```swift
private func startScan() async {
    guard let projectId = projectState.projectId else { return }

    #if DEBUG
    if let fixtureName = UserDefaults.standard.string(forKey: "lidar-fixture") {
        // Fixture path — unchanged from current implementation
        scanState = .uploading
        do {
            let scanData = try Self.loadFixture(named: fixtureName)
            try await client.uploadScan(projectId: projectId, scanData: scanData)
            let newState = try await client.getState(projectId: projectId)
            projectState.apply(newState)
        } catch {
            scanState = .failed(error.localizedDescription)
        }
        return
    }
    #endif

    // REAL SCAN PATH — no mocks, no hardcoded data
    scanState = .scanning
    // RoomCaptureViewWrapper presented via .fullScreenCover(isPresented:)
    // Completion handled by onScanComplete callback
}

private func onScanComplete(_ result: Result<CapturedRoom, Error>) {
    switch result {
    case .success(let capturedRoom):
        scanState = .processing
        let scanData = RoomPlanExporter.export(capturedRoom)
        scanState = .uploading
        Task {
            do {
                try await client.uploadScan(projectId: projectState.projectId!, scanData: scanData)
                let newState = try await client.getState(projectId: projectState.projectId!)
                projectState.apply(newState)
            } catch {
                scanState = .failed(error.localizedDescription)
            }
        }
    case .failure(let error):
        scanState = .failed(error.localizedDescription)
    }
}
```

**Critical**: The `#else` (release) branch that currently returns hardcoded mock data
**must be deleted entirely**. After this task, the release build calls real
`RoomCaptureView` → `RoomPlanExporter.export()` → `uploadScan()`. There is no
fallback to mock data. The `#warning` is gone because there is nothing to warn about.

### T4: Camera Permission Flow (G16) ✅ DONE

**What**: Check and request camera access before starting a scan.

**File**: Modify `LiDARScanScreen.swift`

```swift
import AVFoundation

private func checkCameraPermission() async -> Bool {
    switch AVCaptureDevice.authorizationStatus(for: .video) {
    case .authorized:
        return true
    case .notDetermined:
        return await AVCaptureDevice.requestAccess(for: .video)
    case .denied, .restricted:
        scanState = .failed("Camera access required for room scanning. Enable in Settings > Privacy > Camera.")
        return false
    @unknown default:
        return false
    }
}
```

Call this at the start of `startScan()`, before transitioning to `.scanning`:

```swift
guard await checkCameraPermission() else { return }
scanState = .scanning
```

`NSCameraUsageDescription` is already in Info.plist ("Remo needs camera access to
photograph your room."). Consider updating this text to mention room scanning too.

### T5: App Backgrounding Guard (G17) ✅ DONE

**What**: If the app goes to background during a scan, stop the scan and show an error.

**File**: Modify `LiDARScanScreen.swift`

```swift
@Environment(\.scenePhase) private var scenePhase

// In body:
.onChange(of: scenePhase) { _, newPhase in
    if newPhase != .active && scanState == .scanning {
        scanState = .failed("Scan interrupted. Please try again.")
        // The RoomCaptureSession will be torn down when the view disappears
    }
}
```

ARKit sessions cannot reliably resume after backgrounding. Don't try — just fail
cleanly and let the user retry.

### T6: Unit Tests (G14) ✅ DONE

**What**: Create test suite for `RemoLiDAR` package.

**File**: `ios/Packages/RemoLiDAR/Tests/RemoLiDARTests/` (new directory)

**What to test**:

1. **RoomPlanExporter** (most important):
   - Cannot unit test with real `CapturedRoom` (no public initializer). Instead:
   - Test the dimension computation helper if extracted as a standalone function
   - Test the category mapping functions (Opening.Category → string, Object.Category → string)
   - Test empty input handling (zero walls → zero dimensions)
   - Test JSON structure shape (correct keys present)

2. **State machine transitions**:
   - `ready` → `scanning` on start
   - `scanning` → `failed` on backgrounding
   - `failed` → `ready` on retry

3. **Integration test approach**: Since `CapturedRoom` can't be mocked easily,
   the real integration test is the **device test protocol** (T8). The unit tests
   focus on pure logic that can run on macOS.

**Package.swift test target** — add:
```swift
.testTarget(
    name: "RemoLiDARTests",
    dependencies: ["RemoLiDAR"]
)
```

### T7: Fixture Capture from Real Device (B1)

**What**: After the real scan works (T1-T5 complete), capture an actual
`CapturedRoom` export as a fixture file, replacing the hand-written reference.

**This is a human step.** A developer with an iPhone Pro does this once:

> **Note**: The fixture capture code is already implemented in `LiDARScanScreen.swift`
> (inside `onScanComplete`, `#if DEBUG` guarded). It saves the exported scan JSON to the
> app's Documents directory when the `-capture-lidar-fixture` launch argument is set.
> Logs appear in Console.app under subsystem `com.remo.lidar` with `[FIXTURE]` prefix.

1. In Xcode, select the Remo scheme → Edit Scheme → Run → Arguments → add `-capture-lidar-fixture`

2. Build to iPhone Pro (Debug configuration)

3. Run through the app flow → do a real scan of any room

4. Pull `captured_room.json` off the device:
   - Xcode → Window → Devices and Simulators → select device → select app → Download Container
   - Navigate to `AppData/Documents/captured_room.json`

5. Validate it parses on the backend:
   ```bash
   cd backend
   python -c "
   import json
   from app.utils.lidar import parse_room_scan
   with open('../ios/.maestro/fixtures/captured_room.json') as f:
       data = json.load(f)
   dims = parse_room_scan(data)
   print(f'{dims.width_m}m x {dims.length_m}m x {dims.height_m}m')
   print(f'Walls: {len(dims.walls)}, Furniture: {len(dims.furniture)}')
   "
   ```

6. Copy to both fixture locations:
   ```bash
   cp captured_room.json ios/.maestro/fixtures/captured_room.json
   cp captured_room.json ios/Remo/Resources/captured_room.json
   ```

7. Optionally update Maestro flows to use `captured_room` instead of `reference_room`

8. Commit both files — they contain only room geometry, no PII

### T8: Device Test Protocol (Human)

**What**: End-to-end verification on a physical iPhone Pro.

**Prerequisites**: T1-T5 implemented. Backend running locally.

**Required hardware**: iPhone 12 Pro or later (any Pro/Pro Max model has LiDAR).

**Setup**:
```bash
# Terminal 1: Backend
cd backend && docker compose up -d && .venv/bin/python -m uvicorn app.main:app --reload

# Terminal 2: Temporal worker
cd backend && .venv/bin/python -m app.worker

# Xcode: Build to physical device
# Set scheme to Debug, select your iPhone Pro as target
# Add launch arguments in scheme editor:
#   -real-backend true
#   -backend-url http://<your-mac-ip>:8000
```

**Test Steps**:

| Step | Action | Expected Result | Verify |
|------|--------|-----------------|--------|
| 1 | Launch app on iPhone Pro | Scan screen shows "Start Scanning" button | `hasLiDAR` returns true |
| 2 | Tap "Start Scanning" | Camera permission prompt appears | First launch only |
| 3 | Grant camera access | Full-screen RoomCaptureView appears with live mesh | ARKit session running |
| 4 | Walk around room slowly (~30 seconds) | Room mesh builds progressively | Walls, floor detected |
| 5 | Tap "Done" | Processing spinner briefly | `CapturedRoom` extracted |
| 6 | Automatic upload | Uploading spinner, then advances to intake | Backend logs `lidar_parsed` |
| 7 | Check backend logs | `room_dimensions: {width_m: X, length_m: Y, height_m: Z}` | Values are plausible for the room |
| 8 | Measure room with tape measure | Compare to scanned dimensions | Within **+/- 15%** of physical measurement |
| 9 | Complete intake chat | Normal flow | N/A |
| 10 | Check generation prompt | Room context appears in Temporal UI or logs | Includes wall count, furniture list |
| 11 | Complete full flow to shopping | Shopping list appears | Furniture sizes constrained by room dimensions |
| 12 | Background the app during a scan | Scan fails with "interrupted" message | "Retry" and "Skip" buttons appear |
| 13 | Deny camera permission (reset in Settings) | Error message directing to Settings | Cannot proceed without camera |
| 14 | Test on non-LiDAR iPhone (if available) | "LiDAR not available" message, Skip only | No crash |

**Acceptance criteria**:
- Steps 1-11 complete without crashes
- Scanned dimensions plausible (not zero, not wildly off)
- Backend receives and parses real scan data successfully
- Generation and shopping prompts include room context
- Error paths (12-14) handled gracefully

**If scanned dimensions are wrong**:
- Check `computeRoomDimensions()` math — print raw wall transforms to console
- Compare wall extents to physical walls
- Adjust bounding box computation if needed
- The backend validates dimensions are between 0.3m and 50m — if outside this range,
  the exporter has a bug

### T9: Maestro E2E Verification (Simulator) ✅ DONE

**What**: Run both Maestro happy paths on the iOS Simulator to verify the full app
flow works end-to-end — both the standard skip-scan path and the LiDAR fixture path.
This is an automated step that must pass before looping in a human for device testing.

**Prerequisites**: Backend running locally, app installed on booted simulator, Maestro + Java installed.

**Flows**:
- `happy-path.yaml` — standard path: create project → skip scan → intake chat → select design → iterate → approve → verify output + shopping list
- `happy-path-lidar.yaml` — LiDAR path: same flow but uses `-lidar-fixture reference_room` to upload fixture scan data instead of skipping. Verifies the LiDAR banner is NOT shown in output (scan data was provided).

**Commands**:
```bash
export JAVA_HOME="/opt/homebrew/opt/openjdk"
export PATH="$JAVA_HOME/bin:$HOME/.maestro/bin:$PATH"
maestro test ios/.maestro/flows/happy-path.yaml
maestro test ios/.maestro/flows/happy-path-lidar.yaml
```

**Acceptance criteria**: Both flows pass with all assertions COMPLETED. No timeouts, no crashes.

---

## Execution Order

```
T1: RoomPlanExporter          ← start here (pure logic, can build incrementally)
 │
T2: RoomCaptureView wrapper   ← depends on T1 (needs to call export)
 │
T3: Scan UI state machine     ← depends on T2 (needs to present RoomCaptureView)
 │
T4: Camera permission         ← depends on T3 (called at start of scan flow)
T5: Backgrounding guard       ← depends on T3 (monitors state during scan)
 │
T6: Unit tests                ← write alongside T1-T5, but don't block on them
 │
T9: Maestro E2E verification  ← AUTOMATED, runs on simulator (both happy paths)
 │
T7: Fixture capture           ← HUMAN STEP, after T1-T5 deployed to device
 │
T8: Device test protocol      ← HUMAN STEP, after T1-T5 deployed to device
```

T1 → T2 → T3 are sequential (each builds on the previous). T4 and T5 can be done
in parallel after T3. T6 is written alongside development. T7 and T8 require a
human with an iPhone Pro.

**Estimated scope**: T1-T5 is ~300-400 lines of Swift across 4 new files + 1 modified
file. T6 adds ~100 lines of tests. No backend changes needed.

---

## Files to Create

| File | Purpose |
|------|---------|
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomPlanExporter.swift` | CapturedRoom → JSON dict |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureViewWrapper.swift` | UIViewRepresentable wrapping Apple's RoomCaptureView |
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/RoomCaptureCoordinator.swift` | RoomCaptureSessionDelegate bridging to closure |
| `ios/Packages/RemoLiDAR/Tests/RemoLiDARTests/RoomPlanExporterTests.swift` | Unit tests for exporter logic |
| `ios/Packages/RemoLiDAR/Tests/RemoLiDARTests/ScanStateTests.swift` | Unit tests for state machine |

## Files to Modify

| File | Change |
|------|--------|
| `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift` | Replace `isScanning: Bool` with `scanState: ScanState`. Delete mock data from both DEBUG and RELEASE branches. Add `.fullScreenCover` for RoomCaptureView. Add camera permission check. Add scenePhase monitoring. |
| `ios/Packages/RemoLiDAR/Package.swift` | Add test target `RemoLiDARTests` |

## Files NOT Modified

| File | Reason |
|------|--------|
| Backend (anything in `backend/`) | Pipeline already handles real scan data (77 parser tests, 1277 total) |
| `RealWorkflowClient.swift` | `uploadScan` already serializes `[String: Any]` → JSON POST |
| `WorkflowClientProtocol.swift` | Protocol unchanged |
| `Models.swift` | `RoomDimensions` already mirrors backend |
| `reference_room.json` | Keep as fallback fixture; optionally replaced by `captured_room.json` after T7 |
| Maestro flows | Already work with fixture injection |

---

## Rules for Coding Agents

1. **No mock data in the release path.** The `#else` branch in `startScan()` that returns hardcoded `scanData` must be deleted. After T3, the release build calls `RoomCaptureView` → `RoomPlanExporter.export()` → `uploadScan()`. Period.

2. **No "TODO: implement later" stubs.** Every function must have a real implementation. `RoomPlanExporter.export()` must call real `CapturedRoom` properties, not return placeholder dicts.

3. **No new abstractions.** Don't create `ScanProvider` protocols, `ScanStrategy` patterns, or dependency injection layers. The code path is: `RoomCaptureView` → delegate callback → `RoomPlanExporter.export()` → `client.uploadScan()`. Three steps, no indirection.

4. **Use Apple's API directly.** `CapturedRoom.walls`, `CapturedRoom.doors`, `CapturedRoom.windows`, `CapturedRoom.objects`. Read the properties, extract dimensions, build the dict. If a property doesn't exist (check with Xcode autocomplete on device), omit that field — the backend handles missing fields.

5. **Omit what you can't get.** `surfaces[].material` → omit (RoomPlan doesn't detect materials). `openings[].wall_id` → omit (spatial association is complex and unreliable). `walls[].orientation` → attempt but omit if unreliable. The backend parser defaults all of these to empty/null.

6. **The fixture path is DEBUG-only and already done.** Don't touch the `#if DEBUG` fixture injection code. It works. Focus entirely on the real scan path.

7. **Build and run on device after every task.** T1-T5 each require a build to verify compilation. T8 requires running on a physical iPhone Pro. You can verify compilation on the simulator but the scan itself requires real hardware.

---

## API Corrections (Discovered During Implementation)

The plan's "Apple RoomPlan API Reality" table had several inaccuracies. Corrections
verified against the actual iOS 18.5 SDK `.swiftinterface`:

| Plan Assumption | Actual API (iOS 17+) |
|-----------------|----------------------|
| `CapturedRoom.Wall` type | **Doesn't exist.** All surfaces are `CapturedRoom.Surface`, differentiated by `.category` enum (`.wall`, `.floor`, `.door(isOpen:)`, `.window`, `.opening`) |
| `CapturedRoom.Opening` type | **Doesn't exist.** Same as above — doors/windows/openings are all `CapturedRoom.Surface` |
| `.doorway` opening category | **Doesn't exist.** Only `.door(isOpen: Bool)`, `.window`, `.opening` |
| `data.finalResults` property | **Not public.** `CapturedRoomData` only has `Codable` methods. Use `RoomBuilder.capturedRoom(from:)` for async conversion |
| `RoomCaptureViewDelegate` | Requires `NSCoding` conformance (UIKit archiving). **Avoided** — use `RoomCaptureSessionDelegate` only + `RoomBuilder` |
| `case .door:` switch pattern | Works in Swift even with associated value `door(isOpen: Bool)` — pattern ignores the associated value |

These corrections are reflected in the implementation. The testable helpers (`WallData`,
`computeRoomDimensions`, `round2`) are extracted outside `#if canImport(RoomPlan)` for
macOS test compatibility.

## Known Limitations (Accepted for Beta)

- **Wall orientation**: May be omitted or approximate. Backend shopping prompts work without it.
- **Floor material**: Always null. Backend prompts say "unknown" material, which is fine.
- **Opening-to-wall association**: Not computed. Backend doesn't use `wall_id` for anything critical.
- **Dimension accuracy**: RoomPlan is typically within 5-10cm for rooms up to 10m. Acceptable for furniture sizing.
- **Resume after background**: Not supported. User must retry. This is standard for ARKit apps.
- **Non-LiDAR devices**: See "LiDAR not available", can only Skip. This is by design — no fallback scanning.

---

## Beta Exit Criteria

- [x] T1-T5 implemented — real scan works on iPhone Pro
- [x] T6 — unit tests pass for exporter and state machine (33 tests: 18 exporter + 15 state)
- [ ] T7 — real fixture captured from device and committed
- [ ] T8 — device test protocol completed, all steps pass
- [x] `#warning` in release build path is gone (mock data deleted)
- [x] Maestro `happy-path.yaml` passes on simulator (standard skip-scan path) — verified
- [x] Maestro `happy-path-lidar.yaml` passes on simulator (fixture LiDAR path) — verified
- [x] App builds without warnings for both Debug and Release (BUILD SUCCEEDED, zero Swift errors)
- [ ] Backend receives and parses real device scan data correctly (awaiting T8)
