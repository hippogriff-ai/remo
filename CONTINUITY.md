# Continuity Ledger

## Goal
Build the T1 iOS app for Remo (AI room redesign). P0 Foundation complete, P1 screens scaffolded. Advancing to P1 polish and detail.

## Constraints/Assumptions
- iOS 17+, SwiftUI, SPM local packages (8 packages)
- Protocol injection: all views depend on `WorkflowClientProtocol`, never a concrete client
- Mock API in P1; swap to `RealWorkflowClient` in P2
- XcodeGen (`project.yml`) generates .xcodeproj — never hand-edit it
- `macOS(.v14)` added to all Package.swift for local CLI building/testing
- T0 backend scaffold complete with 301 passing tests (separate branch, merged to main)

## Key Decisions
- **`ProjectStep` enum** with `rawValue` matching backend step strings (`.photoUpload = "photos"`)
- **`AnyCodable`** for type-erased JSON dict fields (walls, openings in `RoomDimensions`)
- **`ProjectState`** is `@Observable` — central state object updated from `WorkflowState` polling responses
- **`PollingManager`** actor for cancel-safe polling (polls until step changes or task cancelled)
- **`NavigationStack` driven by `ProjectStep`** — router maps step to correct screen view
- **28 passing tests** (11 model decoding/encoding + 17 mock client state transitions), 0 warnings
- **Annotation-based editing** (numbered circles, not lasso) — tap to place, instruction per region
- **Polling over SSE** for MVP — iOS polls `GET /projects/{id}` every 2-3s

## State
- Done: P0 Foundation — 8 SPM packages, Swift mirrors of all Pydantic contracts, `WorkflowClientProtocol` + `MockWorkflowClient` + `RealWorkflowClient`, 28 tests passing
- Done: All P1 screens created — PhotoUpload (camera+gallery+validation), IntakeChat (bubbles+chips+summary), DesignSelection (swipeable+compare), Generating (loading), Iteration/Annotation (canvas+regions+text), Approval, Output (save+share), ShoppingList (grouped+badges+fit), LiDAR (scan+skip), Home (project list), ProjectFlow (NavigationStack router)
- Done: XcodeGen project.yml, asset catalogs, Info.plist, app entry point
- Now: Refine screens — async image loading, error states, better gestures, previews
- Next: P2 integration (swap mock for real API), annotation polish (undo, snap guides, haptics)

## Open Questions
- RoomPlan data format: JSON on-device vs USDZ? (P0 end question, deferred)
- Annotation circle sizing: pinch vs drag handle? (test both in prototype)

## Working Set
- ios/Packages/RemoModels/ (contract mirrors, protocol, tests)
- ios/Packages/RemoNetworking/ (mock + real clients, tests)
- ios/Packages/Remo{PhotoUpload,ChatUI,Annotation,DesignViews,ShoppingList,LiDAR}/ (UI)
- ios/Remo/App/ (app shell, navigation, state management)
- ios/project.yml (XcodeGen spec)
