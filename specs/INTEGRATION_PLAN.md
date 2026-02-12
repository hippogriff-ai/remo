# Integration Plan (P2)

> Last updated: 2026-02-12
> Owner: T0 (Platform)
> Status: Phase A (active)

---

## P1 Completion Tally

| Team | P1 Done | Tests | Key Deliverables |
|------|---------|-------|-----------------|
| T0 | 10/10 | 301 | Contracts, API (17 endpoints), workflow (12 signals), DB, R2, photo validation, LiDAR parser, CI |
| T1 | All screens | 99 | 8 SPM packages, all screens, Maestro E2E, mock client, polling |
| T2 | 7/7 | 130+ | `generate_designs`, `edit_design`, Gemini chat manager, annotation drawing, prompt templates |
| T3 | 14/14 | 389 | `run_intake_chat` (3 modes, skill-based refactor), `generate_shopping_list` (5-step pipeline), eval harness |

> **Post-merge status**: T3 merged to `main` (PR #5). Combined test count: **690 passed**, 57 skipped (integration tests), 0 warnings from app code. T3 intake agent is **integration-ready** — INT-2 is unblocked.

---

## P2 Integration Items

### INT-1: Wire real activities into workflow

**Status: DONE** — Workflow imports swapped from mock_stubs to real modules (generate.py, edit.py, shopping.py). Worker's config-driven activity loading (`use_mock_activities` flag) provides mock/real toggle. Per-activity named retry policies in workflow. 718 tests passing.

Replace `mock_stubs.py` imports in `design_project.py` with real activity modules.

**Changes**:
- `design_project.py`: import `generate_designs` from `app.activities.generate`, `edit_design` from `app.activities.edit`, `generate_shopping_list` from `app.activities.shopping`
- Remove or deprecate `mock_stubs.py`

**Schema**: No contract changes. Activity function signatures already match `GenerateDesignsInput/Output`, `EditDesignInput/Output`, `GenerateShoppingListInput/Output`.

**TDD criteria**:
- Workflow test: signal `complete_intake` with brief -> calls `generate_designs` -> state has 2 `generated_options`, step transitions to `selection`
- Workflow test: signal `submit_annotation_edit` -> calls `edit_design` -> `current_image` updated, `revision_history` appended
- Workflow test: signal `approve_design` -> calls `generate_shopping_list` -> `shopping_list` populated, step transitions to `completed`
- Workflow test: activity raises retryable error -> workflow retries (Temporal retry policy)
- Workflow test: activity raises non-retryable error -> `WorkflowState.error` populated, step unchanged

### INT-2: Replace mock API conversation with T3 intake agent

**Status: DONE** — Refactored intake endpoints to dispatch between mock and real agent. `_IntakeSession` dataclass tracks mode, conversation history, and partial brief per project. `send_intake_message` calls `_run_intake_core` when `use_mock_activities=False`. 8 new tests covering input construction, history accumulation, partial brief tracking, error handling, and session cleanup. 715 tests passing.

The intake endpoints (`start_intake`, `send_intake_message`, `confirm_intake`) in `projects.py` use hardcoded canned responses. Replace with calls to T3's `run_intake_chat`.

**Architecture**: API-mediated. The API layer is a dumb pass-through — it assembles `project_context`, calls `run_intake_chat`, and returns the result. The agent (Claude, running inside the activity) is autonomous: it decides what to ask, when to follow up, and when to summarize. The backend's only control is `MAX_TURN` per mode (quick ~4, full ~10, open ~15) which acts as a hard cap. The agent may summarize earlier if it covered all domains. The workflow only cares about the final `DesignBrief` via the `complete_intake` signal.

**Turn flow**:
```
iOS -> POST /intake/start (mode="quick")
  -> API assembles project_context from workflow state (photos, notes)
  -> calls run_intake_chat(turn=1, mode="quick", max_turns=4, project_context)
  -> agent autonomously picks first question
  -> returns IntakeChatOutput to iOS

iOS -> POST /intake/message ("Living room")
  -> API calls run_intake_chat(turn=2, message, previous_brief from prior response, ...)
  -> agent decides next question or summarizes early if domains covered
  -> returns IntakeChatOutput

... agent autonomously decides when is_summary=True ...
... backend forces is_summary=True if turn == max_turns ...

iOS -> POST /intake/confirm (brief)
  -> API signals complete_intake to workflow with DesignBrief
```

**`project_context` shape** (implicit dict contract, document and enforce):
```python
{
    "room_photos": list[str],        # R2 pre-signed URLs
    "inspiration_photos": list[str], # R2 pre-signed URLs
    "inspiration_notes": list[dict], # [{"photo_index": int, "note": str}]
    "previous_brief": dict | None    # Serialized partial DesignBrief from prior turn
}
```

**Changes**:
- `projects.py`: `start_intake` calls `run_intake_chat` with project photos + mode + max_turns
- `projects.py`: `send_intake_message` calls `run_intake_chat` with user message + `previous_brief` from prior response
- API is stateless per-turn — `previous_brief` round-trips through the iOS client (iOS receives `partial_brief` in each response, sends it back with the next message)

**TDD criteria**:
- `start_intake` returns `IntakeChatOutput` with `agent_message`, `options`, `progress` populated
- `send_intake_message` x3 -> agent autonomously reaches summary with `is_summary=True` and valid `partial_brief`
- `partial_brief` accumulates across turns (turn 3 brief contains info from turn 1 answers)
- Agent reaches summary before `max_turns` when domains are covered early
- At `max_turns`, backend forces summary even if agent hasn't covered all domains
- Unknown project_id -> 404
- Wrong step -> 409

### INT-3: Add `DELETE /projects/{id}/photos/{photoId}` endpoint

**Status: DONE** — Added DELETE endpoint with 204/404/409 responses, remove_photo workflow signal, 10 tests (6 API + 3 workflow + 1 not-found).

iOS client already calls this with optimistic UI rollback. Backend needs the actual endpoint.

**Schema**: Returns 204 on success. `ErrorResponse` on 404 (project or photo not found) or 409 (step past photos).

**Changes**:
- `projects.py`: new `DELETE` route
- `design_project.py`: new `remove_photo` signal (or handle via existing state mutation)

**TDD criteria**:
- Delete existing photo -> 204, photo removed from `WorkflowState.photos`
- Delete nonexistent photo -> 404 with `error: "photo_not_found"`
- Delete nonexistent project -> 404 with `error: "workflow_not_found"`
- Delete after step transition past `photos` -> 409 with `error: "wrong_step"`

### INT-4: Health endpoint real connectivity checks

**Status: DONE** — Real connectivity probes for PostgreSQL (asyncpg SELECT 1), Temporal (Client.connect + check_health), and R2 (head_bucket via executor). All checks run in parallel with 3s timeout, fail gracefully to "disconnected". 3 new tests + existing test updated. 718 tests passing.

Currently returns hardcoded `"not_connected"` for all services.

**Changes**: `health.py` — ping PostgreSQL, Temporal, R2 with timeouts.

**TDD criteria**:
- All services up -> 200 with all statuses `"connected"`
- DB down -> 200 with postgres `"disconnected"`, others `"connected"`
- Response includes `version` and `environment` fields

### INT-5: Provision production environment variables

Required in Railway for real activity execution:
- `EXA_API_KEY` (T3 shopping)
- `ANTHROPIC_API_KEY` (T3 intake + shopping scoring)
- `GOOGLE_API_KEY` (T2 Gemini)
- `R2_*` credentials (already provisioned for photo upload)

**TDD criteria**: Health check returns green for all services after deployment.

### INT-6: Add `lifestyle` field to `DesignBrief` contract

**Status: DONE** — Added `lifestyle: str | None = None` to DesignBrief, 4 tests (backwards compat + serialization). T3 can now populate directly.

T3's intake agent now extracts a `lifestyle` field describing how the user uses the space (activities, routines, hosting patterns). Currently T3 merges this into the `occupants` field as a workaround (`intake.py:484-490`). T0 needs to add a dedicated field so the data flows cleanly to T2 generation and T3 shopping.

**Schema change** (T0 owns `contracts.py`):
```python
class DesignBrief(BaseModel):
    # ... existing fields ...
    lifestyle: str | None = None  # How user uses the space: activities, routines, hosting
```

**Changes**:
- `contracts.py`: Add `lifestyle: str | None = None` to `DesignBrief` (additive, non-breaking)
- T3 `intake.py`: Remove the `occupants` merge workaround (lines 484-490), populate `lifestyle` directly
- T1 Swift models: Add `lifestyle: String?` to `DesignBrief` mirror type
- T2 `generate.py`: Optionally incorporate `lifestyle` into generation prompt (e.g., "Space used for morning yoga and evening hosting")

**TDD criteria**:
- `DesignBrief(lifestyle="Morning yoga, weekend hosting")` serializes and deserializes correctly
- `DesignBrief(lifestyle=None)` is valid (backwards compatible)
- T3 intake agent populates `lifestyle` separately from `occupants` (no merge hack)
- Existing tests pass without modification (field is optional with default `None`)

### INT-7: T3 skill-based intake refactor compatibility

T3 refactored the intake agent from a single "god prompt" with two always-called tools to a skill-based architecture with two mutually exclusive skills (`interview_client` and `draft_design_brief`). The agent picks ONE skill per turn based on domain coverage assessment. This is internal to the activity — the `IntakeChatInput`/`IntakeChatOutput` contract is unchanged.

**No code changes needed** — this is informational. The refactor is entirely contained within `intake.py`. The API and workflow see the same input/output shapes.

**Verification**:
- `IntakeChatOutput` shape is unchanged (message, partial_brief, is_summary, options, progress)
- INT-2 wiring works identically — API calls `run_intake_chat`, gets `IntakeChatOutput`
- Eval tests pass against the new skill-based architecture

---

## Feature Gaps

### GAP-1: Room dimensions not used in generation

`GenerateDesignsInput.room_dimensions` exists in the contract but `generate.py` ignores it. Spec says generated designs should respect room proportions when LiDAR data is available.

**Schema**: No changes — field already exists on `GenerateDesignsInput` as `room_dimensions: RoomDimensions | None`.

**Changes**: `generate.py` — incorporate dimensions into the generation prompt when present.

**TDD criteria**:
- Generate with `room_dimensions` provided -> prompt includes "Room is 4.2m x 5.8m x 2.7m" (or equivalent)
- Generate without `room_dimensions` -> prompt omits dimension text
- Generated image does not change contract shape

### GAP-2: Design option captions are placeholders

`generate.py` returns hardcoded "Design Option A" / "Design Option B". Spec expects AI-generated descriptions like "Warm minimalist — linen sofa, walnut coffee table."

**Schema**: No changes — `DesignOption.caption: str` already exists.

**Changes**: `generate.py` — extract caption from Gemini's text response alongside the generated image.

**TDD criteria**:
- Generated captions are non-empty, differ between the two options, and contain at least 3 words
- Caption describes design elements (not "Design Option A")
- If Gemini returns no text, fallback to generic caption (no crash)

### GAP-3: Dimension filtering in shopping is a stub

`filter_by_dimensions()` in `shopping.py` is a pass-through. Products are not filtered by whether they physically fit the room.

**Schema**: `GenerateShoppingListInput.room_dimensions: RoomDimensions | None` already exists. `ProductMatch.fit_status: str | None` and `ProductMatch.dimensions: str | None` already exist.

**Changes**: `shopping.py` — implement dimension comparison when both room dimensions and product dimensions are available.

**TDD criteria**:
- Room 4m wide + product 3m wide -> `fit_status="fits"`
- Room 4m wide + product 3.8m wide -> `fit_status="tight"` with `fit_detail` explaining clearance
- Room 4m wide + product 5m wide -> product filtered out, added to `unmatched` with search URL
- No room dimensions -> all products pass through (no filtering)
- Product has no dimensions string -> skip dimension check, keep product

### GAP-4: Separate style fit from dimension fit

Currently `fit_status` is derived from the confidence score (>= 0.8 -> "fits", 0.5-0.79 -> "tight"). The product spec envisions a LiDAR-derived physical fit indicator. These are two different concerns.

**Approach**: Keep it simple now, leave space to add `dimension_fit` later.

- **Now**: Rename current `fit_status` semantics to mean **style/confidence fit**. T1 already maps `confidence_score` to badge colors (green >= 0.8, orange 0.5-0.79). No code changes needed — just document that `fit_status` currently reflects style match quality.
- **Later (when GAP-3 lands)**: Add `dimension_fit: str | None = None` as a new optional field on `ProductMatch`. Values: `"fits"` / `"tight"` / `None`. T1 shows a second badge when present. This is an additive contract change (new optional field) — no breaking change.

**Schema**: No changes now. Future additive field `dimension_fit: str | None = None` on `ProductMatch`.

**TDD criteria (current)**:
- `confidence_score >= 0.8` -> T1 shows green "Match" badge
- `confidence_score 0.5-0.79` -> T1 shows orange "Close match" badge
- `confidence_score < 0.5` -> product not shown (unmatched)

**TDD criteria (future, when dimension_fit is added)**:
- Room 4m wide + product 3m wide -> `dimension_fit="fits"`
- Room 4m wide + product 3.8m wide -> `dimension_fit="tight"`
- No LiDAR data -> `dimension_fit=None` (badge not shown)
- High confidence + doesn't physically fit -> green "Match" badge + yellow "Tight fit" badge

### GAP-5: Mock API skips generation step

**Status: DONE** — Added simulated generation step with configurable delay. Intake→generation→selection transition now mirrors real workflow. 5 tests, clean cleanup in start-over and delete.

`confirm_intake` and `skip_intake` jump directly to `selection`, skipping the `generation` step. iOS cannot test the `GeneratingScreen` polling flow against the mock.

**Changes**: `projects.py` mock routes — transition to `generation` step first, then after a simulated delay (or on next `getState` poll), transition to `selection` with `generated_options`.

**TDD criteria**:
- After `confirm_intake`, `state.step == "generation"` and `generated_options` is empty
- After polling delay, `state.step == "selection"` with 2 `generated_options` populated
- `skip_intake` follows same two-step transition

---

## Improvement Items

Items discovered during integration that improve quality but don't change contracts or architecture.

### IMP-1: Intake agent silver eval dataset + prompt tuning (T3)

The intake agent needs a curated evaluation dataset to measure and tune prompt quality. Currently only golden tests exist (happy-path integration checks). A silver dataset tests edge cases and quality.

**What's needed**:
1. Curate silver dataset: good and bad intake conversation examples (diverse user styles, vague answers, contradictions, rich first answers)
2. Calibrate `intake_eval.py` scoring prompt against human judgment (do our scores match what a human would rate?)
3. Curate input scenarios for all 3 modes (quick, full, open) with realistic user responses
4. Run intake agent through scenarios, pipe output to eval, measure scores
5. Tune `intake_system.txt` prompt until eval scores reach **87+** across the dataset

**TDD criteria**:
- Silver dataset has 10+ scenarios covering: vague users, rich users, contradictory users, domain-skipping users
- Eval scores calibrated: human-rated "good" briefs score 80+, "bad" briefs score < 60
- All 3 modes reach mean eval score of 87+ across the silver dataset

### IMP-3: Product spec test case coverage (T0)

**Status: DONE** — Added PHOTO-10 (max 3 inspiration photos) and REGEN-2 (10-char minimum text feedback) enforcement at the API level. 4 new tests, 724 total passing.

Audit of `specs/PRODUCT_SPEC.md` test cases against backend tests found two missing backend validations:
- **PHOTO-10**: 4th inspiration photo should be blocked. Added `MAX_INSPIRATION_PHOTOS = 3` with 422 response.
- **REGEN-2**: Text feedback < 10 chars should be rejected. Added API-level 10-char check (contract has `min_length=1`; the stricter validation is defense-in-depth).

**TDD criteria**:
- Upload 4th inspiration photo → 422 `too_many_inspiration_photos`
- Room photo upload unaffected by inspiration limit
- Text feedback "darker" (7 chars) → 422 `feedback_too_short`
- Text feedback exactly 10 chars → 200 accepted

### IMP-4: Photo notes + skip-intake guard (T0)

**Status: DONE** — Added PHOTO-7 inspiration photo note support and INTAKE-3a skip-intake guard. 7 new tests, 731 total passing.

Two product spec gaps closed:
- **PHOTO-7**: `PhotoData.note` existed in the contract but couldn't be set via the upload API. Added `note` query parameter to `upload_photo`. Validates: notes only on inspiration photos (422 `note_not_allowed`), max 200 chars (422 `note_too_long`).
- **INTAKE-3a**: `skip_intake` allowed skipping without inspiration photos. Product spec says intake is mandatory when user has no inspiration photos. Added guard returning 422 `intake_required`. Updated all existing skip_intake tests to pre-populate inspiration photos.

**TDD criteria**:
- Upload inspiration photo with note → note stored on PhotoData
- Upload room photo with note → 422 `note_not_allowed`
- Upload inspiration photo with >200 char note → 422 `note_too_long`
- Upload inspiration photo without note → note is null
- Skip intake with no photos → 422 `intake_required`
- Skip intake with room photos only → 422 `intake_required`
- Skip intake with inspiration photo → 200 accepted

### IMP-33: Shopping error cancellation + OpenAPI method contract (T0)

**Status: DONE** — Two coverage gaps closed: (1) Added `test_cancel_during_shopping_error_abandons` to `TestCancellation` — completes the cancellation symmetry where cancel_project escapes all three error wait states (generation, iteration, and now shopping). The shopping error wait uses the same `_wait` helper with `_cancelled` checking, which was already correct but untested. (2) Added `test_http_methods_match_spec` to `TestOpenAPISchema` — verifies that every endpoint has the correct HTTP method in the OpenAPI schema. T1 iOS generates Swift method signatures from the schema; a POST accidentally registered as GET would produce non-functional Swift code. 807 total passing.

**TDD criteria**:
- `cancel_project` during shopping error wait → `step == "abandoned"`, workflow completes normally
- OpenAPI schema: each endpoint path has exactly the expected HTTP methods (GET, POST, DELETE)

### IMP-32: X-Request-ID on error responses — exception handler fix (T0)

**Status: DONE** — Fixed real production bug: `X-Request-ID` header was missing from 500 error responses. Root cause: Starlette's `BaseHTTPMiddleware` (`@app.middleware("http")`) uses a streaming response wrapper in `call_next`. When an exception handler catches an unhandled error and returns a `JSONResponse`, the middleware's post-processing (header injection) doesn't propagate to the final response. Fix: (1) store `request_id` on `request.state` in the middleware, (2) both exception handlers (`RequestValidationError` + `Exception`) now read `request.state.request_id` and set `X-Request-ID` directly on their response. Belt-and-suspenders: middleware still sets it for normal responses, exception handlers set it for error responses. 5 new tests: 404/409/422 include header, client-provided ID echoed on error, 500 includes header. 805 total passing.

**TDD criteria**:
- 404 response includes `X-Request-ID` header
- 409 response includes `X-Request-ID` header
- 422 response includes `X-Request-ID` header
- Client-provided `X-Request-ID` echoed on error (404)
- 500 unhandled exception response includes `X-Request-ID` header

### IMP-31: Workflow `approve_design` step guard — prevent premature approval (T0)

**Status: DONE** — Fixed critical workflow bug: `approve_design` signal handler had no step guard, so a premature approve during `generation` or `selection` would set `self.approved=True`. The iteration loop (`while count < 5 and not self.approved`) would then exit immediately, skipping the entire iteration phase and going straight to shopping. Users would never get to refine their design. Now the signal is ignored unless `step` is `iteration` or `approval`, matching the mock API's `_check_step(state, ("iteration", "approval"))` guard. 2 new workflow tests (generation + selection paths), 801 total passing.

**TDD criteria**:
- `approve_design` during `generation` → `approved` remains False, workflow reaches `selection` normally
- `approve_design` during `selection` → `approved` remains False, workflow waits for option selection, iteration phase still available

### IMP-30: Step guard hardening — workflow + API edge cases (T0)

**Status: DONE** — Fixed workflow `select_option` signal handler: was missing step guard, so a late signal during `iteration` could silently overwrite `selected_option` in the query result (corrupting iOS state display). Now ignores signals when `step != "selection"`, matching mock API's `_check_step(state, "selection")` guard. Added 4 new tests: (1) workflow test verifying late `select_option` during iteration is ignored, (2-3) mock API tests for approve from `generation` and `selection` steps return 409, (4) mock API test for approve blocked by error at `approval` step (realistic post-5th-iteration failure scenario). 799 total passing.

**TDD criteria**:
- Workflow `select_option` during iteration → `selected_option` unchanged, no error set
- Mock API approve from `generation` → 409 `wrong_step`
- Mock API approve from `selection` → 409 `wrong_step`
- Mock API approve with error at `approval` step → 409 `active_error`, state unchanged

### IMP-29: Inspiration photo content rejection — spec-compliant message (T0)

**Status: DONE** — Product spec PHOTO-11 and PHOTO-12 require a specific rejection message for inspiration photos containing people or animals: "Inspiration photos should show spaces, furniture, or design details — not people or animals. Please choose a different image." Previously the code returned a generic "This doesn't look like a valid inspiration photo" with Claude's raw reason appended. Fixed: (1) updated inspiration prompt to explicitly mention people/animals as rejection criteria, (2) inspiration rejections now return the spec-exact message instead of the generic one. Room photo rejections unchanged (still include Claude's reason for specificity). 3 new tests, 795 total passing.

