# Data Layer, Infrastructure & DevOps Analysis

> Remo MVP — Hackathon Build
> Analyst focus: data model, storage, ephemeral lifecycle, server infra, workflow orchestration (Temporal), cost, security, deployment

---

## 1. Data Model

### 1.1 Entity Relationship Overview

```
Project (1) ──── (N) Photo
Project (1) ──── (0..1) LidarScan
Project (1) ──── (0..1) DesignBrief
Project (1) ──── (N) GeneratedImage
Project (1) ──── (N) Revision
Revision (1) ──── (N) LassoRegion
GeneratedImage (1) ──── (0..1) ShoppingList
ShoppingList (1) ──── (N) ProductMatch
```

**Key architectural note:** The project workflow state (current step, lifecycle transitions, timers) is managed by **Temporal** rather than in the database. The PostgreSQL `projects` table stores metadata and references, but Temporal's durable execution is the source of truth for workflow state. The `project.id` maps directly to a Temporal workflow ID.

### 1.2 Entity Schemas

#### Project

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID (v4) | Primary key. **Also the Temporal workflow ID.** Device-stored reconnect token. |
| `device_fingerprint` | String | Opaque device-generated identifier (not PII). Used for rate-limiting only. |
| `has_lidar` | Boolean | Whether LiDAR scan was completed |
| `created_at` | Timestamp | |
| `updated_at` | Timestamp | Updated on every signal to Temporal workflow |

**Fields removed vs. previous design** (now managed by Temporal):
- ~~`status`~~ — Temporal workflow status (running/completed/terminated) + query handler
- ~~`current_step`~~ — Temporal workflow internal state, exposed via query
- ~~`iteration_count`~~ — Tracked in workflow state
- ~~`approved_at`~~ — Temporal timer started on approval signal
- ~~`purge_at`~~ — Temporal timer (24h or 48h) triggers purge activity

The DB `projects` row is lightweight — it exists for relational joins (photos, images, etc.) and rate-limiting queries. All workflow state lives in Temporal.

**Lifecycle state transitions (managed by Temporal workflow):**
```
                    ┌──────────────────────┐
                    │                      │
  ┌─────────┐   signal ┌─────────────┐  signal ┌──────────┐
  │  photos  │────────▶│ generation   │───────▶│ approved │
  └─────────┘         └─────────────┘        └──────────┘
       │                    │                      │
    workflow              48h timer              24h timer
    sleeping              fires                  fires
       │                    │                      │
       ▼                    ▼                      ▼
  (Temporal keeps      ┌────────────┐         ┌─────────┐
   workflow alive —    │ purge      │         │ purge   │
   client signals      │ (activity) │         │(activity)│
   resume)             └────────────┘         └─────────┘
```

Temporal handles:
- **Crash recovery**: Workflow is durable. Client reconnects via project ID (= workflow ID). Temporal returns current state via query.
- **Interruption**: Workflow simply waits for the next signal. No explicit "interrupted" state needed.
- **Abandonment**: A 48h Temporal timer runs alongside each wait-for-signal. If the timer fires first, the workflow executes the purge activity.
- **Post-approval grace**: On approval signal, workflow starts a 24h timer. When it fires, purge activity runs.

#### Photo

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project |
| `type` | Enum | `room`, `inspiration` |
| `storage_key` | String | Object storage path (S3/R2 key) |
| `note` | String (nullable, max 200 chars) | Only for inspiration photos |
| `validation_status` | Enum | `pending`, `passed`, `failed` |
| `validation_error` | String (nullable) | Failure reason if failed |
| `width` | Int | Pixel width |
| `height` | Int | Pixel height |
| `sort_order` | Int | Ordering within type |
| `created_at` | Timestamp | |

#### LidarScan

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project (unique) |
| `storage_key` | String | Object storage path for scan data |
| `room_dimensions` | JSONB | Extracted dimensions: `{width, length, height, openings: [{type, position, dimensions}]}` |
| `raw_format` | String | Format identifier (e.g., `usdz`, `obj`, `custom`) |
| `file_size_bytes` | Int | |
| `created_at` | Timestamp | |

#### DesignBrief

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project (unique) |
| `intake_mode` | Enum | `quick`, `full`, `open`, `skipped` |
| `brief_data` | JSONB | The structured brief (see spec section 4.5) |
| `conversation_history` | JSONB | Array of `{role, content, timestamp}` for intake chat |
| `created_at` | Timestamp | |
| `confirmed_at` | Timestamp (nullable) | When user confirmed the summary |

`brief_data` schema:
```json
{
  "room_type": "living room",
  "occupants": "couple with 2 dogs",
  "pain_points": ["too dark", "dated furniture"],
  "keep_items": ["built-in bookshelf"],
  "style_profile": {
    "lighting": "warm",
    "colors": ["earth tones", "warm neutrals"],
    "textures": ["linen", "wool", "natural wood"],
    "clutter_level": "curated",
    "mood": "warm and inviting retreat"
  },
  "constraints": ["pet-friendly", "durable fabrics"],
  "inspiration_notes": [
    {
      "photo_index": 0,
      "note": "Love the warm lighting and layered textiles",
      "agent_clarification": "Match exact warmth level, use layered textile approach"
    }
  ]
}
```

#### GeneratedImage

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project |
| `type` | Enum | `initial_option`, `revision`, `overlay` |
| `option_index` | Int (nullable) | 0 or 1 for initial options; null for revisions |
| `revision_number` | Int (nullable) | 1-5 for revisions; null for initial options |
| `storage_key` | String | Object storage path |
| `selected` | Boolean | Whether this option was chosen (for initial_option type) |
| `is_final` | Boolean | True for the approved design |
| `generation_prompt` | Text | The full prompt sent to the model |
| `generation_model` | String | Model identifier used |
| `generation_duration_ms` | Int | Time taken to generate |
| `created_at` | Timestamp | |

