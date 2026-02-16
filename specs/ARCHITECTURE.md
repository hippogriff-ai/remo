# Architecture Diagrams

Mermaid diagrams for the Remo system. Renders on GitHub and in Obsidian.

## System Architecture

```mermaid
graph TB
    subgraph iOS["iOS App (SwiftUI)"]
        UI[Camera / Chat / Canvas UI]
    end

    subgraph Railway["Railway (2 services)"]
        subgraph API["FastAPI Gateway"]
            Routes["/projects/* endpoints"]
            Validation["Photo Validation<br/>(Claude Haiku 4.5 + Pillow)"]
        end

        subgraph Worker["Temporal Worker"]
            WF["DesignProjectWorkflow"]
            Activities["Real Activities<br/>(configurable via<br/>USE_MOCK_ACTIVITIES)"]
            Purge["purge_project_data"]
        end
    end

    subgraph Temporal["Temporal Cloud"]
        TaskQueue["Task Queue: remo-tasks"]
    end

    subgraph External["External Services"]
        R2["Cloudflare R2<br/>(images)"]
        PG["Railway PostgreSQL<br/>(metadata)"]
        Gemini["Gemini 3 Pro Image<br/>(generation + editing)"]
        Claude["Claude Opus 4.6<br/>(intake + room analysis + shopping)"]
        Exa["Exa API<br/>(product search)"]
    end

    UI -->|"HTTPS polling + SSE"| Routes
    Routes -->|"signals / queries"| TaskQueue
    TaskQueue -->|"dispatches"| WF
    WF -->|"execute_activity"| Activities
    WF -->|"execute_activity"| Purge
    Routes -->|"sync call"| Validation
    Activities -->|"API calls"| Gemini
    Activities -->|"API calls"| Claude
    Activities -->|"API calls"| Exa
    Purge -->|"delete_prefix"| R2
    Routes -->|"upload / read"| R2
    Routes -->|"CRUD"| PG
```

## Workflow State Machine

The `DesignProjectWorkflow` drives each design project through a linear pipeline with a restart loop from intake through iteration.

```mermaid
stateDiagram-v2
    [*] --> photos

    photos --> scan : 2+ room photos added
    scan --> analyzing : complete_scan / skip_scan
    analyzing --> intake : room analysis complete (90s max)

    state "Restart Loop" as loop {
        intake --> generation : complete_intake / skip_intake
        generation --> selection : generate_designs succeeds
        generation --> generation_error : ActivityError
        generation_error --> generation : retry_failed_step
        selection --> iteration : select_option(index)
        iteration --> iteration : submit_annotation_edit / submit_text_feedback
        iteration --> iteration_error : ActivityError or ValueError
        iteration_error --> iteration : retry_failed_step
        iteration --> approval : 5 rounds reached (unapproved)
        iteration --> shopping : approve_design (during iteration)
        approval --> shopping : approve_design
    }

    intake --> intake : start_over resets here
    generation --> intake : start_over
    generation_error --> intake : start_over
    selection --> intake : start_over
    iteration --> intake : start_over
    iteration_error --> intake : start_over
    approval --> intake : start_over

    shopping --> shopping_error : ActivityError
    shopping_error --> shopping : retry_failed_step
    shopping --> completed : generate_shopping_list succeeds
    completed --> [*] : 24h purge timer

    photos --> abandoned : 48h timeout + R2 purge
    scan --> abandoned : 48h timeout + R2 purge
    intake --> abandoned : 48h timeout + R2 purge
    generation_error --> abandoned : 48h timeout + R2 purge
    selection --> abandoned : 48h timeout + R2 purge
    iteration --> abandoned : 48h timeout + R2 purge
    iteration_error --> abandoned : 48h timeout + R2 purge
    approval --> abandoned : 48h timeout + R2 purge
    shopping_error --> abandoned : 48h timeout + R2 purge

    photos --> cancelled : cancel_project + R2 purge
    scan --> cancelled : cancel_project + R2 purge
```

## Signal & Query Map

All interactions with the workflow happen via Temporal signals (fire-and-forget) and queries (synchronous read).

