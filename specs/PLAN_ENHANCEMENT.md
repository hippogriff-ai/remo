# Enhancement Plan: E2E Gaps + LiDAR Implementation

> Last updated: 2026-02-12
> Owner: T0 (Platform), with T1 (iOS) collaboration on LiDAR
> Status: Draft
> Companion to: `specs/E2E_TEST_PLAN.md` (does NOT modify it)

---

## 1. Purpose & Relationship to E2E_TEST_PLAN

`E2E_TEST_PLAN.md` defines 4 prerequisites (PRE-0 through PRE-3) and 18 test scenarios (E2E-01 through E2E-18). As of this writing:

- **12 of 18 scenarios are done** at the API level (E2E-01 through E2E-10, E2E-12, E2E-18 structural)
- **5 Maestro UI scenarios are blocked** on PRE-1 (iOS backend switching)
- **1 scenario is partial** (E2E-11 error injection)
- **LiDAR scanning is 100% mock** on iOS, with a complete backend parser

This plan:
1. Catalogues every pending item from the E2E plan with a clear unblock path
2. Maps mock-to-real transition gaps across the testing pyramid
3. Provides a phased LiDAR implementation plan where agent-testable work comes first
4. Structures implementation so coding agents handle Phases A-C, with human LiDAR AR testing as the final phase

---

## 2. E2E Gap Analysis

### 2.1 Prerequisite Gaps

| ID | Item | Status | Owner | Blocker | Unblock Path |
|----|------|--------|-------|---------|--------------|
| PRE-0 | API→Temporal Bridge | **DONE** | T0 | — | `use_temporal` flag, all 17 endpoints dual-mode |
| PRE-1 | iOS Backend Switching | **NOT DONE** | T1 | No work item exists | Add launch arg / build config to `RemoApp.swift` (see Phase A) |
| PRE-2 | Infrastructure for E2E | **DONE** | T0 | — | `docker-compose.yml` + `scripts/e2e-setup.sh` |
| PRE-3 | Observability — Backend | **DONE** | T0 | — | `LOG_FILE` tee-writer, HTTP access logging |
| PRE-3 | Observability — iOS | **NOT DONE** | T1 | No work item exists | Extract `X-Request-ID` from responses, log network errors |

**PRE-1 is the critical-path blocker.** It prevents E2E-13 through E2E-17 (all Maestro UI scenarios that hit a real backend). Until iOS can switch from `MockWorkflowClient` to `RealWorkflowClient`, the entire Maestro-against-real-backend test surface is unreachable.

### 2.2 E2E Scenario Gaps

| ID | Scenario | Status | What's Missing |
|----|----------|--------|----------------|
| E2E-01 | Smoke | **DONE** | — |
| E2E-02 | Photo Upload | **DONE** | Content classification (Haiku) untested without API key |
| E2E-03 | LiDAR Scan | **DONE** | Tests use synthetic JSON, not real RoomPlan output |
| E2E-04 | Intake | **DONE** | Mock 3-step only; real Claude agent untested |
| E2E-05 | Generation | **DONE** | Mock stubs only; real Gemini untested |
| E2E-06 | Selection + Iteration | **DONE** | Mock stubs only |
| E2E-07 | Annotation | **DONE** | Covered in E2E-06 |
| E2E-08 | Iteration Cap | **DONE** | — |
| E2E-09 | Approve → Shopping | **DONE** | Mock shopping list only |
| E2E-10 | Start Over | **DONE** | — |
| E2E-11 | Error Recovery | **PARTIAL** | Only tests retry-with-no-error (no-op). Missing: error injection mechanism to induce one-shot activity failures |
| E2E-12 | Delete Photo | **DONE** | — |
| E2E-13 | Happy Path (Maestro) | **BLOCKED** | PRE-1 (iOS backend switching) |
| E2E-14 | Annotation (Maestro) | **BLOCKED** | PRE-1 |
| E2E-15 | Start Over (Maestro) | **BLOCKED** | PRE-1 |
| E2E-16 | Error + Retry (Maestro) | **BLOCKED** | PRE-1 + error injection mechanism |
| E2E-17 | Multi-Project Resume (Maestro) | **BLOCKED** | PRE-1 |
| E2E-18 | Shopping Quality | **STRUCTURAL DONE** | URL liveness checks require real Exa API key |

