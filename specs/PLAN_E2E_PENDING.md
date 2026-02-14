# Pending Work: Making Remo Functional for Human Testing

> **Created**: 2026-02-13
> **Goal**: Get every feature working so a human can run the app end-to-end and test manually
> **Scope**: Everything except LiDAR AR integration (deliberately last, requires physical device)
> **Starting point**: Backend 95% done (1049 tests, 94% coverage, golden path verified). iOS screens exist but have spec gaps.

---

## What's Already Done

- Backend API + Temporal workflow: fully operational, 80 E2E tests passing
- Real AI verified: Claude Opus (intake), Gemini 3 Pro (generation/edit), Exa (shopping), Claude Haiku (validation)
- Golden path test: 216s end-to-end with real services
- All contracts frozen, Phase 1a models added (112 contract tests)
- Purge: 24h grace + 48h abandonment fully working
- Error injection mechanism ready
- LLM response caching for dev/test

---

## Phase 1: Wire iOS to Real Backend

**Why first**: Everything else is untestable until the app can talk to the real backend.

### P1-01: Backend Switching via Launch Arguments — DONE
**File**: `ios/Remo/App/RemoApp.swift`
**What**: Check UserDefaults for `real-backend` (bool) and `backend-url` (string) launch arguments. If `real-backend` is true, use `RealWorkflowClient(baseURL:)` instead of `MockWorkflowClient`.
**Done**: RemoApp.swift reads `real-backend` and `backend-url` from UserDefaults, creates `RealWorkflowClient(baseURL:)` when enabled.

### P1-02: X-Request-ID in Error Reporting — DONE
**File**: `ios/Packages/RemoNetworking/Sources/RemoNetworking/RealWorkflowClient.swift`
**What**: Extract `X-Request-ID` header from HTTP responses and include in `APIError` for log correlation.
**Why**: When something fails in the real backend, the request ID lets you trace it in server logs.
**Done**: X-Request-ID extracted from HTTP response, included in `ErrorResponse.requestId`, and surfaced in error UI via `APIError.errorDescription` — appends "(Reference: abc-123)" to error messages when requestId is present. All error alerts across the app automatically display it.

---

## Phase 2: Core Feature Gaps (Spec Compliance)

**Why second**: These are missing features that a human tester will immediately notice. Ordered by user flow sequence.

### P2-01: Photo Upload — Inspiration Notes UI — DONE
**File**: `ios/Packages/RemoPhotoUpload/Sources/RemoPhotoUpload/PhotoUploadScreen.swift`
**What**: Add a text field (max 200 chars) below each inspiration photo thumbnail. Bind to `PhotoData.note` and send with upload.
**Done**: Notes TextField added. Note: stored locally only — backend `uploadPhoto` API doesn't accept notes yet.

### P2-02: Photo Upload — "Opposite Corners" Instruction — DONE
**File**: `ios/Packages/RemoPhotoUpload/Sources/RemoPhotoUpload/PhotoUploadScreen.swift`
**What**: Replace generic "Take at least 2 photos" text with: "Take 2 photos from opposite corners of the room so we can see the full space." Add a simple top-down room diagram showing camera positions.
**Spec ref**: 4.3.1
**Done**: Text instruction updated. `CameraDiagram` view added — top-down room rectangle with two camera dots in opposite corners and field-of-view cones drawn using SwiftUI Canvas. Placed between the instruction text and inspiration photos text.

### P2-03: Intake — Mode Selection Screen — DONE
**File**: `ios/Packages/RemoChatUI/Sources/RemoChatUI/IntakeChatScreen.swift`
**What**: Before the chat starts, show 3-4 mode selection buttons (Quick/Full/Open/Skip).
**Done**: Mode selection buttons with accessibility identifiers (`mode_quick`, `mode_full`, `mode_open`, `mode_skip`). Skip visible only when inspiration photos uploaded.

### P2-04: Intake — Summary Correction Flow — DONE
**File**: `ios/Packages/RemoChatUI/Sources/RemoChatUI/IntakeChatScreen.swift`
**What**: Summary card with Confirm + Change actions.
**Done**: SummaryCard shows "Looks Good" and "I Want to Change Something" buttons (`chat_confirm_brief`, `chat_change_brief`). INTAKE-5/6 spec compliance.

### P2-05: Approval — Confirmation Dialog — DONE
**File**: `ios/Packages/RemoDesignViews/Sources/RemoDesignViews/ApprovalScreen.swift` + `IterationScreen.swift`
**Done**: Both screens use `confirmationDialog` with "Approve" / "Keep Editing" buttons. Only calls `approveDesign()` on confirm.

