# iOS Architecture — Remo

Visual guide to the T1 iOS app architecture. All diagrams are Mermaid.

## 1. SPM Package Dependency Graph

```mermaid
graph TD
    subgraph "App Target"
        APP["Remo App<br/><i>ios/Remo/App/</i>"]
    end

    subgraph "Feature Packages"
        PU["RemoPhotoUpload<br/><i>PhotoUploadScreen</i>"]
        CU["RemoChatUI<br/><i>IntakeChatScreen</i>"]
        AN["RemoAnnotation<br/><i>IterationScreen</i>"]
        DV["RemoDesignViews<br/><i>5 screens</i>"]
        SL["RemoShoppingList<br/><i>ShoppingListScreen</i>"]
        LI["RemoLiDAR<br/><i>LiDARScanScreen</i>"]
    end

    subgraph "Core Packages"
        NW["RemoNetworking<br/><i>Mock + Real clients, Polling</i>"]
        MD["RemoModels<br/><i>Models, Protocol, ProjectState</i>"]
    end

    APP --> PU
    APP --> CU
    APP --> AN
    APP --> DV
    APP --> SL
    APP --> LI
    APP --> NW
    APP --> MD

    DV --> SL
    DV --> NW
    DV --> MD
    PU --> NW
    PU --> MD
    CU --> NW
    CU --> MD
    AN --> NW
    AN --> MD
    SL --> MD
    SL --> NW
    LI --> NW
    LI --> MD
    NW --> MD

    style MD fill:#4a9eff,color:#fff
    style NW fill:#34c759,color:#fff
    style APP fill:#ff9500,color:#fff
```

## 2. Project Workflow — Step-by-Step Flow

This shows the **real backend** flow. The mock client has shortcuts noted below.

```mermaid
flowchart LR
    P["photos<br/>PhotoUploadScreen"] -->|"2+ room photos"| S["scan<br/>LiDARScanScreen"]
    S -->|"upload or skip"| I["intake<br/>IntakeChatScreen"]
    I -->|"confirm or skip"| G["generation<br/>GeneratingScreen"]
    G -->|"polling: step changes"| SE["selection<br/>DesignSelectionScreen"]
    SE -->|"select option"| IT["iteration<br/>IterationScreen"]
    IT -->|"submit edit"| IT
    IT -->|"5 rounds hit"| A["approval<br/>ApprovalScreen"]
    IT -.->|"approve early"| C
    A -->|"approve"| SH["shopping<br/>ShoppingGeneratingScreen"]
    SH -->|"polling: step changes"| C["completed<br/>OutputScreen"]

    SE -.->|"start over"| I

    style P fill:#ff6b6b,color:#fff
    style S fill:#ffa07a,color:#fff
    style I fill:#ffd93d,color:#000
    style G fill:#a8e6cf,color:#000
    style SE fill:#6bcb77,color:#fff
    style IT fill:#4d96ff,color:#fff
    style A fill:#9b59b6,color:#fff
    style SH fill:#e056a0,color:#fff
    style C fill:#2ecc71,color:#fff
```

**Mock client shortcuts:** In P1 mock, `confirmIntake`/`skipIntake` skip `generation` and go directly to `selection`. `approveDesign` goes directly to `completed`, skipping `shopping`. The real backend will use `generation` and `shopping` as async polling steps.

## 3. Data Flow — Protocol Injection and State Updates

```mermaid
flowchart TB
    subgraph "App Entry"
        RA["RemoApp"]
        RA -->|"creates"| CLIENT["any WorkflowClientProtocol"]
        CLIENT -.- MOCK["MockWorkflowClient<br/><i>actor, in-memory state</i>"]
        CLIENT -.- REAL["RealWorkflowClient<br/><i>class, HTTP/URLSession</i>"]
    end

    subgraph "Navigation"
        RA -->|"injects client"| HS["HomeScreen"]
        HS -->|"creates per project"| PS["ProjectState<br/><i>@Observable</i>"]
        HS -->|"navigates to"| PF["ProjectFlowScreen"]
        PF -->|"routes via"| PR["ProjectRouter"]
        PR -->|"renders"| SCREEN["Current Screen<br/><i>based on ProjectStep</i>"]
    end

    subgraph "State Update Cycle"
        SCREEN -->|"1. user action"| API["client.someAction()"]
        API -->|"2. fetch latest"| GET["client.getState()"]
        GET -->|"3. returns"| WS["WorkflowState<br/><i>JSON from backend</i>"]
        WS -->|"4. apply()"| PS
        PS -->|"5. step changed"| PF
        PF -->|"6. navigate"| PR
    end

    style PS fill:#4a9eff,color:#fff
    style CLIENT fill:#34c759,color:#fff
    style SCREEN fill:#ff9500,color:#fff
    style WS fill:#ffd93d,color:#000
```