### 2.3 E2E-11 Error Injection Gap

The E2E test plan describes testing error recovery and retry, but the only test verifies "retry when no error is a no-op." Real error injection requires one of:

- **Option A: Test-mode flag** — A `FORCE_NEXT_FAILURE` env var or API endpoint that induces a one-shot activity failure. The activity fails once, then succeeds on retry. This is agent-implementable.
- **Option B: Bad API key** — Run with an intentionally invalid `GOOGLE_AI_API_KEY` to trigger a Gemini failure at generation time. Verify the error overlay appears and retry (with valid key) succeeds.
- **Option C: Network fault injection** — Use `toxiproxy` or similar to inject latency/drops on the Temporal→activity connection.

**Recommendation**: Option A for automated testing (agent can implement the flag + tests), Option B as a manual smoke test.

### 2.4 Mock→Real Transition Gaps

Each component that currently runs against mocks needs a path to real testing. The table below shows the current test coverage tier and what's needed for real mode.

| Component | Mock Tier | Real Tier | Gap | Real Mode Requires |
|-----------|-----------|-----------|-----|--------------------|
| **R2 Storage** | Unit tests mock `upload_to_r2` | API-level E2E with `use_temporal=true` writes to R2 | No round-trip verification (upload → download → verify content) | `R2_*` env vars + Cloudflare credentials |
| **Photo Validation (Pillow)** | Unit: 24 tests | E2E: 9 tests (E2E-02) | None — Pillow runs in all modes | — |
| **Photo Classification (Haiku)** | Unit: mocked | E2E: skipped without API key | Not tested with real images | `ANTHROPIC_API_KEY` |
| **Intake Agent (Claude)** | Unit: mock 3-step | E2E: mock 3-step | Real agent never tested through API | `ANTHROPIC_API_KEY` + `USE_MOCK_ACTIVITIES=false` |
| **Generation (Gemini)** | Unit: mock stubs | E2E: mock stubs | Real Gemini never triggered | `GOOGLE_AI_API_KEY` + `USE_MOCK_ACTIVITIES=false` |
| **Edit (Gemini)** | Unit: mock stubs | E2E: mock stubs | Real Gemini edit never triggered | `GOOGLE_AI_API_KEY` + `USE_MOCK_ACTIVITIES=false` |
| **Shopping (Exa)** | Unit: mock stubs | E2E-18: structural only | Real product search never triggered | `EXA_API_KEY` + `USE_MOCK_ACTIVITIES=false` |
| **LiDAR Parser** | Unit: 19 tests | E2E-03: synthetic JSON | Never tested with real RoomPlan output | Real RoomPlan JSON from device |
| **Temporal Workflow** | Unit: 31 bridge tests | E2E-01: health + CRUD | Full signal chain tested, but only with mock activities | Real activities + API keys |

**Key observation**: The backend Temporal bridge is well-tested (31 tests, 98% coverage on `projects.py`). The gap is not in the bridge itself but in the **activities behind the bridge** — they run as mocks in all current test configurations.

---

## 3. LiDAR Enhancement

### 3.1 Current State (100% Mock)

**iOS** (`ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift`):
- Placeholder UI: icon + "Scan Your Room" title + Start/Skip buttons
- `startScan()` sends hardcoded mock dimensions `{width: 4.2, length: 5.8, height: 2.7}`
- LiDAR availability check always returns `true` (comment: "In P2: check `ARWorldTrackingConfiguration`")
- Zero ARKit/RoomPlan framework imports anywhere in the codebase