**TDD criteria**:
- Inspiration photo with person → message contains "not people or animals" and "Please choose a different image"
- Inspiration photo with pet → same spec message
- Room photo rejection → still contains "valid room photo" and Claude's specific reason (unchanged behavior)

### IMP-28: Mock state idempotency + intake retryable flag (T0)

**Status: DONE** — Two robustness fixes from stale agent review findings: (1) `del _mock_pending_generation[project_id]` → `.pop(project_id, None)` in both `_maybe_complete_generation` and `_maybe_complete_shopping`. Prevents theoretical `KeyError` if concurrent GET requests race through the generation/shopping completion path. (2) Intake agent 500 error response now includes `retryable=True` — most agent failures (rate limits, API timeouts) are transient and iOS should offer retry. Test assertion updated. 792 total passing.

**TDD criteria**:
- `_maybe_complete_generation` uses `.pop()` instead of `del` (idempotent)
- `_maybe_complete_shopping` uses `.pop()` instead of `del` (idempotent)
- Intake 500 error response has `retryable=True`

### IMP-27: R2 delete_prefix partial failure coverage (T0)

**Status: DONE** — Fixed accidental coverage in `TestDeletePrefix` where MagicMock's `.get("Errors", [])` returned a truthy MagicMock instead of the expected empty list, silently hitting the warning branch without verifying it. Fixed by making `delete_objects` return proper S3-shaped response dicts `{"Deleted": [...]}` in existing tests. Added explicit `test_partial_failure_logs_warning` test: configures `delete_objects` to return a response with one success and one error, verifies `logger.warning("r2_delete_partial_failure", ...)` is called with correct prefix and error count. 792 total passing.