### P2-06: Shopping List — Share, Copy, Copy Link — DONE
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**Done**: Share/Copy All buttons with share sheet and clipboard. Per-product "Copy Link" button.

### P2-07: Shopping List — Display "Why This Match" — DONE
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**Done**: `whyMatched` displayed as italic caption on each product card.

### P2-08: Shopping List — Non-LiDAR Banner — DONE
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**Done**: Non-LiDAR banner shown when project has no scan data.

### P2-09: Iteration — 5-Round Limit Message — DONE
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**Done**: Limit message shown when `iterationCount >= 5`, editing controls hidden. `iteration_limit_message` accessibility identifier.

### P2-10: Text Feedback — 10-Character Minimum — DONE
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**Done**: Client-side 10-char minimum with orange hint text. Matches backend validation.

---

## Phase 3: Annotation Improvements

**Why third**: The annotation tool works (circles + instructions) but is simplified vs spec. These items improve the editing experience.

### P3-01: Region Editor — Full Fields — DONE
**Files**: `ios/.../IterationScreen.swift` + `backend/app/models/contracts.py` (AnnotationRegion)
**What**:
1. **Backend**: Add to `AnnotationRegion`: `action: str` (Replace/Remove/Change finish/Resize/Reposition), `avoid: list[str] = []`, `constraints: list[str] = []`
2. **iOS**: Add to Region Editor form:
   - Action picker (single select): Replace, Remove, Change finish/color/material, Resize, Reposition
   - Avoid field: comma-separated text tokens
   - Style nudges: toggle chips (cheaper, premium, more minimal, more cozy, more modern, pet-friendly, kid-friendly, low maintenance)
3. **Backend prompt**: Update `backend/prompts/edit.txt` to reference action/avoid/constraints
**Spec ref**: 4.7.4
**Done**: Backend contracts have action/avoid/constraints fields. iOS RegionListRow has action picker, instruction text field, avoid comma-separated TextField (with split/join binding), and 8 style nudge toggle chips using a custom `FlowLayout`. Backend `_build_edit_instructions()` includes action/avoid/constraints in prompts.

### P3-02: Edit List Panel — DONE
**File**: `ios/.../IterationScreen.swift`
**What**: Replace inline region controls with a proper scrollable list panel (bottom sheet on iPhone, side panel on iPad). Each item shows: number, action, first ~40 chars of instruction. Support swipe-to-delete.
**Spec ref**: 4.7.5
**Done**: Bottom sheet with `RegionListPanel` + `RegionListRow`. Compact summary bar in annotation controls opens sheet. Sheet auto-opens when regions added. Rows expand/collapse for editing. Swipe-to-delete. `presentationBackgroundInteraction` keeps canvas tappable. Maestro flow updated.

### P3-03: Region Overlap Detection — DONE
**File**: `ios/.../IterationScreen.swift`
**Done**: `checkRegionOverlap()` validates before creation. Alert shown with "Regions Can't Overlap" title. 7 unit tests in SnapGuideTests.swift.

### P3-04: Revision History View — DONE
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**What**: Allow user to swipe back through previous revisions during iteration. Read-only view showing each revision image. Data already stored in `WorkflowState.revisionHistory`.
**Spec ref**: 4.7.7
**Done**: "History" button in iteration toolbar opens a sheet listing each revision with number, `AsyncImage` for `revisedImageUrl` (with loading/error states), and instruction text. Image capped at 200pt height with rounded corners.

### P3-05: Freehand Lasso (Design Decision Required)
**File**: `ios/.../IterationScreen.swift`
**What**: Replace tap-to-place circles with freehand drawing: user drags finger to draw a closed loop. Auto-close on finger lift. Validate: min 2% of image area, no self-intersection.
**Spec ref**: 4.7.2
**Decision**: This is a significant UX rewrite. **Option A**: implement freehand for spec compliance. **Option B**: keep circles as a deliberate simplification for hackathon MVP (faster, less error-prone for users). Either way, document the choice.

---

## Phase 4: Data & Privacy UX

### P4-01: Onboarding Tooltip — DONE
**File**: `ios/Remo/App/HomeScreen.swift`
**Done**: First-launch alert with data retention warning. UserDefaults flag `remo_has_seen_onboarding`. Skipped in Maestro tests.