**Backend** (`backend/app/utils/lidar.py`):
- `parse_room_dimensions(raw: dict) -> RoomDimensions` — validates and converts RoomPlan JSON
- 19 tests covering happy path + 12 error cases
- Schema: `{"room": {"width", "length", "height"}, "walls": [...], "openings": [...]}`

**Mock Client** (`ios/Packages/RemoNetworking/Sources/RemoNetworking/MockWorkflowClient.swift`):
- `uploadScan`: ignores input, creates `ScanData(storageKey: "...", roomDimensions: RoomDimensions(widthM: 4.2, ...))`
- `skipScan`: transitions step to `"intake"` with no scan data

### 3.2 Contract Boundary: RoomScanResult

The key architectural decision is where to draw the **testability boundary**. The existing `ScanData` model is the contract between iOS and the backend:

```
┌─────────────────────┐     ScanData (JSON)     ┌──────────────────────┐
│  iOS: RoomPlan AR   │ ──────────────────────→  │  Backend: lidar.py   │
│  (hardware-bound)   │                          │  (agent-testable)    │
└─────────────────────┘                          └──────────────────────┘
         │                                                │
   RoomCaptureSession                             parse_room_dimensions()
   ARCoachingOverlay                                      │
   USDZ → JSON export                            RoomDimensions model
                                                          │
                                                  GenerateDesignsInput
                                                  GenerateShoppingListInput
```

**New type: `RoomScanResult`** — an iOS-side value type that wraps the raw RoomPlan output into a clean, serializable form. This is the bridge between AR hardware and the network layer:

```swift
// In RemoLiDAR package
public struct RoomScanResult: Sendable {
    public let dimensions: RoomDimensions
    public let rawJSON: [String: Any]     // Full RoomPlan export for upload
    public let wallCount: Int
    public let openingCount: Int
    public let scanDurationSeconds: Double
}
```

Everything above `RoomScanResult` (the extraction from `CapturedRoom`) requires real hardware. Everything below it (serialization, upload, backend parsing) is testable with synthetic data.

### 3.3 Phase 1: Data Pipeline (Agent-Testable)

**Goal**: Wire the iOS scan data pipeline end-to-end, using synthetic `RoomScanResult` instances. No AR hardware needed.

#### New Files

| File | Package | Purpose | Lines (est.) |
|------|---------|---------|--------------|
| `RoomScanResult.swift` | RemoLiDAR | Value type wrapping extracted room data | ~40 |
| `RoomPlanExporter.swift` | RemoLiDAR | Converts `CapturedRoom` → `RoomScanResult` (stubbed) | ~60 |
| `ScanUploader.swift` | RemoLiDAR | Serializes `RoomScanResult` → JSON, calls `client.uploadScan()` | ~50 |

#### Modified Files

| File | Change |
|------|--------|
| `LiDARScanScreen.swift` | Replace hardcoded dict with `ScanUploader.upload(result)` |
| `MockWorkflowClient.swift` | `uploadScan` parses the actual `scanData` dict instead of ignoring it |
| `RemoLiDAR/Package.swift` | Add test target |

#### New Tests (in `RemoLiDAR` test target)

- `RoomScanResultTests` — construction, serialization round-trip (~5 tests)
- `ScanUploaderTests` — mock client receives correctly formatted JSON (~4 tests)
- `RoomPlanExporterTests` — stub returns synthetic result (~3 tests)

#### Verification

```bash
swift test --package-path ios/Packages/RemoLiDAR
# Backend: existing test_lidar.py passes (no backend changes in Phase 1)
```

### 3.4 Phase 2: UI Shell + State Machine (Simulator-Testable)

**Goal**: Build the scanning UI with a state machine that drives the screen through scanning states, but using a mock data source instead of real ARKit.

#### State Machine