**TDD criteria**:
- delete_objects returns {"Errors": [1 error]} → logger.warning called with "r2_delete_partial_failure"
- Warning includes prefix and error list
- Existing tests use proper response dicts (not accidental MagicMock coverage)

### IMP-26: Logging configuration branch coverage (T0)

**Status: DONE** — Added 3 tests verifying logging configuration branches not covered by line-only coverage: (1) production environment selects JSONRenderer (structlog.processors.JSONRenderer), (2) unknown LOG_LEVEL string ("BOGUS") falls back to INFO filtering (BoundLoggerFilteringAtInfo), (3) explicit development environment selects ConsoleRenderer. These cover the renderer ternary branch and the `_LOG_LEVEL_MAP.get()` fallback. 791 total passing.

**TDD criteria**:
- ENVIRONMENT=production → last processor is JSONRenderer instance
- LOG_LEVEL=BOGUS → filtering class name contains "Info"
- ENVIRONMENT=development → last processor is ConsoleRenderer instance

### IMP-25: Worker main() + validation helper coverage (T0)

**Status: DONE** — Added 7 tests closing the last meaningful T0 coverage gaps. Worker main() tests (3): verify configure_logging + asyncio.run called, KeyboardInterrupt exits cleanly, fatal error calls sys.exit(1). Validation helper tests (4): _get_anthropic_client singleton creation with API key, singleton reuse on subsequent calls, JPG→JPEG format normalization, None format defaults to JPEG. worker.py 83% → 98% (only `__main__` guard uncovered), validation.py 95% → 99% (only empty-pixels line uncovered). 788 total passing.

