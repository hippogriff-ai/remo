# Designer Brain + Agentic Workflow Redesign

## Context

The current intake agent works as a sequential form-filler: photos validate → LiDAR scan → intake starts from a blank slate → generation. An experienced designer doesn't work this way — they observe the room first, form a hypothesis, then use conversation to refine it. The photos sit idle during the scan phase when they could be yielding a structured room analysis.

This plan redesigns two things:
1. **The agent's "brain"** — enhance the already-good intake agent with pre-formed room understanding
2. **The workflow harness** — from rigid sequential pipeline to eager parallel execution with graceful degradation

**Important framing**: The current intake system is already sophisticated (translation engine, DIAGNOSE pipeline, 20-rule validation, room-specific guidance). This plan *enhances* it with photo pre-analysis and hypothesis injection, not replaces it.

---

## Review Synthesis

Three review perspectives were gathered. Key findings incorporated:

| Reviewer | Top Finding | Resolution |
|---|---|---|
| **Systems** | `asyncio.ensure_future` is NOT replay-safe in Temporal | Use `workflow.start_activity()` — the SDK-sanctioned pattern |
| **Systems** | Enrichment as activity is wasteful for deterministic merge | Make it a workflow method, not an activity |
| **UX** | Hypothesis needs `uncertain_aspects` for confidence calibration | Added to RoomAnalysis model |
| **UX** | Must handle hypothesis-client disagreement gracefully | Add "HYPOTHESIS CORRECTIONS" section to intake prompt |
| **UX** | No-LiDAR users face 60s spinner — terrible UX | Start intake immediately, inject analysis mid-conversation if needed |
| **Skeptic** | New DesignBrief fields not yet parsed by `_build_generation_prompt` | Existing brief fields ARE consumed; 4 new fields need follow-up PR to wire into generation prompt |
| **Skeptic** | Current prompt already has significant designer intelligence | Frame as enhancement, preserve existing systems intact |

---

## Part 1: Eager Photo Analysis (Workflow Layer)

### Current flow (wasted time)
```
photos validated → [idle wait] → scan completes → intake starts from zero → generation
```

### New flow (eager execution)
```
photos validated → read_the_room fires immediately ──┐
                 → scan proceeds in parallel           │
                                                       ▼
                   scan completes → merge LiDAR ──→ RoomContext
                                                       │
                   intake starts ←── hypothesis ───────┘
```

### Workflow changes (`backend/app/workflows/design_project.py`)

**New instance fields:**
```python
self.room_analysis: RoomAnalysis | None = None
self.room_context: RoomContext | None = None
self._analysis_handle: ActivityHandle | None = None  # replay-safe handle
```

**Modified `_run_phases`:**
```python
async def _run_phases(self) -> None:
    # --- Photos (need >= 2 room photos) ---
    await self._wait(lambda: sum(1 for p in self.photos if p.photo_type == "room") >= 2)

    # NEW: Fire off read_the_room immediately (non-blocking)
    self._start_eager_analysis()

    # --- Scan (runs in parallel with analysis) ---
    self.step = "scan"
    await self._wait(lambda: self.scan_data is not None or self.scan_skipped)

    # NEW: Merge LiDAR into analysis if both available (inline, not activity)
    self._enrich_context()

    # --- Intake (with start-over loop) ---
    while True:
        self.step = "intake"
        self._restart_requested = False

        # NEW: Collect pending analysis with short timeout
        await self._resolve_analysis()

        await self._wait(lambda: self.design_brief is not None or self.intake_skipped)
        # ... rest unchanged ...
```

**Key pattern: `workflow.start_activity()` (replay-safe fire-and-collect-later)**

```python
def _start_eager_analysis(self) -> None:
    """Schedule read_the_room activity without awaiting. Replay-safe."""
    if self._analysis_handle is not None:
        return
    self._analysis_handle = workflow.start_activity(
        analyze_room_photos,
        self._analysis_input(),
        start_to_close_timeout=timedelta(seconds=90),  # Opus 4.6: typically 15-45s
        retry_policy=RetryPolicy(maximum_attempts=2),
    )
```