```
┌──────────┐    start    ┌──────────┐   room      ┌───────────┐
│  ready   │ ──────────→ │ scanning │ ─detected──→ │ captured  │
└──────────┘             └──────────┘              └───────────┘
                              │                         │
                           timeout                    upload
                              │                         │
                              ▼                         ▼
                         ┌──────────┐            ┌───────────┐
                         │  failed  │            │  uploaded  │
                         └──────────┘            └───────────┘
```

States: `ready`, `scanning`, `captured`, `uploading`, `uploaded`, `failed`

#### New Files

| File | Package | Purpose | Lines (est.) |
|------|---------|---------|--------------|
| `ScanState.swift` | RemoLiDAR | Enum + state machine logic | ~60 |
| `ScanProgressView.swift` | RemoLiDAR | Progress UI (scanning animation, capture preview) | ~80 |

#### Modified Files

| File | Change |
|------|--------|
| `LiDARScanScreen.swift` | Integrate `ScanState` machine, show `ScanProgressView` during scan, replace placeholder icon with scanning UI |

#### New Tests

- `ScanStateTests` — all transitions, invalid transitions rejected (~8 tests)
- Maestro: Update `02-skip-scan.yaml` to verify new UI elements

#### Verification

```bash
swift test --package-path ios/Packages/RemoLiDAR
maestro test ios/.maestro/flows/02-skip-scan.yaml
```

### 3.5 Phase 3: AR Integration (Human-Tested Last)

**Goal**: Connect real ARKit/RoomPlan to the state machine built in Phase 2.

This phase introduces the only hardware-dependent code. It is deliberately thin — a ~100-line extraction layer that converts Apple's `CapturedRoom` into the `RoomScanResult` from Phase 1.

#### New Files

| File | Package | Purpose | Lines (est.) |
|------|---------|---------|--------------|
| `RoomCaptureCoordinator.swift` | RemoLiDAR | `RoomCaptureSessionDelegate` implementation, drives state machine | ~120 |
| `ARCoachingOverlayWrapper.swift` | RemoLiDAR | SwiftUI wrapper for `ARCoachingOverlayView` (plane detection guidance) | ~50 |
| `RoomCaptureViewWrapper.swift` | RemoLiDAR | SwiftUI `UIViewRepresentable` for `RoomCaptureView` | ~60 |

#### Modified Files

| File | Change |
|------|--------|
| `LiDARScanScreen.swift` | Present `RoomCaptureViewWrapper` + `ARCoachingOverlayWrapper` when `isScanning` |
| `RoomPlanExporter.swift` | Replace stub with real `CapturedRoom` → `RoomScanResult` conversion |
| `RemoLiDAR/Package.swift` | Add `RoomPlan` and `ARKit` framework dependencies |
| `HomeScreen.swift` | Replace `checkLiDARAvailability()` placeholder with real `ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)` check |

#### ARCoachingOverlay Design

```swift
struct ARCoachingOverlayWrapper: UIViewRepresentable {
    func makeUIView(context: Context) -> ARCoachingOverlayView {
        let overlay = ARCoachingOverlayView()
        overlay.activatesAutomatically = true
        overlay.goal = .horizontalPlane  // Guide user to detect floor
        return overlay
    }
}
```

The coaching overlay appears during the initial scanning phase. Once RoomPlan detects sufficient geometry, it auto-dismisses. This provides user guidance without custom UI work.

#### RoomCaptureView Design

```swift
struct RoomCaptureViewWrapper: UIViewRepresentable {
    let session: RoomCaptureSession

    func makeUIView(context: Context) -> RoomCaptureView {
        let view = RoomCaptureView(frame: .zero)
        view.captureSession = session
        view.delegate = context.coordinator
        return view
    }
}
```

RoomPlan's built-in `RoomCaptureView` provides real-time 3D scanning visualization. We wrap it rather than building custom AR rendering.

#### Verification