**TDD criteria**:
- main() calls configure_logging() then asyncio.run(run_worker())
- KeyboardInterrupt caught and suppressed (no re-raise)
- RuntimeError during run → sys.exit(1)
- _get_anthropic_client creates Anthropic(api_key=...) on first call
- _get_anthropic_client returns cached client on subsequent calls
- _detect_media_type normalizes "JPG" → "image/jpeg"
- _detect_media_type defaults None format → "image/jpeg"

### IMP-24: Health check happy path coverage (T0)

**Status: DONE** — Added 5 tests for health check probe functions (`_check_postgres`, `_check_temporal`, `_check_r2`) testing the "connected" happy paths that require mocked external dependencies. Previously only the "disconnected" error paths were covered (tested via the endpoint with no real services). New tests: (1) postgres connected — mock asyncpg.connect, verify fetchval("SELECT 1") + close(), (2) postgres close runs on fetchval failure, (3) temporal connected without API key (no TLS), (4) temporal connected with API key (TLS + api_key verified in call args), (5) R2 connected via head_bucket. health.py coverage 81% → 100%. 781 total passing.

**TDD criteria**:
- _check_postgres returns "connected" when fetchval succeeds, connection closed
- _check_postgres returns "disconnected" when fetchval raises, connection still closed (finally block)
- _check_temporal returns "connected" via non-TLS path (no api_key)
- _check_temporal returns "connected" via TLS path (api_key present, tls=True verified)
- _check_r2 returns "connected" when head_bucket succeeds

### IMP-23: Validation DoS defense + shopping instructions verification (T0)

**Status: DONE** — Two targeted gap closures: (1) Extreme aspect ratio blur defense test — 512x5000 image triggers the longest-side cap in `_check_blur` (line 108-110 of validation.py, previously uncovered). Verifies the DoS guard works without OOM. (2) Revision history instructions assertions — shopping input test now verifies `.instructions` strings match the original signal payloads (not just `.type`). Mixed iterations test verifies both annotation and feedback instruction extraction. These confirm `_extract_instructions` passes correct data to T3's shopping agent. 776 total passing, validation.py coverage 93%→95%.

**TDD criteria**:
- Extreme aspect ratio image (512x5000) completes _check_blur without error
- Shopping input revision_history[0].instructions == ["Replace the couch with a modern sectional"]
- Mixed iterations: revision_history[2].instructions == ["Make it warmer"] (feedback type)

### IMP-22: Queued actions during iteration error (T0)

**Status: DONE** — Added test verifying action ordering when user submits edits while an error is active. Scenario: annotation edit fails → while error shows, user submits text feedback → retry processes re-queued annotation first (index 0), then queued feedback (index 1). Validates `_action_queue.insert(0, ...)` re-queuing semantics. 775 total passing.

**TDD criteria**:
- Annotation edit fails, error is surfaced, iteration_count stays 0
- Feedback submitted while error active is queued
- After retry, both actions process: annotation first (revision_history[0].type=="annotation"), feedback second
- Final state: iteration_count==2, revision_history has 2 entries in correct order

### IMP-21: Start-over during in-flight generation activity (T0)

**Status: DONE** — Added `_slow_generate` activity stub (2s delay) and test verifying start_over during in-flight generation discards the stale result. This is the most likely real user scenario — Gemini generation takes 30-60s in production, users will hit start-over mid-generation. Tests the `if self._restart_requested: continue` check after `execute_activity(generate_designs, ...)`. 775 total passing.

**TDD criteria**:
- While generation activity is in-flight, start_over signal fires
- After activity completes and cycle restarts: step=="intake", generated_options==[], design_brief is None
- Stale generation result NOT applied, error is None (clean restart)

### IMP-20: Delete cleanup coverage in mock mode (T0)

**Status: DONE** — Added test verifying `delete_project` cleans up `_intake_sessions` in mock mode. Previously only tested in real-intake mode (`TestRealIntakeWiring`). Also bundled with the IMP-17 generation cleanup test. 773 total passing.

**TDD criteria**:
- Delete project during active intake session removes entry from `_intake_sessions`
- Session created via `start_intake`, verified present, then cleaned up by delete

### IMP-19: WorkflowState completed-state round-trip (T0)

**Status: DONE** — Added comprehensive round-trip test for WorkflowState with all 12 fields populated, including previously untested: `scan_data` (with room_dimensions), `shopping_list` (with items, unmatched, total), `approved=True`, photo `note` field, `design_brief.inspiration_notes`. This exercises the exact JSON shape iOS parses at the end of the flow. 772 total passing.

**TDD criteria**:
- WorkflowState at "completed" step with all fields survives JSON round-trip
- scan_data.room_dimensions preserved (width_m, length_m, height_m)
- shopping_list with items, unmatched, total_estimated_cost_cents preserved
- Photo notes, design brief inspiration_notes preserved
- approved=True, error=None correctly serialized