`workflow.start_activity()` is the Temporal SDK's canonical way to schedule an activity without blocking. It returns an `ActivityHandle` that:
- Records the scheduling in event history (replay-safe)
- Is awaitable at any later point
- Supports cancellation via `handle.cancel()`
- On replay, re-calling `start_activity` matches the recorded event

**Graceful degradation in `_resolve_analysis`:**
```python
async def _resolve_analysis(self) -> None:
    """Collect analysis result if available. Never blocks intake."""
    if self._analysis_handle is None:
        return
    try:
        # Short timeout — analysis should be done by now (ran during scan)
        result = await asyncio.wait_for(self._analysis_handle, timeout=30)
        self.room_analysis = result.analysis
        self._build_room_context()
    except asyncio.TimeoutError:
        workflow.logger.warning("read_the_room still running, intake starts without it")
    except Exception as exc:
        workflow.logger.warning("read_the_room failed (non-fatal): %s", exc)
```

- If analysis completes: collect `RoomAnalysis`, build `RoomContext`
- If still running after 30s: proceed without (user already waited 30-120s during scan)
- If failed: log warning, proceed without (equivalent to current behavior — no regression)
- **Analysis errors never set `self.error`** — never show error UI to user
- In practice: Opus 4.6 takes 15-45s; user spends 30-120s on LiDAR scan → analysis almost always completes before intake

**Latency for skip-scan users:** If user skips LiDAR (no buffer time), the 30s timeout in `_resolve_analysis` applies. If analysis isn't ready, intake starts immediately with photos-on-turn-1 (current behavior). No spinner, no blocking. Analysis result is wasted for this project but that's acceptable degradation.

**Context enrichment (workflow method, NOT activity):**
```python
def _enrich_context(self) -> None:
    """Deterministic merge of photo analysis + LiDAR. No I/O, no AI call."""
    if self.room_analysis and self.scan_data and self.scan_data.room_dimensions:
        dims = self.scan_data.room_dimensions
        # Replace visual estimate with precise measurements
        self.room_analysis.estimated_dimensions = (
            f"{dims.width_m:.1f}m x {dims.length_m:.1f}m (ceiling {dims.height_m:.1f}m)"
        )
        self.room_context = RoomContext(
            photo_analysis=self.room_analysis,
            room_dimensions=dims,
            enrichment_sources=["photos", "lidar"],
        )
    elif self.room_analysis:
        self.room_context = RoomContext(
            photo_analysis=self.room_analysis,
            enrichment_sources=["photos"],
        )
```

This is a pure function on workflow state — no need for activity overhead, serialization, or retry policy.

**`start_over` behavior:**
```python
async def start_over(self) -> None:
    # ... existing clearing code ...
    # Cancel in-flight analysis and allow re-analysis
    if self._analysis_handle is not None:
        self._analysis_handle.cancel()
    self._analysis_handle = None
    self.room_analysis = None
    self.room_context = None
```

**Extension point (scalable pattern for future):**
```python
# Dict of handles instead of per-task fields — scales to N analyses
self._eager_handles: dict[str, ActivityHandle] = {}

def _start_eager_analysis(self) -> None:
    if "room_analysis" not in self._eager_handles:
        self._eager_handles["room_analysis"] = workflow.start_activity(...)
    # Future: self._eager_handles["style_match"] = workflow.start_activity(...)

async def _resolve_analysis(self) -> None:
    for name, handle in list(self._eager_handles.items()):
        try:
            result = await asyncio.wait_for(handle, timeout=30)
            self._collect_result(name, result)
        except (asyncio.TimeoutError, Exception):
            workflow.logger.warning("%s not ready, proceeding without", name)
```

### WorkflowState update (additive)
```python
class WorkflowState(BaseModel):
    # ... existing 13 fields ...
    room_analysis: RoomAnalysis | None = None   # NEW
    room_context: RoomContext | None = None      # NEW
```

iOS polling automatically gets new fields. Can show "Understanding your room..." when `step == "scan"` and `room_analysis is None`.

---

## Part 2: RoomAnalysis Data Model

### New contracts (`backend/app/models/contracts.py`)