### P4-02: Approval Screen Save Reminder — DONE
**File**: `ios/Packages/RemoDesignViews/Sources/RemoDesignViews/OutputScreen.swift`
**Done**: Save reminder text displayed on output screen.

---

## Phase 5: Validation Error Message Polish

### P5-01: User-Friendly Validation Messages — DONE
**File**: `backend/app/activities/validation.py`
**Done**: Blur, resolution, and content validation messages match spec exactly. No technical details exposed.

---

## Phase 6: Maestro E2E Against Real Backend

**Prerequisite**: P1-01 (backend switching) must be done first.

### P6-01: Maestro Happy Path — Mock Activities
**What**: Run existing `happy-path.yaml` flow with launch args pointing to real Temporal + mock activities. Verify iOS → API → Temporal → mock activity → iOS cycle works.
**How**: `maestro test ios/.maestro/flows/happy-path.yaml` with app launched using `-real-backend true -backend-url http://localhost:8000`

### P6-02: Maestro Happy Path — Real AI
**What**: Same flow but with real activities (`USE_MOCK_ACTIVITIES=false`). Verify full pipeline: photos validated by Claude Haiku, intake by Claude Opus, generation by Gemini, shopping by Exa+Claude.
**Prerequisite**: API keys configured, Gemini quota available.

### P6-03: Maestro Annotation Flow
**What**: Reach iteration, draw annotation circle, add instruction, submit, verify new image appears.

### P6-04: Maestro Start Over
**What**: Progress to iteration, tap "Start Over", verify intake appears with photos preserved.

### P6-05: Maestro Error + Retry
**What**: Arm error injection via `/debug/force-failure`, trigger operation, verify error state + retry button, retry succeeds.

### P6-06: Maestro Multiple Projects + Resume
**What**: Create 2 projects at different steps, kill app, relaunch, verify both resume at correct step.

---

## Phase 8: PRODUCT_SPEC Compliance Gaps

**Why**: These are spec test cases not covered by any existing plan item. Identified by audit swarm cross-referencing PRODUCT_SPEC.md against actual implementation.

### P8-01: Home — Resume Badge (HOME-3) — DONE
**File**: `ios/Remo/App/HomeScreen.swift`
**What**: Show a "Resume" badge on pending project rows.
**Spec ref**: HOME-3
**Done**: ProjectRow now shows a "Resume" capsule badge (accent color, caption2 bold) next to the title for any project whose step is not `.completed`. Accessibility identifier `resume_badge` added.

### P8-02: Photo Upload — Inspiration People/Animals Validation (PHOTO-11/12) — DONE
**File**: `backend/app/activities/validation.py`
**What**: Validate inspiration photos don't contain people or animals.
**Spec ref**: PHOTO-11, PHOTO-12
**Done**: Backend `_check_content()` already differentiates room vs inspiration validation (line 154-164). For inspiration photos, the Claude Haiku prompt explicitly rejects people/animals, and the error message matches spec exactly: "Inspiration photos should show spaces, furniture, or design details — not people or animals." No changes needed — already implemented.

### P8-03: Intake — Domain-Based Progress Indicator (INTAKE-1/2) — DONE
**File**: `ios/Packages/RemoChatUI/Sources/RemoChatUI/IntakeChatScreen.swift` + `backend/app/activities/intake.py`
**What**: During Quick/Full intake, show a progress indicator reflecting which domains have been covered.
**Spec ref**: INTAKE-1, INTAKE-2
**Done**: Backend already returns `progress: "Turn X of ~Y — Z/11 domains covered"` in `IntakeChatOutput`. iOS IntakeChatScreen displays it as a bar above chat messages (line 112). No changes needed — already implemented.

### P8-04: Design Selection — View Mode Persistence (GEN-11) — DONE
**File**: `ios/Packages/RemoDesignViews/Sources/RemoDesignViews/DesignSelectionScreen.swift`
**What**: Persist the user's view mode preference (side-by-side vs swipeable).
**Spec ref**: GEN-11
**Done**: Changed `@State private var showSideBySide` to `@AppStorage("remo_show_side_by_side")`. Preference now persists across the session and app restarts.

### P8-05: Annotation — Number Chip Edge Clamping (LASSO-13) — DONE
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**What**: When a region's number chip would render off-canvas, clamp it to the nearest visible edge.
**Spec ref**: LASSO-13
**Done**: Number chip overlay now computes clamped X/Y offsets (half chip size margin from all edges). When region center is within 12pt of any edge, the chip shifts inward to stay fully visible.