```mermaid
graph LR
    subgraph Signals["Signals (17)"]
        direction TB
        S1["add_photo(PhotoData)"]
        S1a["remove_photo(photo_id)"]
        S1b["update_photo_note(photo_id, note)"]
        S1c["confirm_photos()"]
        S2["complete_scan(ScanData)"]
        S3["skip_scan()"]
        S4["complete_intake(DesignBrief)"]
        S5["skip_intake()"]
        S6["select_option(int)"]
        S7["submit_annotation_edit(list)"]
        S8["submit_text_feedback(str)"]
        S9["approve_design()"]
        S10["start_over()"]
        S11["retry_failed_step()"]
        S12["cancel_project()"]
        S13["handle_shopping_streaming()"]
        S14["receive_shopping_result(result)"]
    end

    subgraph Query["Query (1)"]
        Q1["get_state() → WorkflowState"]
    end

    subgraph Steps["Workflow Steps"]
        direction TB
        P["photos"]
        SC["scan"]
        AN["analyzing"]
        I["intake"]
        G["generation"]
        SE["selection"]
        IT["iteration"]
        A["approval"]
        SH["shopping"]
        C["completed"]
    end

    S1 --> P
    S2 --> SC
    S3 --> SC
    S4 --> I
    S5 --> I
    S6 --> SE
    S7 --> IT
    S8 --> IT
    S9 --> IT & A
    S10 --> I & G & SE & IT & A
    S11 --> G & IT & SH
    S12 --> P & SC & I & G & SE & IT & A & SH

    Q1 --> Steps
```

## API Endpoint Map

```mermaid
graph LR
    subgraph Project["Project Lifecycle"]
        POST_create["POST /projects"]
        GET_state["GET /projects/{id}"]
        DELETE_proj["DELETE /projects/{id}"]
    end

    subgraph Photos["Photos Phase"]
        POST_photo["POST /projects/{id}/photos"]
        DELETE_photo["DELETE /projects/{id}/photos/{photo_id}"]
        PATCH_note["PATCH /projects/{id}/photos/{photo_id}/note"]
        POST_confirm["POST /projects/{id}/photos/confirm"]
    end

    subgraph Scan["Scan Phase"]
        POST_scan["POST /projects/{id}/scan"]
        POST_skip_scan["POST /projects/{id}/scan/skip"]
    end

    subgraph Intake["Intake Phase"]
        POST_intake_start["POST /projects/{id}/intake/start"]
        POST_intake_msg["POST /projects/{id}/intake/message"]
        POST_intake_stream["POST /projects/{id}/intake/message/stream (SSE)"]
        POST_intake_confirm["POST /projects/{id}/intake/confirm"]
        POST_skip_intake["POST /projects/{id}/intake/skip"]
    end

    subgraph Selection["Selection Phase"]
        POST_select["POST /projects/{id}/select"]
    end

    subgraph Iteration["Iteration Phase"]
        POST_annotate["POST /projects/{id}/iterate/annotate"]
        POST_feedback["POST /projects/{id}/iterate/feedback"]
        POST_approve["POST /projects/{id}/approve"]
    end

    subgraph Shopping["Shopping Phase"]
        GET_shop_stream["GET /projects/{id}/shopping/stream (SSE)"]
    end

    subgraph Control["Control"]
        POST_retry["POST /projects/{id}/retry"]
        POST_start_over["POST /projects/{id}/start-over"]
    end

    subgraph Health["Health"]
        GET_health["GET /health"]
    end
```

## Edit System (Annotation-First)

The iteration loop uses a single `edit_design` activity that accepts either annotation regions or text feedback. This replaces the previous dual-activity model (inpaint + regen).

```mermaid
flowchart TD
    subgraph Input["User Input (iOS)"]
        Ann["Annotation Edit<br/>1-3 numbered circles<br/>with instructions"]
        Txt["Text Feedback<br/>free-form text"]
    end

    subgraph Signal["Workflow Signal"]
        SigA["submit_annotation_edit<br/>→ action_queue: ('annotation', [...])"]
        SigT["submit_text_feedback<br/>→ action_queue: ('feedback', str)"]
    end

    subgraph Activity["edit_design Activity"]
        Build["_edit_input() builds<br/>EditDesignInput"]
        Exec["Gemini multi-turn chat<br/>(chat_history_key persists)"]
    end

    subgraph Output["Result"]
        Rev["RevisionRecord added<br/>current_image updated<br/>iteration_count++"]
    end

    Ann --> SigA
    Txt --> SigT
    SigA --> Build
    SigT --> Build
    Build --> Exec
    Exec --> Rev

    Rev -->|"< 5 rounds"| Input
    Rev -->|"approve_design"| Done["→ shopping phase"]
    Rev -->|"5 rounds reached"| Approval["→ approval phase"]
```

## Abandonment & Purge Mechanism

Every user-facing wait in the workflow uses `_wait()`, which wraps `workflow.wait_condition()` with a **48-hour abandonment timeout**. If the user takes no action for 48h at any phase, the workflow:

1. Runs `purge_project_data` (best-effort R2 cleanup via `delete_prefix`)
2. Raises `_AbandonedError`
3. Sets `step = "abandoned"` and the workflow completes

The 10 wait points that carry this timeout:

| Phase | Waiting for |
|-------|-------------|
| `photos` | 2+ room photos |
| `scan` | scan data or skip |
| `intake` | design brief or skip |
| `generation` (error) | retry or start_over |
| `selection` | option selected or start_over |
| `iteration` | edit action, approve, or start_over |
| `iteration` (ActivityError) | retry or start_over |
| `iteration` (ValueError) | retry or start_over |
| `approval` | approve or start_over |
| `shopping` (error) | retry |

Additionally, `cancel_project` triggers R2 purge immediately, and `completed` runs a 24h purge timer (not abandonment — the workflow reached success).

```mermaid
flowchart TD
    Wait["_wait(condition, timeout=48h)"]
    Cond{"condition met<br/>within 48h?"}
    Cancel{"_cancelled?"}
    Purge["_try_purge()<br/>R2 delete_prefix"]
    Abandon["_AbandonedError<br/>step = 'abandoned'"]
    Continue["continue workflow"]

    Wait --> Cond
    Cond -->|"yes"| Cancel
    Cond -->|"TimeoutError"| Purge
    Cancel -->|"no"| Continue
    Cancel -->|"yes"| Purge
    Purge --> Abandon
```

## Data Flow: Photo Upload

```mermaid
sequenceDiagram
    participant iOS
    participant API as FastAPI
    participant R2 as Cloudflare R2
    participant Haiku as Claude Haiku 4.5
    participant WF as Workflow

    iOS->>API: POST /projects/{id}/photos<br/>(multipart file)
    API->>API: Pillow checks<br/>(size, format, dimensions)
    alt Pillow fails
        API-->>iOS: 422 validation error
    end
    API->>R2: upload image
    R2-->>API: storage_key
    API->>Haiku: validate photo content<br/>(is it a room?)
    Haiku-->>API: ValidationResult
    alt Not a valid room photo
        API-->>iOS: 200 + passed=false + reasons
    end
    API->>WF: signal add_photo(PhotoData)
    Note over WF: If room_count >= 2:<br/>advance to "scan"
    API-->>iOS: 200 PhotoUploadResponse
```

---

## iOS Architecture (T1)

### SPM Package Dependency Graph

8 local SPM packages with a clean dependency tree. The app target depends on all packages; UI packages depend on RemoModels and RemoNetworking.

```mermaid
graph TD
    App["Remo App Target<br/>(HomeScreen, ProjectFlow,<br/>Router, PollingManager)"]

    subgraph UI["UI Packages (6)"]
        Photo["RemoPhotoUpload<br/>PhotoUploadScreen<br/>CameraView"]
        Chat["RemoChatUI<br/>IntakeChatScreen<br/>ChatBubble, QuickReplyChips"]
        Annot["RemoAnnotation<br/>IterationScreen<br/>AnnotationCanvas"]
        Design["RemoDesignViews<br/>Selection, Generating,<br/>Analyzing, Approval, Output"]
        Shop["RemoShoppingList<br/>ShoppingListScreen<br/>ProductCard"]
        LiDAR["RemoLiDAR<br/>LiDARScanScreen"]
    end

    subgraph Core["Core Packages (2)"]
        Models["RemoModels<br/>Models, ProjectState,<br/>ProjectStep, AnyCodable,<br/>WorkflowClientProtocol"]
        Net["RemoNetworking<br/>MockWorkflowClient,<br/>RealWorkflowClient,<br/>APIError"]
    end

    App --> Photo & Chat & Annot & Design & Shop & LiDAR
    App --> Models & Net

    Photo --> Models & Net
    Chat --> Models & Net
    Annot --> Models & Net
    Design --> Models & Net & Shop
    Shop --> Models & Net
    LiDAR --> Models & Net

    Net --> Models

    style Models fill:#e8d5b7,stroke:#333
    style Net fill:#b7d5e8,stroke:#333
```

### Navigation Flow

`ProjectStep` drives the entire navigation. The `ProjectRouter` maps each step to its screen view, and `ProjectFlowScreen` pushes screens onto the `NavigationStack` when `ProjectState.step` changes.

```mermaid
stateDiagram-v2
    [*] --> HomeScreen

    HomeScreen --> ProjectFlowScreen : tap project / create new

    state ProjectFlowScreen {
        [*] --> PhotoUploadScreen

        PhotoUploadScreen --> LiDARScanScreen : 2+ room photos
        LiDARScanScreen --> AnalyzingRoomScreen : scan / skip
        AnalyzingRoomScreen --> IntakeChatScreen : analysis complete (90s max)
        IntakeChatScreen --> GeneratingScreen : confirm brief / skip
        GeneratingScreen --> DesignSelectionScreen : poll detects selection ready
        DesignSelectionScreen --> IterationScreen : select option
        IterationScreen --> IterationScreen : submit edit (< 5 rounds)
        IterationScreen --> ApprovalScreen : 5 rounds reached
        IterationScreen --> ShoppingGeneratingScreen : approve during iteration
        ApprovalScreen --> ShoppingGeneratingScreen : approve
        ShoppingGeneratingScreen --> OutputScreen : shopping complete
        OutputScreen --> ShoppingListScreen : view shopping (sheet)

        IntakeChatScreen --> IntakeChatScreen : start_over resets here
        DesignSelectionScreen --> IntakeChatScreen : start_over
        IterationScreen --> IntakeChatScreen : start_over
    }
```