```
- Manual: Run on LiDAR-equipped device (iPhone 12 Pro+, iPad Pro 2020+)
- Verify: coaching overlay appears, room scan completes, dimensions upload to backend
- Verify: dimensions in backend match physical room (±10% tolerance)
- Verify: non-LiDAR device shows "LiDAR not available" and skip-only path
```

### 3.6 File Summary

| File | Phase | New/Modified | Package |
|------|-------|-------------|---------|
| `RoomScanResult.swift` | 1 | New | RemoLiDAR |
| `RoomPlanExporter.swift` | 1 (stub), 3 (real) | New | RemoLiDAR |
| `ScanUploader.swift` | 1 | New | RemoLiDAR |
| `ScanState.swift` | 2 | New | RemoLiDAR |
| `ScanProgressView.swift` | 2 | New | RemoLiDAR |
| `RoomCaptureCoordinator.swift` | 3 | New | RemoLiDAR |
| `ARCoachingOverlayWrapper.swift` | 3 | New | RemoLiDAR |
| `RoomCaptureViewWrapper.swift` | 3 | New | RemoLiDAR |
| `LiDARScanScreen.swift` | 1, 2, 3 | Modified | RemoLiDAR |
| `MockWorkflowClient.swift` | 1 | Modified | RemoNetworking |
| `RemoLiDAR/Package.swift` | 1, 3 | Modified | RemoLiDAR |
| `HomeScreen.swift` | 3 | Modified | Remo (app) |

**Total**: 8 new files, 4 modified files

---

## 4. Implementation Phases (Ordered for Agent-First Testing)

### Phase A: PRE-1 Unblock — iOS Backend Switching

**Owner**: T1 (iOS)
**Blocked by**: Nothing
**Unblocks**: E2E-13, E2E-14, E2E-15, E2E-16, E2E-17

#### Scope

Add a backend switching mechanism to the iOS app so Maestro flows can target either the mock client or a real backend.

#### Files

| File | Change |
|------|--------|
| `ios/Remo/App/RemoApp.swift` | Read `real-backend` and `backend-url` launch arguments; instantiate `RealWorkflowClient(baseURL:)` when `real-backend == "true"`, otherwise `MockWorkflowClient` |
| `ios/Remo/App/AppEnvironment.swift` (new) | Centralize launch arg parsing: `isMaestroTest`, `isRealBackend`, `backendURL` |
| `ios/Packages/RemoNetworking/.../RealWorkflowClient.swift` | Verify all methods match `WorkflowClientProtocol` (should already be the case) |
| `ios/.maestro/flows/happy-path.yaml` | No change needed — existing `arguments` block supports additional keys |

#### Agent Testing

```bash
# Build with mock (default) — existing Maestro flows pass
xcodebuild ... -destination 'platform=iOS Simulator,name=iPhone 16 Pro'
maestro test ios/.maestro/flows/happy-path.yaml

# Verify launch arg parsing (unit test)
swift test --package-path ios/Packages/RemoNetworking
```

#### Acceptance Criteria

- `maestro test happy-path.yaml` passes with mock client (regression)
- `maestro test happy-path.yaml` with `real-backend: "true"` + running backend reaches at least the generation step (may timeout on real AI)
- No hardcoded `MockWorkflowClient` in `RemoApp.swift` — client type is determined at launch

---

### Phase B: LiDAR Data Pipeline + UI Shell

**Owner**: T1 (iOS)
**Blocked by**: Nothing (can run in parallel with Phase A)
**Unblocks**: Phase D (AR integration)

#### Scope

LiDAR Phases 1 + 2 from Section 3: data pipeline with `RoomScanResult`, scan state machine, progress UI. All agent-testable — no AR hardware.

#### Files

See Section 3.3 and 3.4 for full file lists.

#### Agent Testing

```bash
swift test --package-path ios/Packages/RemoLiDAR    # New test target
swift test --package-path ios/Packages/RemoModels    # Regression
swift test --package-path ios/Packages/RemoNetworking # Regression (MockWorkflowClient change)
maestro test ios/.maestro/flows/02-skip-scan.yaml    # Regression
```