### P8-06: Annotation — List Item Tap Highlights Region on Canvas (LASSO-10) — DONE
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**What**: When user taps a region row in the edit panel, the corresponding circle on the canvas highlights (thicker border, glow shadow, enhanced fill).
**Spec ref**: LASSO-10
**Done**: Added `highlightedRegionId: Int?` state on IterationScreen, passed as binding to AnnotationCanvas. RegionListPanel calls `onHighlight` when a row is tapped/expanded. Canvas circles show thicker border (5px), enhanced fill (0.3 opacity), and glow shadow when highlighted. Highlight clears on panel dismiss.

---

## Phase 7: LiDAR Integration (LAST — Manual Human Test)

**Why last**: Requires physical iPhone Pro. All other features work without it.

### P7-01: Real Device Capability Check
**File**: `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/LiDARScanScreen.swift`
**What**: Replace `hasLiDAR { true }` with actual `ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh)` check.

### P7-02: LiDAR Data Pipeline
**Files**: New Swift files in `RemoLiDAR` package
**What**: `RoomScanResult` value type, `RoomPlanExporter` (CapturedRoom → JSON), `ScanUploader` (serialize → upload). Tests for construction, serialization, mock upload.

### P7-03: LiDAR UI State Machine
**File**: `ios/Packages/RemoLiDAR/Sources/RemoLiDAR/`
**What**: State machine: ready → scanning → captured → uploading → uploaded/failed. Progress UI with animation.

### P7-04: LiDAR AR Integration (Manual)
**Files**: `RoomCaptureCoordinator.swift`, `RoomCaptureViewWrapper.swift`
**What**: Integrate Apple's RoomCaptureView. Replace mock scan with real capture → upload flow.
**Test**: On iPhone Pro — scan real room, verify dimensions ±10% of physical measurements, verify shopping list shows fit badges.

---

## Execution Priority (What to Do in What Order)

```
P1-01 (backend switching)     ← GATE: unlocks everything
  │
  ├── P2-01 through P2-10     ← Core feature gaps (do these next)
  │     Priority within P2:
  │       1. P2-03 (intake mode picker) — most visible gap
  │       2. P2-05 (approval confirmation) — prevents accidental approval
  │       3. P2-01 (inspiration notes) — affects generation quality
  │       4. P2-06 (shopping share/copy) — core deliverable UX
  │       5. P2-04 (summary correction)
  │       6. P2-07, P2-08, P2-09, P2-10 (polish)
  │       7. P2-02 (photo instruction text)
  │
  ├── P3-01 through P3-04     ← Annotation improvements
  │     (P3-05 freehand lasso is a design decision — discuss before starting)
  │
  ├── P4-01, P4-02             ← Data privacy UX (quick wins)
  │
  ├── P5-01                    ← Validation message polish
  │
  ├── P6-01 through P6-06     ← Maestro E2E validation
  │     (P6-01 first, then P6-02 with real AI, then edge cases)
  │
  ├── P8-01 through P8-05     ← PRODUCT_SPEC compliance gaps
  │     Priority within P8:
  │       1. P8-04 (view mode persistence) — 1-line fix (@State → @AppStorage)
  │       2. P8-01 (resume badge) — visible UX gap
  │       3. P8-05 (chip edge clamping) — cosmetic but spec-required
  │       4. P8-02 (inspiration validation) — needs backend coordination
  │       5. P8-03 (domain progress) — needs backend chat response changes
  │
  └── P7-01 through P7-04     ← LiDAR (LAST, manual)
```

## Estimated Scope

| Phase | Items | Rough Size | Notes |
|-------|-------|-----------|-------|
| P1 | 2 | ~30 lines iOS | Quick — RealWorkflowClient already exists |
| P2 | 10 | ~400 lines iOS, ~10 lines backend | Mostly iOS UI additions |
| P3 | 5 | ~300 lines iOS, ~20 lines backend | Region editor + contract expansion |
| P4 | 2 | ~40 lines iOS | Quick wins |
| P5 | 1 | ~10 lines backend | String changes |
| P6 | 6 | Maestro YAML + test runs | Validation, not new code |
| P8 | 5 | ~150 lines iOS, ~20 lines backend | Spec compliance polish |
| P7 | 4 | ~500 lines iOS | Significant — ARKit/RoomPlan integration |

**Total before LiDAR**: ~950 lines of code changes + Maestro validation
**LiDAR**: ~500 lines + manual device testing