```python
class LightingAssessment(BaseModel):
    natural_light_direction: str | None = None   # "south-facing windows"
    natural_light_intensity: str | None = None   # "abundant" / "moderate" / "limited"
    window_coverage: str | None = None           # "full wall" / "single window"
    existing_artificial: str | None = None       # "single overhead" / "layered"
    lighting_gaps: list[str] = []                # ["dark reading corner", "no task lighting"]

class FurnitureObservation(BaseModel):
    item: str                                    # "L-shaped gray sectional"
    condition: str | None = None                 # "good" / "worn" / "dated"
    placement_note: str | None = None            # "faces wall instead of window"
    keep_candidate: bool = False                 # designer thinks worth keeping

class BehavioralSignal(BaseModel):
    observation: str                             # "books stacked on floor near armchair"
    inference: str                               # "active reader lacking storage"
    design_implication: str | None = None        # "add reading nook with task lighting"

class RoomAnalysis(BaseModel):
    """Pre-intake photo analysis — the designer's first 5 minutes of observation."""
    # Identity & space
    room_type: str | None = None
    room_type_confidence: float = Field(ge=0, le=1, default=0.5)
    estimated_dimensions: str | None = None       # "approximately 12x15 feet"
    layout_pattern: str | None = None             # "open plan" / "L-shaped"

    # Observations
    lighting: LightingAssessment | None = None
    furniture: list[FurnitureObservation] = []
    architectural_features: list[str] = []        # ["crown molding", "bay window"]
    flooring: str | None = None                   # "hardwood, good condition"
    existing_palette: list[str] = []              # ["cool gray walls", "warm oak floors"]
    overall_warmth: str | None = None             # "cool" / "warm" / "neutral" / "mixed"
    circulation_issues: list[str] = []            # ["path to window blocked by ottoman"]

    # Inferences
    style_signals: list[str] = []                 # ["mid-century legs", "warm neutral palette"]
    behavioral_signals: list[BehavioralSignal] = []
    tensions: list[str] = []                      # ["beautiful moldings with flat-pack furniture"]

    # Synthesis
    hypothesis: str | None = None                 # "lived-in family room, good bones, poor lighting"
    strengths: list[str] = []
    opportunities: list[str] = []
    uncertain_aspects: list[str] = []             # ["lighting warmer than photos suggest", "can't assess room depth"]

    # Meta
    photo_count: int = 0

class RoomContext(BaseModel):
    """Progressive room understanding that enriches over time."""
    photo_analysis: RoomAnalysis | None = None
    room_dimensions: RoomDimensions | None = None
    enrichment_sources: list[str] = []            # ["photos", "lidar"]
```

### Activity contracts
```python
class AnalyzeRoomPhotosInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []

class AnalyzeRoomPhotosOutput(BaseModel):
    analysis: RoomAnalysis
```

No `EnrichRoomContextInput/Output` — enrichment is a workflow method, not an activity.

### Additive DesignBrief fields
```python
class DesignBrief(BaseModel):
    # ... existing fields unchanged ...
    # NEW optional fields (Designer Brain signals):
    emotional_drivers: list[str] = []             # "started WFH, room feels oppressive"
    usage_patterns: str | None = None             # "couple WFH Mon-Fri, host dinners monthly"
    renovation_willingness: str | None = None     # "repaint yes, replace flooring no"
    room_analysis_hypothesis: str | None = None   # preserved from photo analysis
```

**Note on downstream consumers**: `DesignBrief` is already the primary input to generation — `_build_generation_prompt` in `generate.py` consumes `room_type`, `occupants`, `style_profile` (mood/colors/textures/lighting/clutter_level), `pain_points`, `constraints`, `keep_items`, and `inspiration_notes`. The 4 new fields above (`emotional_drivers`, `usage_patterns`, `renovation_willingness`, `room_analysis_hypothesis`) are **not yet parsed** by `_build_generation_prompt`. They still improve the DesignBrief narrative quality the user confirms before generation, and the full object is passed as context. **Follow-up PR**: Wire the new fields into `_build_generation_prompt` for more contextual generation (e.g., "user WFH daily" → prioritize desk lighting and ergonomics in the image).