#### Revision

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project |
| `revision_number` | Int (1-5) | |
| `type` | Enum | `lasso`, `full_regenerate` |
| `base_image_id` | UUID | FK -> GeneratedImage (the image being edited) |
| `result_image_id` | UUID (nullable) | FK -> GeneratedImage (the output); null until generation completes |
| `overlay_image_id` | UUID (nullable) | FK -> GeneratedImage (lasso overlay); null for full_regenerate |
| `feedback_text` | Text (nullable) | For full_regenerate type |
| `edit_payload` | JSONB (nullable) | For lasso type — the structured edit instruction payload |
| `status` | Enum | `pending`, `generating`, `completed`, `failed` |
| `created_at` | Timestamp | |

#### LassoRegion

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `revision_id` | UUID | FK -> Revision |
| `region_number` | Int (1-3) | Priority order |
| `path_points` | JSONB | Array of `{x, y}` normalized coordinates (0-1 range) |
| `bounding_box` | JSONB | `{x, y, width, height}` normalized |
| `action` | Enum | `replace`, `remove`, `change_finish`, `resize`, `reposition` |
| `instruction` | Text (min 10 chars) | |
| `avoid_tokens` | JSONB | Array of strings |
| `style_nudges` | JSONB | Array of selected nudge tags |

#### ShoppingList

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `project_id` | UUID | FK -> Project |
| `generated_image_id` | UUID | FK -> GeneratedImage (the approved design) |
| `total_estimated_cost` | Decimal | Sum of all matched product prices |
| `created_at` | Timestamp | |

#### ProductMatch

| Field | Type | Notes |
|-------|------|-------|
| `id` | UUID | |
| `shopping_list_id` | UUID | FK -> ShoppingList |
| `category_group` | String | e.g., "Seating", "Lighting", "Rugs & Flooring" |
| `item_description` | String | What the AI identified in the design |
| `product_name` | String | Matched product name |
| `retailer` | String | Retailer name |
| `price_cents` | Int | Price in cents |
| `currency` | String | Default "USD" |
| `product_url` | String | Direct purchase URL |
| `image_url` | String (nullable) | Product thumbnail URL |
| `dimensions` | JSONB (nullable) | `{width, depth, height, unit}` |
| `confidence_score` | Float (0-1) | Match quality |
| `why_matched` | Text | AI-generated explanation |
| `fit_status` | Enum (nullable) | `fits`, `tight`, `too_large`, null (no LiDAR) |
| `fit_detail` | Text (nullable) | e.g., "Your wall is 8ft — this bookshelf is 6ft wide" |
| `sort_order` | Int | Display order within group |
| `search_fallback_url` | String (nullable) | Google Shopping link if low confidence |

---

## 2. Storage Strategy

### 2.1 Image / Asset Storage

**Recommendation: Cloudflare R2**

| Factor | R2 | S3 | Rationale |
|--------|----|----|-----------|
| Egress cost | Free | $0.09/GB | Generated images will be downloaded multiple times; free egress is significant |
| S3 API compat | Yes | Yes | Same SDK, easy migration |
| CDN integration | Native (Cloudflare) | Requires CloudFront setup | R2 + Cloudflare CDN is zero-config |
| Object lifecycle | Supported | Supported | Both support TTL-based expiry rules |
| Hackathon speed | Faster setup | More knobs to turn | R2 wins for speed |

**Storage layout:**
```
/projects/{project_id}/
  photos/
    room_0.jpg
    room_1.jpg
    inspiration_0.jpg
    inspiration_1.jpg
    inspiration_2.jpg
  lidar/
    scan.usdz          (or .obj / .ply)
    dimensions.json     (extracted structured data)
  generated/
    option_0.png
    option_1.png
    revision_1.png
    revision_1_overlay.png
    revision_2.png
    ...
    final.png
```

**Object lifecycle rules:**
- All objects under `/projects/{project_id}/` are deleted by the Temporal purge activity when the workflow's timer fires
- R2 lifecycle rule as safety net: delete objects with `x-amz-meta-purge-after` header past expiry (set to `created_at + 72h`, buffer beyond Temporal-driven purge)

**Estimated storage per project:**
| Asset | Count | Size Each | Total |
|-------|-------|-----------|-------|
| Room photos | 2 | ~3 MB | 6 MB |
| Inspiration photos | 0-3 | ~3 MB | 0-9 MB |
| LiDAR scan | 0-1 | ~5-15 MB | 0-15 MB |
| Generated images | 4-12 | ~2 MB | 8-24 MB |
| Overlays | 0-5 | ~2 MB | 0-10 MB |
| **Total per project** | | | **14-64 MB** (avg ~35 MB) |

### 2.2 Metadata Database

**Recommendation: PostgreSQL on Neon (serverless)**

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **PostgreSQL (Neon)** | JSONB for flexible schemas, strong relational modeling, serverless with scale-to-zero, generous free tier | Slightly more setup than SQLite | **Winner** |
| SQLite (Turso) | Simple, serverless via Turso, cheap | No JSONB, harder JSON querying, limited concurrent writes | Good fallback |
| DynamoDB | Infinitely scalable, TTL built-in | Awkward relational queries, IAM complexity, overshoot for hackathon | Overkill |

**Why PostgreSQL:**
- JSONB columns for `brief_data`, `conversation_history`, `path_points`, `edit_payload`, `room_dimensions` — avoids over-normalization while keeping queryability
- Neon serverless: scale-to-zero billing, branching for dev/staging, no server management
- Free tier: 0.5 GB storage, 100 hours compute/month — more than enough for hackathon
- With Temporal managing workflow state, PostgreSQL is only responsible for domain data (photos, images, briefs, shopping lists) — no workflow state or purge scheduling queries needed

### 2.3 LiDAR Data Storage

