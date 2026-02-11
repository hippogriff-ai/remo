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
- **`JSONValue` recursive enum** replaced `AnyCodable` for type-safe JSON (walls, openings in `RoomDimensions`)
- **`ProjectState`** is `@Observable` in RemoModels — central state, accessible to all packages
- **`PollingManager`** actor for cancel-safe polling (polls until step changes or task cancelled)
- **`NavigationStack` driven by `ProjectStep`** — router maps step to correct screen view
- **88 passing tests** (43 model + 35 networking + 10 annotation), 0 warnings, 8/8 packages build
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
- Done: Test coverage review gaps addressed — AnyCodable edge cases, RoomDimensions wall data, forward compatibility, DesignBrief round-trip, ProjectState.apply all fields, APIError.isRetryable (9 tests), 5-iteration cap boundary, error type assertions, not-found assertions
- Done: Silent failure hunt fixes — all 15 `guard projectId else { return }` now surface errors via assertionFailure + user-facing message
- Done: CameraView error surfacing — split guard for image extraction vs JPEG conversion, onError callback wired to validationMessages
- Done: PollingManager retry — transient errors retried up to 3× with exponential backoff (2s/4s/8s) before failing
- Done: RealWorkflowClient force-unwrap removal — `Data("string".utf8)` replaces `"string".data(using: .utf8)!`
- Done: DesignSelectionScreen bounds check — validates selectedIndex < generatedOptions.count before API call
- Done: HomeScreen missing project — shows ContentUnavailableView instead of blank screen
- Done: Resume flow — project IDs persisted to UserDefaults, restored on app launch, 404'd projects auto-removed
- Done: Project delete — swipe-to-delete on HomeScreen project rows (calls deleteProject + removes from persistence)
- Done: Loading state — HomeScreen shows ProgressView while loading projects on startup
- Done: Removed stale PollingManager.swift redirect from app shell
- Done: PollingManager tests — 7 tests covering step change, error state, retry, max retries, non-retryable immediate fail, single poll, cancellation
- Done: PollingManager backoff proportional to poll interval (tests run fast, production uses 2s base)
- Done: Type design improvements — JSONValue recursive enum (replaces Any-based AnyCodable), ProjectStep Comparable, PhotoType/RevisionType enums with computed accessors
- Done: Adopted typed enum accessors in views/mock (photoTypeEnum, revisionTypeEnum) — no more raw string comparisons
- Done: Request encoding tests — 6 tests validating snake_case JSON keys for all request models sent to backend
- Done: Typed accessor tests — forward compatibility (unknown values return nil)
- Done: MockWorkflowClient converted from `@unchecked Sendable` class to `actor` — compile-time data race protection for mutable state
- Done: Accessibility labels on key interactive elements (HomeScreen new project, PhotoUpload delete, IterationScreen submit/approve, OutputScreen save/share/shopping, ShoppingList product cards with buy links, LiDAR skip hint, Chat send + quick reply chips)
- Done: RealWorkflowClient `wrapErrors` helper — DRYed up 4× duplicated error-catching catch chains into single reusable method
- Done: UX polish — loading spinners on DesignSelectionScreen/ApprovalScreen action buttons, confirmation dialog for destructive "Start Over" action
- Done: Product image loading — AsyncImage in ShoppingList ProductCard (falls back to bag icon when no URL or load fails)
- Done: DRYed up duplicated `formatPrice` — extracted file-level function with static NumberFormatter (was duplicated in ShoppingContent + ProductCard)
- Done: Accessibility hints on DesignSelectionScreen "Choose This Design" + "Start Over", ApprovalScreen approve button
- Done: FlakyClient test helper converted from `@unchecked Sendable` class to actor — eliminates data race risk on mutable callCount
- Done: Force casts (`as!`) replaced with `try XCTUnwrap` in BackendCompatibilityTests and ModelsTests — better failure messages
- Done: HomeScreen `loadAndRefreshProjects` uses `withTaskGroup` for concurrent project state fetching (was sequential waterfall)
- Done: HomeScreen `deleteProjects` surfaces server-side delete errors via alert (was silent `try?` swallowing)
- Done: Test coverage expansion — 11 new tests: ProjectStep ordering (4), preview factory for all 9 steps (6 new), GenerationStatus Codable round-trip (1)
- Done: Maestro UI testing — happy path passes end-to-end (8 screenshots: home→scan→chat→selection→iteration→approve→shopping→output)
- Done: accessibilityIdentifier on all interactive elements (8 screen files), test backdoor (skipPhotos via launch arg), composable subflows (8 YAML files)
- Done: Fixed nested NavigationStack crash — ProjectFlowScreen uses direct view switch instead of nested NavigationStack
- Done: HomeScreen.createProject() now fetches server state after creation (was defaulting to .photoUpload)
- Done: PR fix — Annotation regionId collision uses `max() + 1` instead of `count + 1` (avoids duplicate IDs after deletion)
- Done: PR fix — HomeScreen refresh merges by projectId instead of array index (safe against concurrent mutations)
- Done: PR fix — PollingManager skips interval sleep when retrying (was double-sleeping: interval + backoff)
- Done: Maestro kept as local-only tool (GitHub Free plan: macOS runners too expensive). Commands documented in CLAUDE.md for Claude Code sessions.
- Now: All P1+P2+P3 deliverables done except real API swap. **99 tests** + Maestro happy path, 0 warnings, 8/8 packages build.
- Next: P2 integration (swap mock for real API)

## Open Questions
- RoomPlan data format: JSON on-device vs USDZ? (P0 end question, deferred)

## Working Set
- ios/Packages/RemoModels/ (contract mirrors, protocol, ProjectState, tests)
- ios/Packages/RemoNetworking/ (mock + real clients, tests)
- ios/Packages/Remo{PhotoUpload,ChatUI,Annotation,DesignViews,ShoppingList,LiDAR}/ (UI)
- ios/Remo/App/ (app shell, navigation, state management)
- ios/project.yml (XcodeGen spec)
