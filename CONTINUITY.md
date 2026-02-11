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
- **32 passing tests** (15 model + 17 mock client), 0 warnings, 8/8 packages build
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
- Now: P1 polish — can consider done for PR. Minor remaining: photo delete/retake, undo on annotation
- Next: P2 integration (swap mock for real API), annotation polish (undo, snap guides, haptics)

## Open Questions
- RoomPlan data format: JSON on-device vs USDZ? (P0 end question, deferred)
- Annotation circle sizing: pinch vs drag handle? (test both in prototype)

## Working Set
- ios/Packages/RemoModels/ (contract mirrors, protocol, ProjectState, tests)
- ios/Packages/RemoNetworking/ (mock + real clients, tests)
- ios/Packages/Remo{PhotoUpload,ChatUI,Annotation,DesignViews,ShoppingList,LiDAR}/ (UI)
- ios/Remo/App/ (app shell, navigation, state management)
- ios/project.yml (XcodeGen spec)