### IMP-18: Worker _load_activities branch coverage (T0)

**Status: DONE** — Added 3 tests verifying the `_load_activities()` config switch in worker.py. Tests: (1) `use_mock_activities=True` loads mock stubs with correct Temporal activity names, (2) `use_mock_activities=False` loads real T2/T3 modules (generate.py, edit.py, shopping.py), (3) missing real modules raise `ImportError` with actionable message mentioning `USE_MOCK_ACTIVITIES`. 771 total passing.

**TDD criteria**:
- Mock branch returns 4 activities with correct @activity.defn names
- Real branch returns 4 activities from real modules (not mock_stubs)
- Missing module gives ImportError mentioning USE_MOCK_ACTIVITIES

### IMP-17: Pydantic boundary validation + delete cleanup (T0)

**Status: DONE** — Added 5 tests covering Pydantic validation boundaries and delete cleanup during generation. Tests: (1) `select_option` with index=-1 → 422 (ge=0 constraint), (2) `select_option` with index=2 → 422 (le=1 constraint), (3) empty string feedback → 422 (min_length=1 constraint), (4) annotation region_id=0 → 422 (ge=1 constraint), (5) delete project during generation → 204 with `_mock_pending_generation` cleaned up. 768 total passing.

**TDD criteria**:
- Negative selection index (-1) returns 422 validation_error from Pydantic ge=0
- Oversized selection index (2) returns 422 validation_error from Pydantic le=1
- Empty string feedback returns 422 validation_error from Pydantic min_length=1
- Annotation with region_id=0 returns 422 validation_error from Pydantic ge=1
- Delete during generation step returns 204 and cleans up _mock_pending_generation

### IMP-16: Mock API edge case coverage (T0)

**Status: DONE** — Added 5 tests covering previously untested mock API error paths and edge cases. Tests: (1) `send_intake_message` without `start_intake` → 409 "Call start_intake first", (2) `retry` when no error exists → 200 no-op, (3) `start_over` from scan step → 200 with photos preserved, (4) `start_over` from approval step (not yet approved) → 200, (5) `start_over` blocked from shopping step → 409. 763 total passing.

**TDD criteria**:
- Intake message at correct step but without session initialization returns 409 with "start_intake" in message
- Retry with error=None returns 200, state.error remains None
- Start-over from scan step resets to intake, photos preserved
- Start-over from approval (approved=False) resets to intake
- Start-over from shopping step returns 409 wrong_step

### IMP-15: Unique mock edit URLs for chain integrity (T0)

**Status: DONE** — Mock `edit_design` stub now returns unique URLs per invocation via `uuid4` hex prefix (e.g., `mock/edit_a1b2c3d4.png`). Previously returned static `mock/edit.png`, making `test_revision_chain_integrity` pass trivially since all revisions had identical URLs. Updated conformance test to check URL prefix+suffix pattern and verify two calls produce different URLs. 758 total passing.

**TDD criteria**:
- Mock `edit_design` returns URLs matching `https://r2.example.com/mock/edit_{hex}.png`
- Two calls with same input produce different URLs
- `test_revision_chain_integrity` (workflow) now non-trivially verifies chaining via unique URLs

### IMP-14: Mock shopping step with polling-based completion (T0)

**Status: DONE** — Mock API's `approve_design` now transitions to `step="shopping"` with `shopping_list=None` first, then auto-completes to `step="completed"` with populated shopping list on next poll after `MOCK_SHOPPING_DELAY`. Mirrors GAP-5 generation pattern. Cleanup in `delete_project` and `start_over`. 4 new tests, 758 total passing.

**TDD criteria**:
- After approve with delay, `state.step == "shopping"` and `shopping_list is None`
- After poll with delay=0, `state.step == "completed"` with 2 items and unmatched
- Shopping list includes `unmatched` items with Google Shopping fallback URLs
- Delete project during shopping step cleans up pending state

### IMP-13: Activity contract round-trip tests + edit input completeness (T0)

**Status: DONE** — Added 13 new tests: 5 activity Input model JSON round-trip tests (GenerateDesignsInput, EditDesignInput x2, GenerateShoppingListInput, IntakeChatInput), 5 activity Output model round-trip tests (GenerateDesignsOutput, EditDesignOutput, GenerateShoppingListOutput, IntakeChatOutput x2), 3 mock stub output conformance tests. Also added missing assertions to workflow edit input tests: `project_id`, `room_photo_urls`, `inspiration_photo_urls`. 754 total passing.

**TDD criteria**:
- All 4 activity Input models survive JSON round-trip with fully-populated nested data (DesignBrief, StyleProfile, InspirationNote, RoomDimensions, AnnotationRegion, RevisionRecord, ChatMessage)
- All 4 activity Output models survive JSON round-trip including optional fields (None, empty lists)
- Mock stubs produce outputs satisfying all Pydantic constraints (min_length, ge/le, etc.)
- Edit input builder forwards `project_id`, `inspiration_photo_urls`, and all context fields to T2 activity

### IMP-12: REGEN-5 complete coverage (T0)

**Status: DONE** — Extended `test_sixth_iteration_blocked_after_cap` to also verify text feedback is blocked after 5-iteration cap (not just annotations). Directly covers REGEN-5 product spec test case: "Both 'Annotate' and 'Regenerate' buttons are disabled." 741 total passing.

### IMP-11: Complete OpenAPI response documentation (T0)

**Status: DONE** — Added missing response codes to OpenAPI `responses` dicts: 413 on `upload_photo` (file too large), 409 on `start-over` (blocked by approved/completed). No code behavior changes, documentation only. 741 total passing.

### IMP-10: Premium happy path e2e test (T0)

**Status: DONE** — Full-featured end-to-end test exercising the "premium experience" path: LiDAR scan upload + inspiration photos with notes (during scan step) + full intake conversation + mixed annotation/feedback iterations. Validates data preservation across all step transitions. 1 new test, 741 total passing.

**TDD criteria**:
- Create → 2 room photos → 1 inspiration w/ note during scan → LiDAR scan → intake → confirm → select → 1 annotation + 1 feedback → approve
- Final state: 3 photos (note preserved), scan_data (dimensions preserved), design_brief, iteration_count=2, mixed revision_history, shopping_list

### IMP-9: Block approve_design during active error (T0)

**Status: DONE** — Mock API parity fix: the Temporal workflow's `approve_design` signal silently ignores the call when `self.error is not None`, but the mock API had no such check. Added error guard to `approve_design` endpoint — returns 409 `active_error` when workflow has an unresolved error. 2 new tests, 740 total passing.

**TDD criteria**:
- approve_design with active error returns 409 `active_error`, state unchanged
- approve_design after retry (error cleared) returns 200, step becomes "completed"

### IMP-8: OpenAPI response documentation + CLAUDE.md update (T0)

**Status: DONE** — Added 422 to `responses` dict on 4 endpoints that return custom 422 errors from handler logic (upload_photo, skip_intake, submit_text_feedback, select_option). Updated CLAUDE.md status from "301 tests, P2 blocked" to "738 tests, P2 in progress."

### IMP-7: Preserve photo notes when intake is skipped (T0)

**Status: DONE** — Bug fix: `_generation_input()` only extracted inspiration notes from `DesignBrief.inspiration_notes`, silently dropping `PhotoData.note` values when intake was skipped. Added fallback to build `InspirationNote` objects from photo notes. 1 new workflow test, 738 total passing.

