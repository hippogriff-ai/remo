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

### P1-01: Backend Switching via Launch Arguments
**File**: `ios/Remo/App/RemoApp.swift`
**What**: Check UserDefaults for `real-backend` (bool) and `backend-url` (string) launch arguments. If `real-backend` is true, use `RealWorkflowClient(baseURL:)` instead of `MockWorkflowClient`.
**Why**: Currently hardcoded to `MockWorkflowClient`. The `RealWorkflowClient` is already fully implemented (all 17 API methods) — just need to wire the switch.
**Test**: Launch app with `-real-backend true -backend-url http://localhost:8000`, create a project, verify it appears in the backend DB.

### P1-02: X-Request-ID in Error Reporting
**File**: `ios/Packages/RemoNetworking/Sources/RemoNetworking/RealWorkflowClient.swift`
**What**: Extract `X-Request-ID` header from HTTP responses and include in `APIError` for log correlation.
**Why**: When something fails in the real backend, the request ID lets you trace it in server logs.

---

## Phase 2: Core Feature Gaps (Spec Compliance)

**Why second**: These are missing features that a human tester will immediately notice. Ordered by user flow sequence.

### P2-01: Photo Upload — Inspiration Notes UI
**File**: `ios/Packages/RemoPhotoUpload/Sources/RemoPhotoUpload/PhotoUploadScreen.swift`
**What**: Add a text field (max 200 chars) below each inspiration photo thumbnail. Bind to `PhotoData.note` and send with upload.
**Spec ref**: 4.3.2 — "For each inspiration photo, the user can add a short text note"
**Acceptance**: Upload inspiration photo, type a note, verify note appears in backend `WorkflowState.photos[].note`.

### P2-02: Photo Upload — "Opposite Corners" Instruction
**File**: `ios/Packages/RemoPhotoUpload/Sources/RemoPhotoUpload/PhotoUploadScreen.swift`
**What**: Replace generic "Take at least 2 photos" text with: "Take 2 photos from opposite corners of the room so we can see the full space." Add a simple top-down room diagram showing camera positions.
**Spec ref**: 4.3.1

### P2-03: Intake — Mode Selection Screen
**File**: `ios/Packages/RemoChatUI/Sources/RemoChatUI/IntakeChatScreen.swift`
**What**: Before the chat starts, show 3-4 buttons:
- "Quick Intake" — ~3 questions, ~2 minutes
- "Full Intake" — ~10 questions, ~8 minutes
- "Open Conversation" — Tell us everything, take your time
- "Skip" (only visible if inspiration photos uploaded)

Pass selected mode to `client.startIntake(projectId:mode:)`. Currently hardcoded to `"full"` at line 137.
**Spec ref**: 4.5 Entry & Form Selection
**Acceptance**: Select "Quick", verify agent asks ~3 questions. Select "Full", verify ~10. Skip button hidden when no inspiration photos.

### P2-04: Intake — Summary Correction Flow
**File**: `ios/Packages/RemoChatUI/Sources/RemoChatUI/IntakeChatScreen.swift`
**What**: When `is_summary == true`, show numbered confirmation: "1. Looks good / 2. I want to change something". If user picks 2, agent asks what to change, updates brief, re-displays summary.
**Spec ref**: 4.5 INTAKE-5, INTAKE-6
**Currently**: Only shows "Looks Good!" button.

### P2-05: Approval — Confirmation Dialog
**File**: `ios/Packages/RemoDesignViews/Sources/RemoDesignViews/ApprovalScreen.swift` (or `IterationScreen.swift` if approve button lives there)
**What**: Tapping "Approve Design" shows an alert: "Happy with this design? Once approved, it's final." with "Approve" and "Keep editing" buttons. Only call `client.approveDesign()` on confirm.
**Spec ref**: 4.9 line 519
**Why**: Users can currently approve accidentally with a single tap.

### P2-06: Shopping List — Share, Copy, Copy Link
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**What**:
- **Share Shopping List** button: Format all products as text (name, price, URL per line) and open iOS share sheet
- **Copy All** button: Same formatted text → clipboard, show "Shopping list copied!" toast
- **Copy Link** per product card: Copy product URL → clipboard
**Spec ref**: 4.9.3 lines 572-574

### P2-07: Shopping List — Display "Why This Match"
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**What**: The `whyMatched` field already exists in the `ProductMatch` model. Display it on each product card as a secondary text line (e.g., italic caption below the product name).
**Spec ref**: 4.9.3 table — "Why this match" column

### P2-08: Shopping List — Non-LiDAR Banner
**File**: `ios/Packages/RemoShoppingList/Sources/RemoShoppingList/ShoppingListScreen.swift`
**What**: If project has no scan data, show a banner at the top: "Tip: We matched products by style. For size-verified recommendations, use Room Scan on an iPhone Pro next time."
**Spec ref**: 4.9.3 LiDAR comparison table