---

## Part 3: Designer Brain (Agent Layer)

### What's already good (preserve intact)

The current intake system has significant design intelligence that this plan **enhances, not replaces**:

- **Translation Engine** (intake_system.txt): Maps "cozy" → warm palette, layered textiles, 2200-2700K, refuge layout. Excellent.
- **DIAGNOSE Pipeline**: 8-step reasoning process per response. Real design methodology.
- **Diagnostic Question Bank**: Professional probing questions. Keep all of them.
- **Room-Specific Guidance**: Evidence-based per-room rules with measurements. Critical.
- **20-Rule Validation**: Completeness checklist before brief generation. Essential safety net.
- **Domain Notepad**: 11-domain tracking. Remains as completeness safety net.

### What the `read_the_room` skill adds

The hypothesis-driven approach enhances these systems by giving the agent **a starting point**:
- Pre-populated domains (room_type, furniture, lighting) → fewer turns wasted on obvious questions
- Observation-led opener → builds trust, demonstrates expertise
- `uncertain_aspects` → targeted first questions instead of generic domain walk
- Behavioral signals → lifestyle probes grounded in evidence

### The `read_the_room` skill (`backend/app/activities/analyze_room.py`)

- **Model**: Claude Opus 4.6 — deep spatial reasoning about room features and potential problems
- **Input**: Room photos as image blocks + inspiration photos for context
- **Output**: Structured `RoomAnalysis` via tool call
- **Prompt** (`backend/prompts/read_the_room.txt`): 7-step observational protocol:
  1. **Read the light** — direction, intensity, time-of-day clues, color temperature
  2. **Read the furniture** — condition/wear reveals where life happens; arrangement reveals actual vs intended use
  3. **Read the architecture** — features the space honors or fights against
  4. **Read the behavior** — toys, pet beds, work setups, book stacks — the room is a diary
  5. **Read the tensions** — where the space is at war with itself
  6. **Form the hypothesis** — synthesize into 2-3 sentence assessment
  7. **Flag uncertainties** — what can't be determined from photos alone (populate `uncertain_aspects`)

### Intake prompt changes (`backend/prompts/intake_system.txt`)

**Add "ROOM ANALYSIS" injection point** (when `room_analysis` is available):
- Pre-populate room_type, furniture, lighting observations
- Inject hypothesis and uncertain_aspects
- Instruction: "You already know the room type and basic layout. Do NOT re-ask these. Start by confirming your understanding and probing the highest-uncertainty aspects."

**Add "HYPOTHESIS CORRECTIONS" section** (new):
```
When the user contradicts your room analysis:
- Acknowledge warmly: "Good to know — photos can be misleading about [aspect]"
- Update hypothesis immediately — don't carry forward invalidated assumptions
- Use correction as learning signal: if they corrected lighting, they care deeply about it — probe deeper
- NEVER say "but the photos show..." or imply the user is wrong about their own space
```

**Mode adjustments with analysis:**
- **Quick (3-4 turns)**: Pre-fills ~4 domains. Agent confirms hypothesis, probes 1-2 gaps, drafts brief.
- **Full (10-11 turns)**: Pre-fills basics. Deep-dives into emotional drivers, usage patterns, renovation willingness. Domain notepad remains as safety net.
- **Open (15-16 turns)**: Opens with observation. Analysis provides anchors when energy slows.

### New tool schema fields

The `interview_client` and `draft_design_brief` tools gain:

| Field | What it captures | Example |
|---|---|---|
| `emotional_drivers` | Why this project now | "started WFH, room feels oppressive" |
| `usage_patterns` | Detailed who/when/what | "couple WFH Mon-Fri, host dinners monthly" |
| `renovation_willingness` | Scope signals | "repaint yes, fixtures maybe, tile no" |
| `hypothesis_updates` | Mental model changes | "confirmed warm preference, surprised by aversion to texture" |

### Choice-signal interpretation (P3 — deferred)

- **P2 minimal**: Store a note at selection: `"Selected '{a.caption}' over '{b.caption}'"` — pass to edit activity as context
- **P3 full**: `interpret_choice` activity with dimensional preference extraction

