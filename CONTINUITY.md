# Continuity Ledger

## Goal
Build the T1 iOS app for Remo (AI room redesign). P1 Independent Build substantially complete. All success metrics met.

## Constraints/Assumptions
- iOS 17+, SwiftUI, SPM local packages (8 packages)
- Protocol injection: all views depend on `WorkflowClientProtocol`, never a concrete client
- Mock API in P1; swap to `RealWorkflowClient` in P2
- XcodeGen (`project.yml`) generates .xcodeproj — never hand-edit it
- `macOS(.v14)` added to all Package.swift for local CLI building/testing
- T0 backend scaffold complete with 301 passing tests (separate branch, merged to main)
- `#if os(iOS)` guards on all platform-specific APIs for macOS build compatibility

## Key Decisions
- **`ProjectStep` enum** with `rawValue` matching backend step strings (`.photoUpload = "photos"`)
- **`AnyCodable`** for type-erased JSON dict fields (walls, openings in `RoomDimensions`)
- **`ProjectState`** is `@Observable` in RemoModels — central state, accessible to all packages
- **`PollingManager`** actor for cancel-safe polling (polls until step changes or task cancelled)
- **`NavigationStack` driven by `ProjectStep`** — router maps step to correct screen view
- **54 passing tests** (27 model + 17 mock client + 10 annotation), 0 warnings, 8/8 packages build
- **Annotation-based editing** (numbered circles, not lasso) — tap to place, drag to reposition
- **Polling over SSE** for MVP — iOS polls `GET /projects/{id}` every 2-3s
- **`ProjectState.preview(step:)`** factory for creating pre-populated states for #Preview blocks
- **Error handling** via `@State errorMessage: String?` + `.alert()` pattern on all screens

## State
- Done: P0 Foundation — 8 SPM packages, Swift mirrors of all Pydantic contracts, protocol + mock + real clients, 32 tests
- Done: All P1 screens — Photo upload, Intake chat, Design selection, Generating, Iteration/Annotation, Approval, Output (share + zoom), Shopping list, LiDAR scan, Home, ProjectFlow
- Done: #Preview blocks on every SwiftUI view (11 total), using MockWorkflowClient
- Done: Error handling on all async actions (no TODOs remaining)
- Done: Platform guards (#if os(iOS)) on all iOS-only APIs
- Done: DesignImageView (reusable AsyncImage wrapper), OutputScreen share + pinch-to-zoom
- Done: XcodeGen project.yml, asset catalogs, Info.plist, app entry point
- Done: Review fix — Missing UI imports in ProjectRouter (6 packages added)
- Done: Review fix — Eliminated silent `try?` error swallowing in ProjectFlowScreen, MockWorkflowClient, PhotoUploadScreen
- Done: Review fix — RealWorkflowClient now wraps URLError/DecodingError in APIError types, checkHTTPResponse throws on non-HTTP responses
- Done: Review fix — PollingManager moved to RemoNetworking (public), wired into GeneratingScreen + new ShoppingGeneratingScreen
- Done: Review fix — AnnotationCanvas uses stable regionId for ForEach, safe index lookups in drag/delete
- Done: Review fix — ProjectState.apply() logs unknown step strings via OSLog instead of silent ignore
- Done: Review fix — APIError.isRetryable respects CancellationError (not retryable)
- Done: Annotation undo — snapshot-based undo stack for add/delete/drag, undo button in annotation controls, history cleared on submit
- Done: ShoppingGeneratingScreen — loading screen with polling for the `shopping` step (was showing empty ShoppingListScreen)
- Done: HomeScreen refreshes project states on appear (no more stale data after backgrounding)
- Done: ProjectFlowScreen navigation — replaced append with replace (linear flow, no backward nav)
- Done: AnyCodable equality — replaced fragile String(describing:) with JSON-based comparison
- Done: Annotation submit — server-side validation guard before sending (prevents race with button disable)
- Done: Snap guides — dashed yellow alignment lines when dragging near center or other circles, with position snapping
- Done: Pinch-to-resize — MagnifyGesture on annotation circles (0.04-0.20 radius range)
- Done: Photo delete — X button on photo thumbnails with haptic feedback and animation
- Done: 10 snap guide unit tests (center snap, region alignment, threshold, exclusion)
- Done: Inspiration photo upload — separate PhotosPicker for room vs inspiration photos with correct type tagging
- Now: P2 annotation polish complete. All P1+P2 deliverables done except real API swap.
- Next: P2 integration (swap mock for real API), P3 stabilization (resume flow, error edge cases, polish)

## Open Questions
- RoomPlan data format: JSON on-device vs USDZ? (P0 end question, deferred)
- Annotation circle sizing: pinch vs drag handle? (test both in prototype)

## Working Set
- ios/Packages/RemoModels/ (contract mirrors, protocol, ProjectState, tests)
- ios/Packages/RemoNetworking/ (mock + real clients, tests)
- ios/Packages/Remo{PhotoUpload,ChatUI,Annotation,DesignViews,ShoppingList,LiDAR}/ (UI)
- ios/Remo/App/ (app shell, navigation, state management)
- ios/project.yml (XcodeGen spec)