### P2-09: Iteration — 5-Round Limit Message
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**What**: When `iterationCount >= 5`, show message: "You've used all 5 revision rounds. Please approve your design or start a new project." Disable both Annotate and Regenerate buttons.
**Spec ref**: 4.7 LASSO-17, 4.8 REGEN-5
**Currently**: Buttons disabled but no explanation shown.

### P2-10: Text Feedback — 10-Character Minimum
**File**: `ios/Packages/RemoAnnotation/Sources/RemoAnnotation/IterationScreen.swift`
**What**: Change text feedback validation from `.isEmpty` to `.count < 10`. Show hint: "Please provide more detail (at least 10 characters)".
**Spec ref**: 4.8 REGEN-2
**Currently**: Backend enforces this but iOS doesn't show the validation.

---

## Phase 3: Annotation Improvements

**Why third**: The annotation tool works (circles + instructions) but is simplified vs spec. These items improve the editing experience.

### P3-01: Region Editor — Full Fields
**Files**: `ios/.../IterationScreen.swift` + `backend/app/models/contracts.py` (AnnotationRegion)
**What**:
1. **Backend**: Add to `AnnotationRegion`: `action: str` (Replace/Remove/Change finish/Resize/Reposition), `avoid: list[str] = []`, `constraints: list[str] = []`
2. **iOS**: Add to Region Editor form:
   - Action picker (single select): Replace, Remove, Change finish/color/material, Resize, Reposition
   - Avoid field: comma-separated text tokens
   - Style nudges: toggle chips (cheaper, premium, more minimal, more cozy, more modern, pet-friendly, kid-friendly, low maintenance)
3. **Backend prompt**: Update `backend/prompts/edit.txt` to reference action/avoid/constraints
**Spec ref**: 4.7.4
**Currently**: Only `instruction` field exists.

### P3-02: Edit List Panel — DONE
**File**: `ios/.../IterationScreen.swift`
**What**: Replace inline region controls with a proper scrollable list panel (bottom sheet on iPhone, side panel on iPad). Each item shows: number, action, first ~40 chars of instruction. Support swipe-to-delete.
**Spec ref**: 4.7.5
**Done**: Bottom sheet with `RegionListPanel` + `RegionListRow`. Compact summary bar in annotation controls opens sheet. Sheet auto-opens when regions added. Rows expand/collapse for editing. Swipe-to-delete. `presentationBackgroundInteraction` keeps canvas tappable. Maestro flow updated.

### P3-03: Region Overlap Detection
**File**: `ios/.../IterationScreen.swift`
**What**: Before creating a new region, check if it overlaps any existing region. If so, show: "Regions can't overlap. Please draw around a different area, or delete an existing region first."
**Spec ref**: 4.7.2

### P3-04: Revision History View
**File**: `ios/Packages/RemoDesignViews/` (new or existing screen)
**What**: Allow user to swipe back through previous revisions during iteration. Read-only view showing each revision image. Data already stored in `WorkflowState.revisionHistory`.
**Spec ref**: 4.7.7

### P3-05: Freehand Lasso (Design Decision Required)
**File**: `ios/.../IterationScreen.swift`
**What**: Replace tap-to-place circles with freehand drawing: user drags finger to draw a closed loop. Auto-close on finger lift. Validate: min 2% of image area, no self-intersection.
**Spec ref**: 4.7.2
**Decision**: This is a significant UX rewrite. **Option A**: implement freehand for spec compliance. **Option B**: keep circles as a deliberate simplification for hackathon MVP (faster, less error-prone for users). Either way, document the choice.

---

## Phase 4: Data & Privacy UX

### P4-01: Onboarding Tooltip
**File**: `ios/Remo/App/RemoApp.swift` or `HomeScreen.swift`
**What**: On first launch (check UserDefaults flag), show a dismissible tooltip: "Your design data is temporary — save your final image to Photos when you're done. We automatically delete all project data within 48 hours."
**Spec ref**: 4.10 line 660

### P4-02: Approval Screen Save Reminder
**File**: `ios/.../ApprovalScreen.swift` or `OutputScreen.swift`
**What**: Add text: "Make sure to save your design image and copy your specs. Project data will be deleted after 24 hours."
**Spec ref**: 4.10 line 661

---

## Phase 5: Validation Error Message Polish

### P5-01: User-Friendly Validation Messages
**File**: `backend/app/activities/validation.py`
**What**: Update error messages to match spec exactly:
- Blur: "This photo looks blurry. Please retake with a steady hand." (remove technical score)
- Resolution: "This photo is too low resolution. Please use a higher quality image." (remove pixel count)
- Not a room: "We couldn't identify a room in this photo. Please upload a photo of an interior space."
**Spec ref**: 4.3.3 table

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
| P7 | 4 | ~500 lines iOS | Significant — ARKit/RoomPlan integration |

**Total before LiDAR**: ~800 lines of code changes + Maestro validation
**LiDAR**: ~500 lines + manual device testing