The bug: user uploads inspiration photos with notes → skips intake → `design_brief` is None → generation receives zero notes. Fix: when `design_brief` has no inspiration notes, fall back to `[InspirationNote(photo_index=i, note=p.note) for i,p in enumerate(inspo) if p.note]`.

**TDD criteria**:
- Upload 2 inspiration photos (1 with note, 1 without) → skip intake → generation receives exactly 1 InspirationNote with correct photo_index and note text
- Photos without notes produce no fallback InspirationNote
- Both inspiration photos still appear in inspiration_photo_urls

### IMP-6: Edge case test coverage (T0)

**Status: DONE** — Added REGEN-4 mixed iteration types test, start-over during generation, and double-approve guard. 3 new tests, 737 total passing.

Three product spec edge cases covered:
- **REGEN-4**: Annotation and text feedback iterations share the 5-count pool. Test verifies 3 annotations + 2 feedbacks = 5 total, auto-transition to approval, revision history preserves mixed types.
- **Start over during generation**: User confirms intake, generation starts (pending), user calls start-over. Verifies mock pending generation is cleaned up.
- **Double approve**: Approving an already-completed project returns 409 (step is "completed", not "iteration" or "approval").

### IMP-5: Allow photo uploads during scan step (T0)

**Status: DONE** — Fixed product spec bug where auto-transition to "scan" blocked inspiration photo uploads. 3 new tests (1 existing updated), 734 total passing.

The product spec flow (§4.3) has inspiration photos uploaded *before* the scan step, but the auto-transition after 2 room photos moved step to "scan" prematurely, returning 409 for subsequent uploads. Fix: expanded `upload_photo` allowed steps from `"photos"` to `("photos", "scan")`, consistent with `delete_photo` (INT-3) which already allows both steps.

**TDD criteria**:
- Upload inspiration photo during scan step → 200, step stays "scan"
- Upload room photo during scan step → 200, additional photo stored
- Upload photo during intake step → 409 blocked (past the photo/scan window)
- Updated existing wrong-step test from "scan" → "intake"

### IMP-2: Intake agent integration quality (discovered during wiring)

*Placeholder — items discovered while wiring INT-2 go here. The ralph loop agent should add specific issues as sub-bullets when found during integration testing.*

Potential areas (to be confirmed during integration):
- Agent response latency under real API conditions
- Partial brief accumulation fidelity across turns
- Edge case: user sends empty message or single word
- Edge case: user's first answer covers all domains (should draft immediately)
- Quality of options/quick-reply chips (relevant, not generic)
- Summary brief completeness when agent drafts early (< max_turns)

---

## T1 iOS Remaining Features

### Must-do

| Feature | Spec Section | What's Missing |
|---------|-------------|----------------|
| 5-iteration limit gate | 4.7 | Counter displays but submit buttons aren't disabled at round 5 |
| Intake mode picker | 4.5 | Hardcoded to "full"; need Quick/Full/Open Conversation selector |
| Approval confirmation dialog | 4.9 | No "Keep editing" alternative; single approve button |
| Rich region editor (bottom sheet) | 4.7.4 | See UX design below — structured inputs compile to `instruction` |
| Onboarding tooltip | 4.10 | First-launch tooltip: "Your design data is temporary — save your final image to Photos when you're done." |
| Approval save reminder | 4.10 | On approval screen: "Make sure to save your design image and copy your specs. Project data will be deleted after 24 hours." |
| Non-LiDAR tip banner | 4.9.3 | When scan was skipped: "We matched products by style. For size-verified recommendations, use Room Scan on an iPhone Pro next time." |
| Text feedback 10-char min | 4.8 | Annotation mode enforces it; text mode only checks non-empty |
| Brief summary correction | 4.5 | Only "Looks Good" button; need "I want to change something" option |

### Nice-to-have

| Feature | Spec Section | What's Missing |
|---------|-------------|----------------|
| "Why matched" on product cards | 4.9.3 | `whyMatched` data exists; not rendered in `ProductCard` |
| Inspiration photo notes | 4.3.2 | Model has `note` field; UI has no text input for it |
| Shopping list sharing | 4.9.3 | No "Share Shopping List", "Copy All", or per-product "Copy Link" |
| Side-by-side auto-detection | 4.6 | Always defaults to swipe; spec says side-by-side on tablet |

### Deprioritized

| Feature | Spec Section | Notes |
|---------|-------------|-------|
| Collapsible product groups | 4.9.3 | Flat sections work fine for MVP |
| Photo upload diagram | 4.3 | Text instruction is sufficient |

### Deferred to P2

| Feature | Spec Section | What's Missing |
|---------|-------------|----------------|
| RoomPlan/RoomCaptureView | 4.4 | Stub only; real LiDAR scanning requires ARKit integration |

### Rich Region Editor UX Design

The structured fields are a T1-only UI change — no contract changes. Action/Style/Avoid inputs compile into the existing `instruction` string that T2 already consumes.

**Presentation**: Bottom sheet (replaces current inline text field). Opens when a region is tapped or created.

```
┌─────────────────────────────────────────┐
│ ● 1                                  ✕  │
├─────────────────────────────────────────┤
│ What do you want to do?                 │
│ ┌─────────┐ ┌────────┐ ┌─────────────┐ │
│ │ Replace │ │ Remove │ │ Change look │ │
│ └─────────┘ └────────┘ └─────────────┘ │
│ ┌────────┐ ┌────────────┐               │
│ │ Resize │ │ Reposition │               │
│ └────────┘ └────────────┘               │
├─────────────────────────────────────────┤
│ Describe the change (required, 10+ ch)  │
│ ┌─────────────────────────────────────┐ │
│ │ Swap for a low-pile neutral rug     │ │
│ └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│ Style preferences                       │
│ ┌──────────┐ ┌─────────┐ ┌───────────┐ │
│ │ cheaper  │ │ minimal │ │✓pet-safe  │ │
│ └──────────┘ └─────────┘ └───────────┘ │
│ ┌─────────┐ ┌────────┐ ┌─────────────┐ │
│ │ premium │ │  cozy  │ │   modern    │ │
│ └─────────┘ └────────┘ └─────────────┘ │
│ ┌─────────────┐ ┌───────────────────┐   │
│ │ kid-friendly│ │  low maintenance  │   │
│ └─────────────┘ └───────────────────┘   │
├─────────────────────────────────────────┤
│ Avoid (optional)                        │
│ ┌─────────────────────────────────────┐ │
│ │ brass, glossy finish                │ │
│ └─────────────────────────────────────┘ │
├─────────────────────────────────────────┤
│         [ Save ]  [ Cancel ]            │
└─────────────────────────────────────────┘
```

**Assembly rule** — all fields compile into one `instruction` string:
```
"{Action} this area. {Description}. Style: {comma-joined style chips}. Avoid: {avoid text}."
```

Example: User taps "Replace", toggles "pet-safe", types "swap for a low-pile neutral rug", types "brass, glossy finish" in avoid:
```
"Replace this area. Swap for a low-pile neutral rug. Style: pet-safe. Avoid: brass, glossy finish."
```

**Rules**:
- Action chips: single-select (one verb per region). Default: none selected.
- Style chips: multi-select toggles. Default: none selected.
- Description: required, min 10 characters. This is where specificity goes.
- Avoid: optional free text, comma-separated.
- 10-char minimum applies to Description field only.
- If Action is not selected, instruction starts with the Description directly.
- The assembled string is stored in `AnnotationRegion.instruction` — no new contract fields.