#### Acceptance Criteria

- `RoomScanResult` can be constructed from synthetic data and serialized to the JSON schema `lidar.py` expects
- `ScanState` machine covers all transitions with tests
- `LiDARScanScreen` uses `ScanUploader` instead of hardcoded dict
- `MockWorkflowClient.uploadScan` parses the actual `scanData` dict
- Backend `test_lidar.py` still passes (no backend changes)

---

### Phase C: Real AI Service Testing

**Owner**: T0 (Platform) + T2/T3 for activity-specific issues
**Blocked by**: API keys in `.env`
**Unblocks**: Confidence in real mode before Maestro flows

#### Scope

Run the existing API-level E2E tests (E2E-01 through E2E-12, E2E-18) against a real backend with `USE_MOCK_ACTIVITIES=false`. Fix any issues that surface.

#### Steps

1. **Setup**: `./scripts/e2e-setup.sh --real` with all API keys in `.env`
2. **Run API server**: `USE_TEMPORAL=true USE_MOCK_ACTIVITIES=false .venv/bin/python -m uvicorn app.main:app --port 8100`
3. **Run worker**: `USE_MOCK_ACTIVITIES=false .venv/bin/python -m app.worker`
4. **Execute**: `E2E_BASE_URL=http://localhost:8100 .venv/bin/python -m pytest tests/test_e2e.py -x -v`

#### Expected Issues

| Test | Likely Issue | Fix |
|------|-------------|-----|
| E2E-04 | Real intake agent takes 10-30s per turn (vs instant mock) | Increase poll timeout |
| E2E-05 | Real Gemini generation takes 30-90s | Increase poll timeout to 120s |
| E2E-06/07 | Real Gemini edit takes 30-60s per iteration | Increase poll timeout |
| E2E-08 | 5 real iterations = 5 × 30-60s Gemini calls | Long test, may need `@pytest.mark.slow` |
| E2E-09 | Real Exa shopping takes 15-30s | Increase poll timeout |
| E2E-18 | Mock product URLs won't be live | URL liveness only meaningful with real Exa |

#### Additional Real-Mode Tests to Add

```python
class TestRealModeSmoke:
    """Smoke tests that only make sense with real activities."""

    @pytest.mark.real  # Only run when USE_MOCK_ACTIVITIES=false
    async def test_generation_produces_real_images(self, client):
        """Generated images are real URLs (not mock placeholders)."""
        # ... advance to selection ...
        for opt in state["generated_options"]:
            assert "mock" not in opt["image_url"].lower()
            assert "mock" not in opt["caption"].lower()

    @pytest.mark.real
    async def test_shopping_produces_real_products(self, client):
        """Shopping list contains real product names and live URLs."""
        # ... advance to completed ...
        for item in state["shopping_list"]["items"]:
            assert "Mock" not in item["product_name"]
            # URL liveness check
            r = await client.get(item["product_url"], follow_redirects=True)
            assert r.status_code in (200, 301, 302, 403)
```

#### Agent Testing

```bash
# All existing E2E tests still pass in mock mode (regression)
cd backend && .venv/bin/python -m pytest tests/test_e2e.py -x -v

# Real mode (requires running infrastructure + API keys)
E2E_BASE_URL=http://localhost:8100 .venv/bin/python -m pytest tests/test_e2e.py -x -v -m "not slow"
```

#### Acceptance Criteria

- E2E-01 through E2E-03 pass with real Temporal (no AI dependency)
- E2E-04 passes with real Claude intake agent
- E2E-05 passes with real Gemini generation
- E2E-09 + E2E-18 pass with real Exa shopping
- All tests auto-detect mock vs real mode and adjust timeouts accordingly

---

### Phase D: LiDAR AR Integration (Human-Tested)

**Owner**: T1 (iOS)
**Blocked by**: Phase B (data pipeline + UI shell must be complete)
**Unblocks**: Nothing — this is the final phase