---

## Part 4: Error Handling & Durability

### Failure matrix

| Failure | Impact | Recovery |
|---|---|---|
| `read_the_room` retries exhausted | No hypothesis | Intake starts from blank slate (current behavior) |
| Analysis still running at intake start | 30s wait | If not ready, intake starts immediately — no spinner |
| LiDAR parse fails | No dimensions | Photo-only analysis preserved |
| Both analysis AND LiDAR fail | Bare minimum | Current behavior — no regression |

### Retry semantics
- **`read_the_room` (Opus 4.6)**: `RetryPolicy(maximum_attempts=2)`, 90s timeout
- **Analysis errors are silent**: Logged as `warning`, never set `self.error`, never show error UI
- **Only blocking activities** (generation, edit, shopping) set user-visible error state
- **Handle cancellation** on `start_over`: prevents wasting Opus API cost on stale analysis

### Extension pattern
```python
# Dict of handles — scales to N analyses without per-task boilerplate
self._eager_handles["room_analysis"] = workflow.start_activity(...)
self._eager_handles["style_match"] = workflow.start_activity(...)  # future

# Collect all with workflow.wait
for name, handle in self._eager_handles.items():
    try:
        result = await asyncio.wait_for(handle, timeout=30)
        self._collect_result(name, result)
    except (asyncio.TimeoutError, Exception):
        pass  # graceful degradation per-analysis
```

---

## Part 5: Implementation Sequence

| PR | Scope | Owner | Est. Lines |
|---|---|---|---|
| **PR-1** | New contracts: `RoomAnalysis`, `RoomContext`, `LightingAssessment`, `FurnitureObservation`, `BehavioralSignal`, activity I/O, additive `WorkflowState` + `DesignBrief` fields + tests | T0 | ~200 |
| **PR-2** | Mock stub for `analyze_room_photos` + worker registration | T0 | ~60 |
| **PR-3** | Workflow changes: `workflow.start_activity()` eager launch, handle collection, inline enrichment, `start_over` cancellation + workflow tests | T0 | ~180 |
| **PR-4** | API changes: inject `room_analysis`/`room_context` into intake `project_context` dict | T0 | ~40 |
| **PR-5** | Real `analyze_room_photos` activity (`read_the_room` skill) + `prompts/read_the_room.txt` + tests. Uses Opus 4.6. | T3 | ~250 |
| **PR-6** | Intake prompt enhancement: hypothesis injection, HYPOTHESIS CORRECTIONS section, new tool schema fields, `build_brief` updates + tests | T3 | ~200 |
| **PR-7** | iOS `WorkflowState` model update + "Understanding your room..." UI | T1 | ~60 |

PR-1 and PR-2 first. Then PR-3 (workflow), PR-5 (real activity), PR-7 (iOS) in parallel. PR-4 and PR-6 are final integration.

**Follow-up (post-merge):** Update `generate.py:_build_generation_prompt` to leverage new DesignBrief fields.

---

## Critical Files to Modify

- `backend/app/models/contracts.py` — New models + additive fields (T0)
- `backend/app/workflows/design_project.py` — `workflow.start_activity()` eager analysis orchestration (T0)
- `backend/app/worker.py` — Register new activity (T0)
- `backend/app/activities/mock_stubs.py` — Mock stub (T0)
- `backend/app/activities/analyze_room.py` — **New file**: `read_the_room` activity (T3)
- `backend/prompts/read_the_room.txt` — **New file**: 7-step observational protocol prompt (T3)
- `backend/app/activities/intake.py` — `load_system_prompt`, `build_brief`, tool schemas (T3)
- `backend/prompts/intake_system.txt` — Room analysis injection + hypothesis corrections (T3)
- `backend/app/api/routes/projects.py` — Inject room context into intake (T0)
- `backend/tests/test_contracts.py` — Contract tests for new models (T0)
- `backend/tests/test_workflow.py` — Workflow tests for eager analysis (T0)

---

## Appendix: Gemini Model & Image Budget