**TDD criteria**:
- Tap region -> bottom sheet opens with empty fields
- Select "Replace" + type 10+ chars + Save -> `instruction` contains "Replace this area. {text}"
- Toggle "cheaper" + "minimal" -> instruction contains "Style: cheaper, minimal"
- Type avoid text -> instruction contains "Avoid: {text}"
- Description < 10 chars -> Save button disabled
- Cancel -> region deleted (same as current behavior)
- Edit existing region -> sheet opens with pre-parsed fields

---

## Parallelization Strategy

All four teams can work simultaneously. The dependency graph has two phases:

### Phase A: Independent Work (all teams parallel)

```
T0 ─── INT-3 (delete photo endpoint)
   └── INT-6 (add lifestyle field to DesignBrief — contract change)
   └── GAP-5 (mock generation step)
   └── INT-2 (wire real intake agent — T3 merged, unblocked)
   └── INT-1 prep (activity import swap structure + retry policy)

T2 ─── GAP-2 (real captions from Gemini text response)
   └── GAP-1 (room dimensions in generation prompt)

T3 ─── GAP-3 (dimension filtering implementation)
   └── GAP-4 (fit_status semantic fix — decouple from confidence)

T1 ─── Must-do iOS fixes (iteration limit, 10-char min, intake mode picker,
       approval dialog, rich region editor, tooltips/reminders, tip banner,
       brief correction)
   └── GAP-5 dependent: add GeneratingScreen Maestro flow once mock supports it
```

**No cross-team blocking** in Phase A. Each team works in their own files.

### Phase B: Integration Wiring (T0 leads, sequential)

Blocked on T2 merge for final activity swap:

```
T0 ─── INT-1 finalize (swap mock_stubs imports to real T2+T3 activities — needs T2 merged)
   └── INT-4 (health checks)
   └── INT-5 (env vars + deploy)

T1 ─── Maestro E2E against real backend (acceptance flows below)
```

### Phase A Duration Estimate

All Phase A items are independent single-PR changes. With 4 teams working in parallel, Phase A is bounded by the longest single-team queue:
- T0: ~5 items (INT-3, INT-6, GAP-5, INT-2, INT-1 prep)
- T2: ~2 items (GAP-2, GAP-1)
- T3: ~2 items (GAP-3, GAP-4)
- T1: ~12 small items (can batch into 2-3 PRs)

### Phase B Duration Estimate

INT-1 finalization (needs T2 merged) is the remaining critical path. INT-2 (intake wiring) moved to Phase A since T3 is merged. INT-1 finalization is a one-line import swap per activity once T2 code is available.

---

## Maestro Acceptance Tests (Post-Integration)

These flows verify end-to-end behavior when the real backend is wired up. They complement the existing mock-based Maestro flows.

### Prerequisites

All acceptance flows require:
- Real backend running (not mock)
- Simulator with `--maestro-test` launch argument (skips photo upload)
- Network connectivity to backend API

### ACCEPT-01: Happy Path with Real Generation

```yaml
# Verifies: INT-1, GAP-2, GAP-5
# Full flow: create -> skip scan -> intake -> REAL generation -> select -> iterate -> approve -> shopping
flow: accept-01-real-generation.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"

# Create project
- tapOn:
    id: "home_new_project"
- assertVisible: "Scan"

# Skip scan
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"

# Quick intake (tap through 3 quick-reply options)
- assertVisible: "Welcome"
- tapOn:
    id: "chat_option_1"
- assertVisible: "Question 2"
- tapOn:
    id: "chat_option_1"
- assertVisible: "Question 3"
- tapOn:
    id: "chat_send"
    # Open-ended Q3 needs typed answer
- inputText: "I want a calm, minimal space with natural light"
- tapOn:
    id: "chat_send"
# Summary
- assertVisible: "Summary"
- tapOn:
    id: "chat_confirm_brief"

# REAL generation: verify GeneratingScreen appears and polls
- assertVisible: "Creating your designs"
- screenshot: accept-01-generating.png
# Wait for generation to complete (up to 60s for real Gemini call)
- extendedWaitUntil:
    visible: "Choose This Design"
    timeout: 60000
- screenshot: accept-01-selection.png

# Verify real captions (not "Design Option A")
- assertNotVisible: "Design Option A"
- assertNotVisible: "Design Option B"

# Select first option
- tapOn:
    id: "selection_card_0"
- tapOn:
    id: "selection_choose"

# Text feedback iteration
- assertVisible: "Refine Design"
- tapOn:
    id: "iteration_text_input"
- inputText: "Make the lighting warmer and add a cozy throw blanket"
- tapOn:
    id: "iteration_submit"
# Wait for real edit (up to 60s)
- extendedWaitUntil:
    visible: "Round 2 of 5"
    timeout: 60000
- screenshot: accept-01-iteration.png

# Approve
- tapOn:
    id: "iteration_approve"
- assertVisible: "Your Design"
# Wait for shopping list generation
- extendedWaitUntil:
    visible: "Shopping List"
    timeout: 60000
- screenshot: accept-01-output.png

# Verify shopping list has real products (not mock data)
- assertVisible: "$"
- screenshot: accept-01-shopping.png
```

### ACCEPT-02: Intake Conversation Quality

```yaml
# Verifies: INT-2 (real T3 intake agent)
# Tests: adaptive questions, quick-reply format, summary with brief
flow: accept-02-intake-quality.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"

# Verify first message is from agent (not canned)
- assertVisible: "Welcome"

# Answer with quick-reply chip
- tapOn:
    id: "chat_option_1"
# Verify agent acknowledges and asks follow-up
- assertVisible: "Question 2"
- screenshot: accept-02-q2.png

# Type a free-text answer that covers multiple domains
- tapOn:
    id: "chat_input"
- inputText: "I have two dogs and hate how dark and cluttered it feels"
- tapOn:
    id: "chat_send"

# Agent should acknowledge both constraints and pain point
# Progress should reflect multiple domains covered
- screenshot: accept-02-multi-domain.png

# Continue until summary
- repeat:
    times: 5
    commands:
      - runFlow:
          when:
            notVisible: "Summary"
          commands:
            - tapOn:
                id: "chat_option_1"
                optional: true
            - tapOn:
                id: "chat_input"
                optional: true
            - inputText: "Warm and cozy modern style"
              optional: true
            - tapOn:
                id: "chat_send"
                optional: true

# Verify summary appears with brief
- assertVisible: "Summary"
- screenshot: accept-02-summary.png

# Confirm brief
- tapOn:
    id: "chat_confirm_brief"
- assertVisible: "Creating your designs"
```

### ACCEPT-03: Annotation Edit Preservation

```yaml
# Verifies: INT-1 (edit_design activity wired), annotation artifacts removed
# Tests: circle annotations sent to Gemini, revised image has no circles
flow: accept-03-annotation-edit.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"

# Speed through intake
- tapOn:
    id: "chat_option_1"
- tapOn:
    id: "chat_option_1"
- inputText: "Clean modern look with lots of plants"
- tapOn:
    id: "chat_send"
- tapOn:
    id: "chat_confirm_brief"

# Wait for generation
- extendedWaitUntil:
    visible: "Choose This Design"
    timeout: 60000

# Select and go to iteration
- tapOn:
    id: "selection_card_0"
- tapOn:
    id: "selection_choose"
- assertVisible: "Refine Design"

# Place an annotation circle (tap center of canvas)
- tapOn: "Mark Areas"
- tapOn:
    point: "50%,30%"
- screenshot: accept-03-annotation-placed.png

# Type instruction for the region
- inputText: "Replace this lamp with a tall fiddle leaf fig plant"
- tapOn:
    id: "iteration_submit"

# Wait for real edit
- extendedWaitUntil:
    visible: "Round 2 of 5"
    timeout: 60000
- screenshot: accept-03-revised.png
# Visual check: revised image should not have red/blue/green circles
```