## 4. Navigation Architecture — Two-Level NavigationStack

```mermaid
flowchart TD
    subgraph "Outer NavigationStack (HomeScreen)"
        HL["Project List"]
        HL -->|"tap project or create new"| PFS["ProjectFlowScreen"]
    end

    subgraph "Inner NavigationStack (ProjectFlowScreen)"
        PFS --> ROUTER["ProjectRouter<br/><i>switch projectState.step</i>"]

        ROUTER -->|".photoUpload"| V1["PhotoUploadScreen<br/><i>RemoPhotoUpload</i>"]
        ROUTER -->|".scan"| V2["LiDARScanScreen<br/><i>RemoLiDAR</i>"]
        ROUTER -->|".intake"| V3["IntakeChatScreen<br/><i>RemoChatUI</i>"]
        ROUTER -->|".generation"| V4["GeneratingScreen<br/><i>RemoDesignViews</i>"]
        ROUTER -->|".selection"| V5["DesignSelectionScreen<br/><i>RemoDesignViews</i>"]
        ROUTER -->|".iteration"| V6["IterationScreen<br/><i>RemoAnnotation</i>"]
        ROUTER -->|".approval"| V7["ApprovalScreen<br/><i>RemoDesignViews</i>"]
        ROUTER -->|".shopping"| V8["ShoppingGeneratingScreen<br/><i>RemoDesignViews</i>"]
        ROUTER -->|".completed"| V9["OutputScreen<br/><i>RemoDesignViews</i>"]
    end

    subgraph "Error Handling"
        PFS -->|"projectState.error != nil"| ERR["ErrorOverlay<br/><i>retry or show message</i>"]
    end

    style ROUTER fill:#4a9eff,color:#fff
    style PFS fill:#ff9500,color:#fff
    style ERR fill:#ff6b6b,color:#fff
```

## 5. Polling Pattern — Async Step Transitions

```mermaid
sequenceDiagram
    participant V as GeneratingScreen
    participant PM as PollingManager
    participant C as WorkflowClientProtocol
    participant PS as ProjectState

    V->>PM: pollUntilStepChanges(projectId, "generation")
    loop Every 2 seconds
        PM->>C: getState(projectId)
        C-->>PM: WorkflowState(step: "generation")
        Note over PM: step unchanged, continue polling
    end
    PM->>C: getState(projectId)
    C-->>PM: WorkflowState(step: "selection")
    Note over PM: step changed!
    PM-->>V: return newState
    V->>PS: apply(newState)
    Note over PS: step = .selection
    Note over V: Navigation auto-advances to DesignSelectionScreen

    Note over PM: On transient error: retry up to 3x<br/>with exponential backoff (4s, 8s, 16s)
```

## 6. Test Coverage Map

```mermaid
graph LR
    subgraph "99 Total Tests"
        subgraph "RemoModels - 54 tests"
            M1["JSON Decoding (11)"]
            M2["Request Encoding (8)"]
            M3["ProjectStep (6)"]
            M4["ProjectState.apply (5)"]
            M5["ProjectState.preview (9)"]
            M6["JSONValue/AnyCodable (8)"]
            M7["Typed Accessors (2)"]
            M8["Other: RoomDimensions,<br/>Forward Compat, DesignBrief,<br/>GenerationStatus, PhotoCount (5)"]
        end

        subgraph "RemoNetworking - 35 tests"
            N1["MockClient Lifecycle (19)"]
            N2["APIError Retryable (9)"]
            N3["PollingManager (7)"]
        end

        subgraph "RemoAnnotation - 10 tests"
            A1["Snap Guides (10)"]
        end
    end

    style M1 fill:#34c759,color:#fff
    style N1 fill:#34c759,color:#fff
    style A1 fill:#34c759,color:#fff
```

## 7. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `@Observable` (not `ObservableObject`) | iOS 17+ only; simpler, no `@Published` needed |
| `actor MockWorkflowClient` | Compile-time data race safety for mutable in-memory state |
| Protocol in `RemoModels` (not `RemoNetworking`) | Avoids circular dependency; views import models, not networking |
| `ProjectStep.Comparable` via ordinal | Enables `<` ordering for navigation guards and sorting |
| `JSONValue` (not `Any`) | Type-safe recursive JSON for LiDAR wall/opening data |
| Two-level `NavigationStack` | Outer: project list. Inner: step flow. Prevents nav corruption |
| Polling (not SSE) | Simpler for MVP; backend returns full state each time |
| `wrapErrors` in RealWorkflowClient | Single place to map URLError/DecodingError to typed `APIError` |
