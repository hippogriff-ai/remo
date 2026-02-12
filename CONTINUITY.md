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

### T0 Platform
- Done: Product spec, all plans, T0 P0 #2-#8 + #10 (all P0 complete), P1 #11-#12, purge, worker, validation, migration, error handling hardened, validation.py silent failure fixes
- Done: **T0 code migration complete** — lasso/inpaint/regen → annotation-based edit system. 320 tests pass, ruff/format/mypy clean.

### T1 iOS
- Done: All P1 screens, 99 tests, 13 Maestro flows, 0 warnings, 8/8 packages build.
- Now: P1 complete. Next: P2 integration (swap mock for real API).

### T2 Image Gen
- Done: **T2 complete** — Gemini spike, all activities, 138 T2-specific tests (460 total), 100% coverage. Merged via PR #4.
- Now: Ready for T0 P2 #13 (wire real activities into workflow).

## Open Questions
- RoomPlan data format: JSON on-device vs USDZ? (P0 end question, deferred)
- Gemini annotation targeting quality: **PASS with caveat** — annotation artifacts in output. Stronger prompting needed in edit.txt template.

## Working Set
- ios/Packages/RemoModels/ (contract mirrors, protocol, ProjectState, tests)
- ios/Packages/RemoNetworking/ (mock + real clients, tests)
- ios/Packages/Remo{PhotoUpload,ChatUI,Annotation,DesignViews,ShoppingList,LiDAR}/ (UI)
- ios/Remo/App/ (app shell, navigation, state management)
- ios/project.yml (XcodeGen spec)