### ACCEPT-04: Shopping List with Real Products

```yaml
# Verifies: INT-1 (generate_shopping_list wired), real Exa results
# Tests: real product names, prices, buy links, grouped display
flow: accept-04-real-shopping.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"

# Quick intake
- tapOn:
    id: "chat_option_1"
- tapOn:
    id: "chat_option_1"
- inputText: "Something bright and Scandinavian"
- tapOn:
    id: "chat_send"
- tapOn:
    id: "chat_confirm_brief"

# Wait for generation + select + approve
- extendedWaitUntil:
    visible: "Choose This Design"
    timeout: 60000
- tapOn:
    id: "selection_card_0"
- tapOn:
    id: "selection_choose"
- tapOn:
    id: "iteration_approve"

# Wait for shopping list
- extendedWaitUntil:
    visible: "Shopping List"
    timeout: 90000
- tapOn:
    id: "output_shopping"

# Verify real products (not mock "Modern Accent Chair")
- assertNotVisible: "Modern Accent Chair"
- assertNotVisible: "West Elm"
# Verify has actual $ prices
- assertVisible: "$"
# Verify grouped sections exist
- assertVisible: "Furniture"
- screenshot: accept-04-shopping-real.png

# Verify unmatched items have Google Shopping fallback
- scrollUntilVisible:
    element: "Search on Google"
    direction: "DOWN"
    timeout: 5000
    optional: true
- screenshot: accept-04-shopping-bottom.png
```

### ACCEPT-05: Error Recovery and Retry

```yaml
# Verifies: workflow error handling, retry UI
# Tests: generation failure shows error + retry, retry succeeds
flow: accept-05-error-retry.yaml
---
# This flow requires a test backend mode that fails generation once then succeeds.
# Skip if not available. Useful for manual testing.
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
      simulate-generation-failure: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"
- tapOn:
    id: "chat_option_1"
- tapOn:
    id: "chat_option_1"
- inputText: "Cozy reading nook"
- tapOn:
    id: "chat_send"
- tapOn:
    id: "chat_confirm_brief"

# Generation should fail
- extendedWaitUntil:
    visible: "Something went wrong"
    timeout: 60000
- screenshot: accept-05-error.png

# Tap retry
- tapOn: "Retry"

# Should succeed on retry
- extendedWaitUntil:
    visible: "Choose This Design"
    timeout: 60000
- screenshot: accept-05-recovered.png
```

### ACCEPT-06: Five-Iteration Limit

```yaml
# Verifies: T1 iteration limit gate
# Tests: after 5 rounds, submit is disabled, approve is the only option
flow: accept-06-iteration-limit.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"
- tapOn:
    id: "chat_option_1"
- tapOn:
    id: "chat_option_1"
- inputText: "Modern industrial"
- tapOn:
    id: "chat_send"
- tapOn:
    id: "chat_confirm_brief"
- extendedWaitUntil:
    visible: "Choose This Design"
    timeout: 60000
- tapOn:
    id: "selection_card_0"
- tapOn:
    id: "selection_choose"

# 5 iterations of text feedback
- repeat:
    times: 5
    commands:
      - tapOn:
          id: "iteration_text_input"
      - inputText: "Make it slightly warmer with more wood tones"
      - tapOn:
          id: "iteration_submit"
      - extendedWaitUntil:
          visible: "Round"
          timeout: 60000

# After 5 rounds: submit should be disabled
- assertVisible: "Round 5 of 5"
- screenshot: accept-06-limit-reached.png
# The "Generate Revision" button should be disabled or hidden
# Only "Approve This Design" should be actionable
- tapOn:
    id: "iteration_approve"
- assertVisible: "Your Design"
```

### ACCEPT-07: Intake Mode Selection

```yaml
# Verifies: T1 intake mode picker
# Tests: Quick/Full/Open Conversation options appear, selection affects flow
flow: accept-07-intake-modes.yaml
---
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"

# Verify mode selection screen appears
- assertVisible: "Quick Intake"
- assertVisible: "Full Intake"
- assertVisible: "Open Conversation"
- screenshot: accept-07-mode-picker.png

# Select Quick mode
- tapOn: "Quick Intake"

# Quick mode should have ~3 questions (fewer turns)
- assertVisible: "Question 1"
- tapOn:
    id: "chat_option_1"
- tapOn:
    id: "chat_option_1"
# Should reach summary faster than full mode
- extendedWaitUntil:
    visible: "Summary"
    timeout: 30000
- screenshot: accept-07-quick-summary.png
```

### ACCEPT-08: Delete Photo from Project

```yaml
# Verifies: INT-3 (delete photo endpoint), optimistic rollback
# Tests: delete photo -> removed from grid, server confirms
flow: accept-08-delete-photo.yaml
---
# This flow requires a non-skip-photos mode to have actual photos to delete.
# Use a variant that uploads mock photos but doesn't skip the photo step.
- launchApp:
    clearState: true
    arguments:
      maestro-test: "true"
- tapOn:
    id: "home_new_project"

# Project starts at scan step (skipPhotos=true adds 3 mock photos)
# Navigate to verify photos exist in state
- tapOn:
    id: "scan_skip"
- tapOn: "Skip"
# At this point photos are in the state but we skipped past the photo screen
# This test is more useful when photo screen is reachable with existing photos
# For now, verify the delete endpoint via API-level tests (TDD criteria above)
```

---

## Recommended Priority Order

### Phase A (all teams in parallel)

| Priority | Item | Team | Unblocks |
|----------|------|------|----------|
| A1 | INT-3: Delete photo endpoint | T0 | ACCEPT-08 |
| A2 | INT-6: Add `lifestyle` to DesignBrief | T0 | T3 removes workaround, T1 mirrors field |
| A3 | GAP-5: Mock generation step | T0 | T1 GeneratingScreen testing |
| A4 | INT-2: Wire real intake agent (T3 merged, unblocked) | T0 | ACCEPT-02 |
| A5 | INT-1 prep: activity import swap | T0 | Phase B |
| A6 | GAP-2: Real captions | T2 | ACCEPT-01 |
| A7 | GAP-1: Room dimensions in prompt | T2 | - |
| A8 | GAP-3: Dimension filtering | T3 | ACCEPT-04 |
| A9 | GAP-4: fit_status semantic fix | T3 | - |
| A10 | IMP-1: Intake agent eval dataset + prompt tuning | T3 | Prompt quality |
| A11 | T1 must-do fixes (9 items + rich region editor) | T1 | ACCEPT-06, ACCEPT-07 |

### Phase B (T0 leads, after Phase A)

| Priority | Item | Team | Unblocks |
|----------|------|------|----------|
| B1 | INT-1 finalize: swap all imports (needs T2 merged) | T0 | ACCEPT-01, ACCEPT-03, ACCEPT-04 |
| B2 | INT-4: Health checks | T0 | Deployment |
| B3 | INT-5: Env vars + deploy | T0 | All ACCEPT flows on real infra |
| B4 | Maestro acceptance suite | T1 | Final sign-off |