### Data Flow: Polling + State Update

Shows how backend state flows through the system. For async steps (generation, analyzing), `PollingManager` polls `GET /projects/{id}` every 2s. For intake chat and shopping, SSE streaming delivers real-time deltas. The `WorkflowState` DTO is decoded, `ProjectState.apply()` maps it into the `@Observable` state, and SwiftUI views reactively update.

```mermaid
sequenceDiagram
    participant View as SwiftUI View
    participant PS as ProjectState<br/>(@Observable)
    participant PM as PollingManager<br/>(actor)
    participant Client as WorkflowClient<br/>(protocol)
    participant API as FastAPI Backend

    View->>Client: user action<br/>(e.g., submitAnnotationEdit)
    Client->>API: POST /projects/{id}/iterate/annotate
    API-->>Client: ActionResponse
    View->>Client: getState(projectId)
    Client->>API: GET /projects/{id}
    API-->>Client: WorkflowState JSON
    Client-->>View: WorkflowState (decoded)
    View->>PS: apply(workflowState)
    Note over PS: Updates step, photos,<br/>currentImage, etc.
    PS-->>View: @Observable triggers<br/>view re-render

    Note over PM,API: For async steps (generation, shopping):
    PM->>Client: getState (every 2s)
    Client->>API: GET /projects/{id}
    API-->>Client: WorkflowState
    alt step changed or error
        PM-->>View: return new WorkflowState
        View->>PS: apply(workflowState)
    else same step, no error
        PM->>PM: sleep 2s, retry
    end
```

### Protocol Injection Architecture

All views depend on `WorkflowClientProtocol`, never a concrete implementation. `RealWorkflowClient` makes HTTP calls (+ SSE streaming) to the FastAPI backend. `MockWorkflowClient` provides hardcoded responses for SwiftUI previews and tests. The swap happens in one line in `RemoApp`.

```mermaid
graph LR
    subgraph Views["SwiftUI Views (12 screens)"]
        direction TB
        V1["PhotoUploadScreen"]
        V2["IntakeChatScreen"]
        V3["IterationScreen"]
        V4["DesignSelectionScreen"]
        V5["..."]
    end

    subgraph Protocol["Abstraction"]
        P["WorkflowClientProtocol<br/>(Sendable, async throws)"]
    end

    subgraph Implementations["Concrete Clients"]
        Real["RealWorkflowClient<br/>(HTTP + SSE to FastAPI,<br/>multipart upload)"]
        Mock["MockWorkflowClient<br/>(in-memory state,<br/>previews + tests)"]
    end

    Views -->|"depends on"| P
    Real -->|"conforms to"| P
    Mock -->|"conforms to"| P

    subgraph App["RemoApp (entry point)"]
        Swap["let client: any WorkflowClientProtocol<br/>= RealWorkflowClient(baseURL: backendURL)"]
    end

    App -->|"injects"| Views

    style Mock stroke-dasharray: 5 5
```

### Error Handling Flow

The codebase uses two error handling patterns: screen-level `@State errorMessage` alerts for user actions, and a global `ErrorOverlay` on `ProjectFlowScreen` for workflow-level errors from the backend.

```mermaid
flowchart TD
    subgraph UserAction["User Action (e.g., submit edit)"]
        Action["async function in view"]
    end

    subgraph ErrorPath["Error Handling"]
        Catch["catch block"]
        Alert["@State errorMessage<br/>→ .alert() sheet"]
    end

    subgraph WorkflowError["Backend Workflow Error"]
        Poll["PollingManager<br/>or getState()"]
        Apply["ProjectState.apply()"]
        ErrField["projectState.error != nil"]
        Overlay["ErrorOverlay<br/>(retryable? → Retry button)"]
    end

    Action -->|"throws"| Catch
    Catch -->|"error.localizedDescription"| Alert

    Poll -->|"WorkflowState"| Apply
    Apply -->|"sets error field"| ErrField
    ErrField -->|"overlay shown"| Overlay
    Overlay -->|"tap Retry"| RetryAPI["retryFailedStep()"]
    RetryAPI -->|"success"| Apply
```