### Model Choice
- **Model**: `gemini-3-pro-image-preview` (Gemini 3 Pro Image Preview)
- **Max input images**: 14 per request (6 objects + 5 humans + extras)
- **Daily quota**: **250 requests/day** — this is a hard constraint during development
- **Current constants** (`gemini_chat.py`): `MAX_INPUT_IMAGES = 14`, `MAX_ROOM_PHOTOS = 2`, `MAX_INSPIRATION_PHOTOS = 3`
- **Current generation**: 2 room + up to 3 inspiration = 5 images per call. Well within 14-image ceiling.
- **Config update needed**: `config.py` currently has `gemini_model = "gemini-2.5-flash-image"` — update to `gemini-3-pro-image-preview`

### Quota Management (250 requests/day)

**Budget per full user flow:**
- Generation: 2 calls (2 parallel options)
- Edit rounds: up to 5 calls (1 per edit, plus potential retries on text-only responses)
- Worst case per project: ~12 calls (2 gen + 5 edits + 5 retries)

**Development strategy to conserve quota:**
1. **Mock-first development**: All workflow, contract, and API changes use mock stubs (`mock_stubs.py`). No Gemini calls needed for PR-1 through PR-4.
2. **Unit tests never hit Gemini**: Test prompt construction, image budget math, history pruning, and serialization with synthetic data. Zero API calls.
3. **Batch real-AI testing**: Dedicate specific test sessions (not ad-hoc). Run the full E2E suite once per day, not per-commit.
4. **Shared test fixtures**: Cache Gemini responses in `tests/fixtures/` for regression testing. First run records; subsequent runs replay.
5. **Separate dev vs CI quota**: If CI needs real-AI tests, use a separate API key/project to avoid burning dev quota.
6. **Retry budget awareness**: Generation retries (text-only fallback) and edit retries each consume a request. The `RetryPolicy(maximum_attempts=2)` means a single failed activity can burn 2 requests.
7. **Daily quota tracking**: Add a simple counter/log to track how many Gemini requests have been made today. Log a warning at 200/250 (80%) to prevent surprise exhaustion mid-session.

### Image Budget: No Changes Needed
The current architecture works within Gemini 3 Pro's 14-image ceiling:
- **Generation**: 5 input images max (2 room + 3 inspiration) → 1 output. Run twice in parallel for 2 design options.
- **Edit bootstrap**: ~7 images (2 room + 3 inspiration + 1 base + 1 annotated). Within budget.
- **Edit continuation**: History pruning (`_prune_history_images`) keeps first 2 turns + last 2 turns intact, strips images from middle turns. Handles 5-edit-round accumulation (~16 images) by pruning back to ≤14.

### History Pruning (already implemented, no changes)
- **Images**: First 2 turns preserved (room/inspiration context), last 2 turns preserved (latest exchange), middle turns stripped to text-only
- **Text**: ALL text preserved across ALL turns — edit instructions, feedback, model explanations survive. Only `inline_data` (pixel bytes) removed from intermediate turns.
- **Generation vs edit**: Separate pipelines. Generation has no history. Edit chat bootstraps with reference images + selected design, then continues via R2-serialized history.

---

## Verification

1. **Contract tests**: All new models serialize/deserialize, additive fields have defaults, backward-compatible
2. **Workflow tests**: Eager analysis fires after 2+ photos, analysis failure doesn't block intake, LiDAR enrichment merges correctly, `start_over` cancels handle and resets
3. **Replay safety test**: Workflow replays correctly with analysis activity in history
4. **Intake unit tests**: System prompt loads with/without room analysis, hypothesis corrections section present, new tool schema fields work, `build_brief` handles new fields
5. **Latency test**: Verify analysis completes within scan time (15-45s Opus vs 30-120s scan)
6. **Skip-scan test**: Verify intake starts immediately when scan is skipped and analysis isn't ready
7. **E2E test**: Full flow with real Opus — verify hypothesis quality, intake conversation references it, brief contains new fields
8. **Mock mode**: Full flow works with mock stubs (no API keys needed)
9. **Image budget test**: Verify generation stays within 14-image ceiling (5 images per call), edit history pruning keeps total ≤14 through 5 rounds
10. **Config test**: Verify `gemini_model` is set to Gemini 3 Pro model ID