**Format strategy:**
- **Raw scan**: Store as USDZ (Apple's native format from RoomPlan API) in R2. This preserves full fidelity for future use.
- **Extracted dimensions**: Parse the RoomPlan output on-device into a structured JSON summary before upload. Store this JSON both:
  - In R2 as `dimensions.json` (backup)
  - In PostgreSQL `lidar_scans.room_dimensions` JSONB column (queryable)

**Extracted dimensions schema:**
```json
{
  "room": { "width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters" },
  "walls": [
    { "id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0 },
    { "id": "wall_1", "width": 5.8, "height": 2.7, "orientation": 90 }
  ],
  "openings": [
    { "type": "door", "wall_id": "wall_0", "width": 0.9, "height": 2.1, "position": { "x": 1.5 } },
    { "type": "window", "wall_id": "wall_1", "width": 1.2, "height": 1.0, "position": { "x": 2.0, "y": 1.0 } }
  ],
  "floor_area_sqm": 24.36
}
```

**Why this approach:**
- Raw USDZ is large (5-15 MB) but stored cheaply in R2 and only needed for potential re-processing
- Structured JSON is what the AI generation and shopping list logic actually consume
- Parsing happens on-device where RoomPlan API is available — server never needs to parse USDZ

---

## 3. Ephemeral Data Lifecycle

### 3.1 Temporal-Driven Lifecycle (replaces cron-based purge)

With Temporal, the ephemeral lifecycle is modeled as **durable timers** inside the project workflow itself. No external cron jobs or scheduled workers needed.

**How it works:**

```python
# Pseudocode for the Temporal workflow's timer logic

async def project_workflow(ctx, project_id):
    # ... workflow steps (photos, scan, intake, generation, iteration) ...

    # At every "wait for user input" point:
    result = await workflow.wait_for_signal_or_timeout(
        signals=["user_action"],
        timeout=timedelta(hours=48)  # Abandonment timer
    )

    if result.timed_out:
        # 48h with no activity — abandon and purge
        await workflow.execute_activity(purge_project, project_id)
        return  # Workflow completes

    # ... continue workflow ...

    # On approval:
    await workflow.execute_activity(generate_shopping_list, project_id)

    # Start 24h grace period
    await workflow.sleep(timedelta(hours=24))

    # Grace period expired — purge
    await workflow.execute_activity(purge_project, project_id)
```

**Advantages over cron-based purge:**
| Aspect | Cron Approach (previous) | Temporal Approach |
|--------|-------------------------|-------------------|
| Timer precision | 15-minute polling intervals | Exact to the second |
| Orphan risk | Cron might miss; needs safety nets | Timer is part of the workflow — cannot be orphaned |
| Code complexity | Separate cron service + R2 lifecycle rules + DB queries | Timer logic is inline in the workflow code |
| Observability | Must query DB + check cron logs | Temporal UI shows every workflow's timer state |
| Testing | Hard to test cron timing | `workflow.sleep()` is easily time-skipped in tests |

### 3.2 Timer Behavior by State

| State | Timer | Fires After | Action |
|-------|-------|-------------|--------|
| Waiting for photos | Abandonment | 48h since last signal | Execute `purge_project` activity |
| Waiting for scan decision | Abandonment | 48h since last signal | Execute `purge_project` activity |
| Waiting for intake | Abandonment | 48h since last signal | Execute `purge_project` activity |
| Waiting for option selection | Abandonment | 48h since last signal | Execute `purge_project` activity |
| Waiting for iteration input | Abandonment | 48h since last signal | Execute `purge_project` activity |
| Approved (grace period) | Grace | 24h since approval | Execute `purge_project` activity |

**Timer reset on activity:** Every signal from the client resets the 48h abandonment timer. In Temporal, this is implemented by racing the timer against signal receipt in a loop:

```python
while True:
    signal_or_timeout = await workflow.wait_for_signal_or_timeout(
        signals=["photos_uploaded", "scan_completed", "scan_skipped",
                 "intake_message", "intake_confirmed", "intake_skipped",
                 "option_selected", "revision_submitted", "design_approved"],
        timeout=timedelta(hours=48)
    )
    if signal_or_timeout.timed_out:
        await workflow.execute_activity(purge_project, project_id)
        return
    # Process the signal, advance workflow state
    handle_signal(signal_or_timeout.signal)
```

### 3.3 Purge Activity

The `purge_project` activity (executed by Temporal worker) performs:
1. Delete all R2 objects under `/projects/{project_id}/` (list + batch delete)
2. Delete all PostgreSQL rows for this project (CASCADE from `projects` table)
3. Log the purge event (project_id, timestamp, reason: "abandoned" or "grace_expired")

**Idempotency:** The purge activity is idempotent — safe to retry. Deleting already-deleted R2 objects and DB rows is a no-op.

### 3.4 R2 Safety Net (Belt and Suspenders)

Even with Temporal, keep one safety net:
- Each uploaded object gets `x-amz-meta-purge-after` set to `created_at + 72h`
- R2 lifecycle rule deletes objects past this header
- This catches truly orphaned objects (e.g., if Temporal cluster loses state — extremely unlikely but cheap to protect against)

### 3.5 Device-Side Storage

- Store only: `[{project_id: UUID, created_at: Date, last_step: String}]`
- Use iOS `UserDefaults` (simple key-value)
- On app launch: iterate stored project IDs, call `GET /projects/{id}/state` for each
  - API server queries Temporal workflow status for the given workflow ID
- Remove IDs where server returns 404 (workflow completed/terminated = purged)
- Maximum stored IDs: 10 (prune oldest on overflow — unlikely for MVP)

---

## 4. Server Infrastructure

### 4.1 Hosting Platform

| Component | Platform | Rationale |
|-----------|----------|-----------|
| **API server** | Railway | Persistent process, fast deploys, good DX |
| **Temporal server** | Temporal Cloud (managed) | Zero ops, handles persistence/clustering, free tier available |
| **Temporal worker** | Railway (same or separate service) | Runs activities (image gen, validation, purge); co-located with API |
| **Object storage** | Cloudflare R2 | Free egress, S3-compatible, lifecycle rules |
| **Metadata DB** | Neon PostgreSQL | Serverless, scale-to-zero, free tier |
| **CDN** | Cloudflare (automatic with R2) | Free, global |

**Temporal Cloud vs. self-hosted:**

| Factor | Temporal Cloud | Self-hosted | Verdict |
|--------|---------------|-------------|---------|
| Ops burden | Zero | Must run Temporal server + Cassandra/PostgreSQL | Cloud wins for hackathon |
| Free tier | Yes (limited namespace) | Free software, but you pay for infra | Cloud wins |
| Reliability | Managed SLA | You manage uptime | Cloud wins |
| Cost at scale | $0.25/1000 actions (~$25/mo at 100k actions) | Infra cost only | Cloud until significant scale |
| Latency | ~50ms overhead | Lower (co-located) | Negligible for our use case |

**Recommendation: Temporal Cloud** for hackathon. Self-host later if cost or latency matters.

**What this replaces:**
- ~~Redis / BullMQ~~ — Temporal handles job queuing, retries, and scheduling natively
- ~~Cloudflare Worker cron~~ — Temporal timers replace cron-based purge
- ~~Custom state machine code~~ — Temporal workflow IS the state machine

### 4.2 API Server Architecture

**Runtime: Python (FastAPI)**

Recommendation: **FastAPI (Python)** — reasoning:
- AI/ML ecosystem is Python-native (prompt construction, Exa SDK, image processing)
- FastAPI has native async, WebSocket/SSE support, auto-generated OpenAPI docs
- Temporal has a first-class Python SDK (`temporalio`)
- Railway has first-class Python support

The API server is a **thin coordination layer**:
- Receives HTTP requests from iOS client
- Sends Temporal signals to advance the workflow
- Queries Temporal workflow state for resume/status endpoints
- Handles file uploads to R2 (photos, LiDAR scans)

It does NOT run long computations — those are Temporal activities.

### 4.3 Temporal Workflow & Activities

#### Workflow: `DesignProjectWorkflow`

```
Workflow ID: project_id (UUID)
Task Queue: "remo-design"
```

**Workflow pseudocode (Python SDK):**

```python
@workflow.defn
class DesignProjectWorkflow:
    def __init__(self):
        self.state = "photos"
        self.iteration_count = 0
        self.has_lidar = False
        self.latest_image_id = None
        self.selected_option = None

    @workflow.run
    async def run(self, project_id: str):
        # Phase 1: Wait for photos
        await self._wait_for_step("photos", ["photos_ready"])

        # Phase 2: Wait for scan decision
        await self._wait_for_step("scan", ["scan_completed", "scan_skipped"])

        # Phase 3: Wait for intake
        await self._wait_for_step("intake", ["intake_confirmed", "intake_skipped"])

        # Phase 4: Generate 2 options (Temporal activities)
        self.state = "generating"
        options = await workflow.execute_activity(
            generate_design_options,
            args=[project_id],
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=RetryPolicy(max_attempts=3)
        )

        # Phase 5: Wait for option selection
        self.state = "selection"
        await self._wait_for_step("selection", ["option_selected", "start_over"])
        if self._last_signal == "start_over":
            # Loop back to intake (Temporal supports this naturally)
            # ... restart from Phase 3

        # Phase 6: Iteration loop (up to 5 rounds)
        self.state = "iteration"
        while self.iteration_count < 5:
            signal = await self._wait_for_step(
                "iteration",
                ["revision_submitted", "design_approved", "start_over"]
            )
            if signal == "design_approved":
                break
            if signal == "start_over":
                self.iteration_count = 0
                # ... restart from intake
            # Execute revision activity
            result = await workflow.execute_activity(
                generate_revision,
                args=[project_id, self._revision_payload],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(max_attempts=3)
            )
            self.iteration_count += 1

        # Phase 7: Generate shopping list
        self.state = "generating_shopping_list"
        await workflow.execute_activity(
            generate_shopping_list,
            args=[project_id],
            start_to_close_timeout=timedelta(minutes=3),
            retry_policy=RetryPolicy(max_attempts=3)
        )

        # Phase 8: Approved — 24h grace period
        self.state = "approved"
        await workflow.sleep(timedelta(hours=24))

        # Phase 9: Purge
        await workflow.execute_activity(
            purge_project,
            args=[project_id],
            start_to_close_timeout=timedelta(minutes=2)
        )

    async def _wait_for_step(self, step_name, expected_signals):
        """Wait for a client signal or 48h abandonment timeout."""
        self.state = step_name
        while True:
            try:
                signal = await workflow.wait_condition(
                    lambda: self._pending_signal is not None,
                    timeout=timedelta(hours=48)
                )
                if self._pending_signal in expected_signals:
                    result = self._pending_signal
                    self._pending_signal = None
                    return result
                # Unexpected signal — ignore or log
                self._pending_signal = None
            except asyncio.TimeoutError:
                # 48h abandonment
                await workflow.execute_activity(purge_project, ...)
                raise  # End workflow

    @workflow.signal
    async def user_action(self, signal_name: str, payload: dict):
        """Receive signals from the API server."""
        self._pending_signal = signal_name
        self._signal_payload = payload

    @workflow.query
    def get_state(self) -> dict:
        """Query handler — returns current workflow state to API server."""
        return {
            "state": self.state,
            "iteration_count": self.iteration_count,
            "has_lidar": self.has_lidar,
            "latest_image_id": self.latest_image_id,
        }
```

#### Activities (run on Temporal worker)

| Activity | What It Does | Timeout | Retries |
|----------|-------------|---------|---------|
| `validate_photo` | Call vision model to validate a single photo | 30s | 3 |
| `generate_design_options` | Call image model twice (parallel) for 2 options; store results in R2 + DB | 5 min | 3 |
| `generate_revision` | Call image model for a single revision; store in R2 + DB | 5 min | 3 |
| `generate_shopping_list` | Analyze final image, call Exa search per item, store results in DB | 3 min | 3 |
| `purge_project` | Delete all R2 objects + DB rows for a project | 2 min | 5 |
| `send_progress_event` | Push SSE event to connected client (via API server) | 10s | 2 |

**Activity retry policy:**
- All activities use `RetryPolicy(initial_interval=5s, backoff_coefficient=2, max_attempts=3)`
- `purge_project` gets 5 attempts (critical to ensure cleanup)
- Failed generation activities don't consume iteration count (per spec) — the workflow simply retries or exposes failure to client

**Heartbeating for long activities:**
- `generate_design_options` and `generate_revision` heartbeat every 10s
- This allows Temporal to detect stuck workers and reassign the activity
- Heartbeat payloads include progress info (e.g., "generating option 1 of 2") which can be forwarded to client via SSE

### 4.4 Real-Time Progress Updates

**Recommendation: Server-Sent Events (SSE)**

SSE remains the right choice for client-facing progress. The flow changes slightly with Temporal:

```
iOS Client ──POST signal──▶ API Server ──signal──▶ Temporal Workflow
                                                        │
                                                   executes activity
                                                        │
                                                   activity heartbeats
                                                        │
API Server ◀──activity heartbeat/completion──── Temporal Worker
     │
     └──SSE push──▶ iOS Client
```

**Implementation detail:** The API server subscribes to Temporal workflow events (via `WorkflowHandle.describe()` polling or the Temporal visibility API) and forwards progress to connected SSE clients. Alternatively, activities can call back to the API server directly with progress updates.

**Simpler approach for hackathon:** Activities write progress to a Redis pub/sub channel keyed by project_id. The API server's SSE endpoint subscribes to that channel and forwards events to the client. This avoids tight coupling between activities and the API server.

```
Activity ──publish──▶ Redis pub/sub (channel: project:{id}:progress)
                              │
API SSE endpoint ◀──subscribe─┘──push──▶ iOS Client
```

**SSE event stream:**
```
event: progress
data: {"status": "generating", "step": "option_1", "message": "Designing your space..."}

event: progress
data: {"status": "generating", "step": "option_2", "progress": 0.7}

event: complete
data: {"status": "completed", "result": {"option_0_url": "...", "option_1_url": "..."}}

event: error
data: {"status": "failed", "message": "Something went wrong. Tap to retry.", "retryable": true}
```

**Note on Redis:** With Temporal handling job queuing and retries, Redis is only needed for real-time SSE pub/sub. This is a much lighter use case — **Upstash free tier** (10k commands/day) is more than sufficient. Alternatively, skip Redis entirely and use simple HTTP polling from the SSE endpoint to Temporal's query API (acceptable latency for hackathon).

### 4.5 CDN for Generated Images

- Cloudflare CDN is automatic when serving from R2 via a custom domain or R2 public bucket URL
- Set `Cache-Control: public, max-age=86400` on generated images (they're immutable once created)
- Use signed URLs for access control (project-scoped, time-limited)
- Cache hit ratio should be high: each image is viewed multiple times during iteration

---

## 5. Temporal Workflow Design (replaces custom state machine)

### 5.1 Why Temporal Replaces the Custom State Machine

In the previous design, we had:
- A `current_step` enum in PostgreSQL tracking workflow position
- Custom transition validation code in the API server
- Redis + BullMQ for async job processing
- Cloudflare Worker cron for purge scheduling
- Manual crash recovery logic (check DB state, replay)

Temporal replaces ALL of this with a single workflow definition:

| Previous Component | Temporal Equivalent |
|-------------------|---------------------|
| `project.current_step` enum in DB | Workflow internal state (queryable) |
| Transition validation code | Workflow control flow (if/while/await) |
| BullMQ job queue + Redis | Temporal activity task queue |
| Job retries + exponential backoff | `RetryPolicy` on activities |
| Cron-based purge worker | `workflow.sleep(timedelta(hours=24))` |
| 48h abandonment detection | Timer racing against signal receipt |
| Crash recovery logic | Automatic — Temporal replays workflow from event history |
| DB-tracked job status | Activity status in Temporal (visible in UI) |

### 5.2 Signal-Driven Architecture

The iOS client never directly calls workflow code. Instead:

1. **Client sends HTTP request** to API server (e.g., `POST /projects/{id}/photos`)
2. **API server processes the request** (e.g., uploads photo to R2, writes DB row)
3. **API server sends a Temporal signal** to the workflow: `workflow.signal("photos_ready", payload)`
4. **Workflow wakes up**, processes the signal, advances to next step (may execute activities)
5. **Client queries workflow state** via `GET /projects/{id}/state` (API server calls Temporal query)

This keeps the API server as a thin translation layer between HTTP and Temporal signals.

### 5.3 Workflow Queries (for Resume)

Temporal queries allow reading workflow state without advancing it:

```python
# API server handler for GET /projects/{id}/state
async def get_project_state(project_id: str):
    handle = temporal_client.get_workflow_handle(project_id)
    try:
        state = await handle.query(DesignProjectWorkflow.get_state)
        return state  # {"state": "iteration", "iteration_count": 2, ...}
    except WorkflowNotFoundError:
        raise HTTPException(404)  # Workflow completed/purged
```

The iOS app on launch:
1. Reads local project IDs
2. Calls `GET /projects/{id}/state` for each
3. Server queries Temporal — if workflow is running, returns current state; if not found, returns 404
4. App navigates to the appropriate screen or removes the stale project ID

### 5.4 Temporal Task Queues

| Queue | Used By | Purpose |
|-------|---------|---------|
| `remo-design` | `DesignProjectWorkflow` | Main workflow task queue |
| `remo-ai` | AI activities (generate, validate) | Separate queue allows scaling AI workers independently |
| `remo-cleanup` | `purge_project` activity | Low priority; separate so purge doesn't compete with AI activities |

For hackathon: a single worker process can poll all 3 queues. Scale later by running separate worker processes per queue.

### 5.5 Error Handling in Temporal

| Scenario | Temporal Behavior | Client Experience |
|----------|------------------|-------------------|
| Image generation API fails | Activity retries (3 attempts, exponential backoff) | Client sees "generating..." longer; if all retries fail, workflow signals error |
| Worker crashes mid-activity | Temporal detects heartbeat timeout, reassigns to another worker | Transparent to client; slight delay |
| API server crashes | Workflow is unaffected (lives in Temporal); client reconnects and queries state | Client sees "resume" on reopen |
| Temporal Cloud outage | Workflows are paused; resume when Temporal is back | Client sees loading/error; retry on reconnect |
| Client disconnects mid-generation | Workflow continues; result stored; client queries on reconnect | Seamless resume |

---

## 6. Cost Estimation

### 6.1 Per-Project Cost Breakdown

| Resource | Calculation | Cost per Project |
|----------|-------------|------------------|
| **Image generation** (GPT-Image / DALL-E 3) | 4 images avg (2 options + 2 revisions) x ~$0.04-0.08/image | $0.16 - $0.32 |
| **Photo validation** (Vision API) | 2-5 photos x ~$0.01/call | $0.02 - $0.05 |
| **Intake chat** (GPT-4o-mini) | ~10 messages x ~$0.001/msg | ~$0.01 |
| **Shopping list** (Exa search) | 6-10 searches x $0.01/search | $0.06 - $0.10 |
| **Shopping list** (LLM extraction) | 1 call ~$0.02 | $0.02 |
| **Object storage** (R2) | ~35 MB x 2 days avg retention | ~$0.0005 |
| **CDN egress** (Cloudflare) | ~100 MB served | Free |
| **Temporal Cloud** | ~100 actions/project x $0.25/1000 | ~$0.025 |
| **Compute** (API server + worker) | Amortized per request | ~$0.01 |
| **Total per project** | | **$0.30 - $0.53** |

### 6.2 Monthly Cost at Scale

| Scale | Projects/mo | AI costs | Temporal | Infra (fixed) | Storage | Total |
|-------|-------------|----------|----------|---------------|---------|-------|
| **Hackathon** (testing) | 50 | $15 | $1 | $10 | $1 | **~$27** |
| **Soft launch** | 100 | $35 | $3 | $10 | $2 | **~$50** |
| **Growing** | 500 | $175 | $13 | $20 | $5 | **~$213** |
| **Active** | 1,000 | $350 | $25 | $30 | $10 | **~$415** |

**Fixed infrastructure costs:**
| Service | Free Tier | Paid (when needed) |
|---------|-----------|-------------------|
| Railway (API server + worker) | $5/mo starter | $20/mo for more RAM |
| Neon PostgreSQL | Free (0.5 GB) | $19/mo pro |
| Temporal Cloud | Free tier (limited) | ~$0.25/1000 actions |
| Upstash Redis (SSE pub/sub only) | Free (10k cmds/day) | $10/mo pro |
| Cloudflare R2 | Free (10 GB, 1M requests) | $0.015/GB/mo |

**What we no longer need** (vs. previous design):
- ~~Cloudflare Worker for cron~~ — Temporal timers handle purge
- ~~Redis for BullMQ~~ — Temporal handles job queuing (Redis only needed for SSE pub/sub, much lighter usage)

**Hackathon budget: ~$25-50/month covers everything comfortably.**

---

## 7. Security

### 7.1 Rate Limiting (No Auth)

Without user accounts, abuse prevention relies on device fingerprinting and rate limits.

**Rate limit strategy:**

| Endpoint | Limit | Window | Key |
|----------|-------|--------|-----|
| `POST /projects` (create) | 5 | 24 hours | device_fingerprint |
| `POST /projects/{id}/photos` | 20 | 1 hour | project_id |
| `POST /projects/{id}/generate` | 10 | 1 hour | project_id |
| `POST /projects/{id}/revisions` | 5 | 1 hour | project_id |
| All endpoints | 100 | 1 minute | IP address |

**Implementation:** Use a Redis-based sliding window rate limiter (e.g., `slowapi` for FastAPI). Since we already have Upstash Redis for SSE pub/sub, rate limiting adds negligible load.

**Device fingerprint:**
- Generated client-side: `UIDevice.current.identifierForVendor` (resets on app reinstall — acceptable for MVP)
- Sent as `X-Device-ID` header on all requests
- Not PII — opaque UUID, only used for rate limiting

### 7.2 Abuse Prevention

| Threat | Mitigation |
|--------|-----------|
| Image spam (upload garbage to consume storage) | Photo validation rejects non-room images; rate limit on uploads |
| Generation abuse (burn AI credits) | Rate limit on generate endpoints; max 5 iterations enforced in Temporal workflow (not bypassable via API) |
| Project spam (create thousands of projects) | 5 projects/device/day limit; projects auto-purge via Temporal timers |
| Large file uploads | Max file size: 20 MB per photo, 50 MB for LiDAR scan; enforced at API gateway |
| Prompt injection via user text | Sanitize user inputs before including in generation prompts; use structured prompt templates |
| Scraping generated images | Signed URLs with 1-hour expiry; no public listing of project IDs |
| Temporal signal abuse | API server validates signals before forwarding to Temporal; rate limiting on signal endpoints |

**Temporal-specific security note:** The iteration limit (max 5) is enforced inside the Temporal workflow, not in the API server. Even if someone bypasses the API rate limiter, the workflow itself will reject signals beyond the iteration cap. This is more robust than API-only enforcement.

### 7.3 Ephemeral Data Guarantees

- **Data deletion is hard-delete** — rows are removed from PostgreSQL, objects are removed from R2. No soft-delete.
- **Temporal guarantees timer execution** — unlike cron, Temporal timers are durable. If the Temporal cluster restarts, timers resume from where they left off. The 24h/48h purge will fire.
- **Temporal workflow history** — Temporal retains event history for completed workflows (configurable retention). Set retention to 24 hours to match our privacy guarantees. After retention, Temporal deletes all workflow data.
- **No backups retain purged data** — Neon point-in-time recovery window set to 24 hours. After PITR window, purged data is unrecoverable.
- **Audit log** — Temporal event history IS the audit log for workflow lifecycle. For DB/R2 purge events, the purge activity logs to a separate lightweight table (retained 7 days).
- **No analytics on user content** — We never analyze photos or design preferences outside the active session.

### 7.4 Transport Security

- All API traffic over HTTPS (enforced by Railway/Cloudflare)
- R2 signed URLs for all object access (no public bucket)
- Temporal Cloud connections use mTLS (mutual TLS) — API server and workers authenticate to Temporal with client certificates
- CORS restricted to the app's bundle identifier

---

## 8. Deployment

### 8.1 CI/CD Pipeline

**Recommendation: GitHub Actions -> Railway**

```
Push to main ──▶ GitHub Actions ──▶ Run tests ──▶ Deploy to Railway (auto)
     │                                    │
     └──▶ Push to staging branch ──▶ Deploy to staging environment
```

**Pipeline steps:**
1. **Lint & type check** — `ruff` + `mypy`
2. **Unit tests** — `pytest` (including Temporal workflow tests with time-skipping)
3. **Integration tests** — Test API endpoints against a test database (Neon branch) + Temporal test server
4. **Deploy** — Railway auto-deploys on push to `main`

### 8.2 Temporal Workflow Testing

Temporal's Python SDK provides a test environment with time-skipping:

```python
async def test_abandonment_purge():
    """Test that a project is purged after 48h of inactivity."""
    async with await WorkflowEnvironment.start_time_skipping() as env:
        client = env.client
        handle = await client.start_workflow(
            DesignProjectWorkflow.run,
            args=["test-project-id"],
            id="test-project-id",
            task_queue="remo-design"
        )
        # Don't send any signals — simulate abandonment
        # Time-skip 48 hours
        await env.sleep(timedelta(hours=48))
        # Verify workflow completed and purge activity was called
        result = await handle.result()
        assert result.purged == True
```

This is a major advantage for testing ephemeral lifecycle behavior without waiting real hours.

### 8.3 Environment Management

| Environment | Branch | Database | Temporal | Purpose |
|-------------|--------|----------|----------|---------|
| **Development** | `dev` | Neon branch | Temporal dev server (local) | Local development |
| **Staging** | `staging` | Neon branch | Temporal Cloud (staging namespace) | Pre-production testing |
| **Production** | `main` | Neon main | Temporal Cloud (production namespace) | Live |

**Local development:** Use `temporalite` (single-binary Temporal dev server) for local development. No Docker required.

### 8.4 Railway Configuration

**`Procfile` (multi-process):**
```
web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: python -m app.temporal_worker
```

The worker process registers all Temporal activities and polls the task queues. For hackathon, a single worker process handles all queues. Scale later by splitting into separate services per queue.

### 8.5 Database Migrations

**Tool: Alembic (Python/SQLAlchemy)**

- Migrations run automatically on deploy (pre-start hook)
- Rollback strategy: Alembic `downgrade` for the last migration
- For hackathon: schema changes are frequent, keep migrations simple
- Note: Temporal workflow versioning is separate from DB migrations. Use Temporal's `workflow.patched()` for workflow code changes.

### 8.6 Environment Variables

| Variable | Source | Notes |
|----------|--------|-------|
| `DATABASE_URL` | Neon | Connection string with pooling |
| `REDIS_URL` | Upstash | For SSE pub/sub and rate limiting |
| `R2_ACCOUNT_ID` | Cloudflare | |
| `R2_ACCESS_KEY_ID` | Cloudflare | |
| `R2_SECRET_ACCESS_KEY` | Cloudflare | |
| `R2_BUCKET_NAME` | Cloudflare | Per environment |
| `TEMPORAL_HOST` | Temporal Cloud | e.g., `remo.tmprl.cloud:7233` |
| `TEMPORAL_NAMESPACE` | Temporal Cloud | e.g., `remo-production` |
| `TEMPORAL_TLS_CERT` | Temporal Cloud | mTLS client certificate (base64) |
| `TEMPORAL_TLS_KEY` | Temporal Cloud | mTLS client key (base64) |
| `OPENAI_API_KEY` | OpenAI | For image generation + vision |
| `EXA_API_KEY` | Exa | For product search |
| `APP_ENV` | Railway | `development`, `staging`, `production` |

### 8.7 Monitoring (Lightweight for Hackathon)

| What | Tool | Cost |
|------|------|------|
| **Workflow visibility** | Temporal Cloud UI | Free (included) |
| **API metrics** | Railway built-in | Free |
| **Error tracking** | Sentry (free tier) | Free (5k events/mo) |
| **Logs** | Railway log drain -> Axiom | Free tier |
| **Uptime** | BetterUptime (free tier) | Free |

**Temporal Cloud UI** is a significant observability win — you can see every workflow's current state, signal history, activity execution timeline, and timer schedules without building any custom dashboards.

---

## 9. API Surface Summary

Quick reference of the core API endpoints the iOS client will call:

| Method | Endpoint | Purpose | Temporal Interaction |
|--------|----------|---------|---------------------|
| `POST` | `/api/v1/projects` | Create new project | Starts `DesignProjectWorkflow` |
| `GET` | `/api/v1/projects/{id}/state` | Get project state (for resume) | Queries workflow `get_state` |
| `POST` | `/api/v1/projects/{id}/photos` | Upload photo (multipart) | Writes to R2 + DB |
| `POST` | `/api/v1/projects/{id}/photos/{photo_id}/validate` | Trigger validation | Signals workflow `photo_validated` |
| `POST` | `/api/v1/projects/{id}/photos/ready` | All photos uploaded | Signals workflow `photos_ready` |
| `POST` | `/api/v1/projects/{id}/scan` | Upload LiDAR scan data | Signals workflow `scan_completed` |
| `POST` | `/api/v1/projects/{id}/scan/skip` | Skip scan | Signals workflow `scan_skipped` |
| `POST` | `/api/v1/projects/{id}/intake/message` | Send intake chat message | Signals workflow `intake_message` |
| `POST` | `/api/v1/projects/{id}/intake/confirm` | Confirm design brief | Signals workflow `intake_confirmed` |
| `POST` | `/api/v1/projects/{id}/intake/skip` | Skip intake | Signals workflow `intake_skipped` |
| `GET` | `/api/v1/projects/{id}/events` | SSE stream for progress | Subscribes to Redis pub/sub |
| `POST` | `/api/v1/projects/{id}/select-option` | Select design option (0 or 1) | Signals workflow `option_selected` |
| `POST` | `/api/v1/projects/{id}/revisions` | Submit lasso or regenerate | Signals workflow `revision_submitted` |
| `POST` | `/api/v1/projects/{id}/approve` | Approve final design | Signals workflow `design_approved` |
| `GET` | `/api/v1/projects/{id}/shopping-list` | Get shopping list | Reads from DB |
| `GET` | `/api/v1/projects/{id}/images/{image_id}` | Redirect to signed R2 URL | Reads from DB |

**Pattern:** Most endpoints follow: validate request -> write data to R2/DB if needed -> signal Temporal workflow -> return 202 Accepted. The workflow then executes activities asynchronously. The client gets results via SSE or by querying state.

---

## 10. Key Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Image generation latency (30-60s) | Poor UX during waits | SSE progress via activity heartbeats; optimistic UI; generate 2 options in parallel |
| Image generation quality | Designs don't look photorealistic | Prompt engineering iteration; consider multiple model providers |
| Exa search returns poor product matches | Shopping list feels useless | Fallback to Google Shopping links; show confidence labels |
| Temporal Cloud outage | All workflows paused | Rare (99.9% SLA); workflows auto-resume; client sees "retry later" |
| Temporal workflow versioning | Deploying new workflow code breaks running workflows | Use `workflow.patched()` for non-breaking changes; use workflow versioning for breaking changes |
| No auth means no abuse attribution | Cost runaway from bad actors | Rate limiting; device fingerprint; iteration cap enforced in Temporal workflow |
| LiDAR data too large | Slow uploads, storage costs | Extract dimensions on-device; only upload structured JSON for MVP |
| R2 orphaned objects | Storage leak if purge activity fails | R2 lifecycle rules as safety net (72h TTL on all objects) |

---

## 11. Hackathon Simplification Options

If time is extremely tight, these simplifications reduce scope without breaking the core experience:

| Simplification | What Changes | Trade-off |
|----------------|-------------|-----------|
| Use `temporalite` instead of Temporal Cloud | Run Temporal locally / on Railway alongside API | More ops; no managed UI; but zero Temporal cost |
| Skip SSE, use polling against Temporal query | Client polls `GET /state` every 2s during generation | Slightly worse UX; eliminates Redis entirely |
| Use SQLite (Turso) instead of PostgreSQL | Simpler setup; less querying power | Lose JSONB, harder schema evolution |
| Skip R2 lifecycle safety net | Rely only on Temporal purge activity | Must ensure activity is reliable (Temporal retries help) |
| Single Railway service (API + worker) | One deploy, one service | Can't scale workers independently; good enough for hackathon |
| Skip photo validation activity | Validate client-side only (basic checks) | Less robust; but removes one AI API call per photo |

**Recommended minimum for hackathon:** Keep Temporal Cloud (free tier), PostgreSQL (Neon), R2, and SSE via Redis pub/sub. Run API + Temporal worker as a single Railway service. This gives durable workflow orchestration, reliable purge, and real-time progress — with minimal infrastructure to manage.

---

## 12. Architecture Diagram Summary

```
┌─────────────┐     HTTPS      ┌──────────────────┐
│  iOS Client  │◄──────────────▶│   API Server     │
│             │     SSE         │   (FastAPI)      │
│  - project  │                 │                  │
│    IDs in   │                 │  - HTTP handlers │
│    UserDefs │                 │  - R2 uploads    │
└─────────────┘                 │  - Temporal      │
                                │    signals/      │
                                │    queries       │
                                │  - SSE endpoint  │
                                └───────┬──────────┘
                                        │
                    ┌───────────────────┼───────────────────┐
                    │                   │                   │
              ┌─────▼─────┐     ┌──────▼──────┐    ┌──────▼──────┐
              │  Temporal  │     │    Neon     │    │ Cloudflare  │
              │  Cloud     │     │ PostgreSQL  │    │    R2       │
              │            │     │             │    │             │
              │ - Workflows│     │ - Photos    │    │ - Images    │
              │ - Timers   │     │ - Briefs    │    │ - Scans     │
              │ - History  │     │ - Images    │    │ - Assets    │
              └─────┬──────┘     │ - Shopping  │    └─────────────┘
                    │            │   lists     │
              ┌─────▼──────┐    └─────────────┘
              │  Temporal   │
              │  Worker     │          ┌──────────┐
              │  (Railway)  │◄────────▶│  Upstash │
              │             │  pub/sub │  Redis   │
              │ - AI gen    │          └──────────┘
              │ - Validate  │
              │ - Exa search│
              │ - Purge     │
              └─────────────┘
```