#### Scope

LiDAR Phase 3 from Section 3.5: Connect real ARKit/RoomPlan to the data pipeline and UI shell built in Phase B.

#### Files

See Section 3.5 for full file list (3 new, 4 modified).

#### Human Testing Protocol

This phase cannot be agent-tested — it requires a physical LiDAR-equipped device.

**Devices**: iPhone 12 Pro or later, iPad Pro 2020 or later

**Test Matrix**:

| Test | Device | Expected Result |
|------|--------|-----------------|
| AR coaching appears | LiDAR device | `ARCoachingOverlayView` guides user to detect floor plane |
| Room scan completes | LiDAR device | `RoomCaptureView` captures room, transitions to `captured` state |
| Dimensions upload | LiDAR device | Backend receives JSON, `parse_room_dimensions` succeeds |
| Dimension accuracy | LiDAR device | Parsed dimensions within ±10% of physical measurement |
| Non-LiDAR fallback | iPhone 14 (no LiDAR) | Skip-only path shown, "Start Scanning" disabled |
| Scan timeout | LiDAR device (cover sensor) | State transitions to `failed` after timeout |
| Cancel mid-scan | LiDAR device | "Skip Scan" during scanning returns to `ready` state |

#### Acceptance Criteria

- Room scan on LiDAR device produces `RoomScanResult` that serializes to valid JSON
- Backend `parse_room_dimensions` accepts real RoomPlan output without errors
- Parsed dimensions are within ±10% of physical room measurements
- Non-LiDAR devices see skip-only path
- If AR issues are found, fix is contained to the 3 new Phase 3 files (thin extraction layer)

---

### Phase E: Maestro Real-Backend Flows (E2E-13 through E2E-17)

**Owner**: T1 (iOS) + T0 (observability)
**Blocked by**: Phase A (PRE-1) + Phase C (real mode confidence)
**Unblocks**: P3 sign-off

#### Scope

Create and run the 5 Maestro flows defined in `E2E_TEST_PLAN.md` sections E2E-13 through E2E-17 against a real backend.

#### New Maestro Flows

| Flow | E2E Scenario | Est. Duration |
|------|-------------|---------------|
| `e2e-13-real-happy-path.yaml` | E2E-13: Full happy path | 3-5 min (real AI) |
| `e2e-14-real-annotation.yaml` | E2E-14: Annotation with real edit | 3-5 min |
| `e2e-15-real-start-over.yaml` | E2E-15: Start over from various steps | 5-8 min |
| `e2e-16-real-error-retry.yaml` | E2E-16: Error overlay + retry | 2-3 min |
| `e2e-17-real-multi-project.yaml` | E2E-17: Multiple projects + resume | 8-12 min |

All flows use `arguments: { real-backend: "true", backend-url: "http://localhost:8000" }` (enabled by Phase A).

#### Key Differences from Mock Maestro Flows

- `extendedWaitUntil` timeouts: 60-120s (real AI calls) vs 5s (mock)
- Assertions: `assertNotVisible: "Mock"` to verify real data
- Observability: backend log tailing between steps, `GET /health` checks
- Screenshot capture at each major step for debugging

#### Agent Testing

```bash
# Start real backend
./scripts/e2e-setup.sh --real
cd backend && USE_TEMPORAL=true USE_MOCK_ACTIVITIES=false .venv/bin/python -m uvicorn app.main:app &
cd backend && USE_MOCK_ACTIVITIES=false .venv/bin/python -m app.worker &

# Run flows
maestro test ios/.maestro/flows/e2e-13-real-happy-path.yaml
maestro test ios/.maestro/flows/e2e-14-real-annotation.yaml
# ... etc
```

#### Acceptance Criteria

- E2E-13: Full happy path completes end-to-end with real AI-generated images and products
- E2E-14: Annotation circle edit produces visibly different image
- E2E-15: Start-over from 3 different steps all return to intake with photos preserved
- E2E-16: Error overlay appears on induced failure, retry succeeds
- E2E-17: Two projects persist across app restart, resume at correct steps

---

## 5. Testing Matrix

### Who Tests What

| Phase | Agent-Testable? | Human Required? | Owner |
|-------|----------------|-----------------|-------|
| A: PRE-1 iOS switching | Yes (unit tests + mock Maestro) | No | T1 |
| B: LiDAR pipeline + UI | Yes (Swift unit tests + Maestro skip-scan) | No | T1 |
| C: Real AI services | Yes (API-level E2E with keys) | No | T0 |
| D: LiDAR AR integration | No | Yes (LiDAR device) | T1 + Human |
| E: Maestro real-backend | Partially (agent runs Maestro, human verifies visual output) | Visual QA | T1 + T0 |

### Mock vs Real Coverage Matrix

| Feature | Unit (Mock) | API E2E (Mock) | API E2E (Real) | Maestro (Mock) | Maestro (Real) |
|---------|------------|---------------|----------------|---------------|----------------|
| Project CRUD | 30+ tests | E2E-01 (6) | Same | 01, 09, 12 | E2E-13, 17 |
| Photo Upload | 24 tests | E2E-02 (9) | Same + Haiku | 01 | E2E-13 |
| LiDAR Scan | 19 tests | E2E-03 (4) | Same | 02 | Phase D manual |
| Intake Chat | 15 tests | E2E-04 (7) | + Real Claude | 03, 08 | E2E-13 |
| Generation | 8 tests | E2E-05 (1) | + Real Gemini | 04 | E2E-13 |
| Selection | 10 tests | E2E-06 (5) | Same | 04 | E2E-13 |
| Annotation | 8 tests | E2E-06 (1) | + Real Gemini | 11 | E2E-14 |
| Iteration Cap | 4 tests | E2E-08 (1) | Same (slow) | 05 | — |
| Approval | 6 tests | E2E-09 (2) | Same | 06 | E2E-13 |
| Shopping | 8 tests | E2E-09, 18 (5) | + Real Exa | 07 | E2E-13 |
| Start Over | 8 tests | E2E-10 (3) | Same | 10 | E2E-15 |
| Error/Retry | 4 tests | E2E-11 (1) | + Injection | — | E2E-16 |
| Delete Photo | 6 tests | E2E-12 (3) | Same | — | — |
| Multi-Project | 4 tests | — | — | 13 | E2E-17 |
| Resume | 2 tests | — | — | — | E2E-17 |

### Phase Dependencies

```
Phase A (PRE-1) ─────────────────────────────────────────→ Phase E (Maestro real)
                                                              ↑
Phase B (LiDAR pipeline + UI) ──→ Phase D (LiDAR AR)         │
                                                              │
Phase C (Real AI testing) ────────────────────────────────────┘
```

Phases A, B, and C are independent and can run in parallel. Phase D depends on B. Phase E depends on A and C.

---

## 6. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| PRE-1 takes longer than expected (iOS client refactor) | Low | High — blocks all Maestro real flows | Phase A is scoped to launch arg only, no architectural change |
| Real AI timeouts in E2E tests | High | Medium — flaky tests | Separate `@pytest.mark.slow` marker, generous timeouts, retry decorator |
| RoomPlan JSON schema differs from parser expectation | Medium | Low — contained to parser | Phase 1 verifies serialization matches `lidar.py` expected schema |
| Gemini rate limits during E2E runs | Medium | Medium — tests fail mid-run | Test sequentially, add backoff, use test-specific API key with higher quota |
| LiDAR AR issues in Phase D | Medium | Low — contained to 3 files | Data pipeline + state machine are proven in Phases 1-2; fix is in thin extraction layer |
| Exa product URLs become stale | High | Low — cosmetic | URL liveness is informational, not blocking. Flag stale URLs, don't fail the test. |
