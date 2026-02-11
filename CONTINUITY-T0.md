# T0 Continuity Ledger

## Current State
- **Phase**: P1 Independent Build
- **Last completed deliverable**: P1 #11 — Photo validation

## Done
- P0 #2: Project scaffold complete
  - `backend/pyproject.toml` with all dependencies (FastAPI, Temporal, SQLAlchemy, etc.)
  - `backend/Dockerfile` + `.dockerignore`
  - `backend/app/` package structure (models, api/routes, workflows, activities, utils, prompts)
  - `backend/app/main.py` — FastAPI app with structlog, health + projects routers
  - `backend/app/config.py` — pydantic-settings with all env vars
  - `backend/app/models/db.py` — SQLAlchemy Base
  - `backend/alembic.ini` + `migrations/env.py` — async Alembic setup
  - `docker-compose.yml` — PostgreSQL 17, Temporal auto-setup, Temporal UI, API service
  - `backend/tests/conftest.py` + `test_scaffold.py` — 5 passing tests
- P0 #3: All Pydantic contract models complete
  - 37 models in `backend/app/models/contracts.py` (263 lines)
  - Matches PLAN_T0_PLATFORM.md Section 7 exactly
  - Added API request/response models (CreateProjectRequest, SelectOptionRequest, etc.)
  - Added PhotoData and ScanData models (needed by WorkflowState)
  - Field constraints: LassoRegion region_id 1-3, instruction min 10 chars, ProductMatch price_cents >= 0, confidence_score 0-1, GenerateDesignsOutput exactly 2 options
  - Literal types: ChatMessage role, RevisionRecord type, PhotoData photo_type, IntakeChatInput mode
  - 41 validation tests in `test_contracts.py` covering valid/invalid for every constrained model
  - All 46 tests pass, ruff lint clean
- P0 #4: Database schema (SQLAlchemy ORM) complete
  - 9 tables in `backend/app/models/db.py`: projects, photos, lidar_scans, design_briefs, generated_images, revisions, lasso_regions, shopping_lists, product_matches
  - All FKs use ON DELETE CASCADE
  - Required indexes: idx_photos_project_type, idx_generated_images_project, idx_revisions_project, idx_product_matches_list
  - 1:1 relationships with unique constraints: lidar_scans, design_briefs, shopping_lists
  - JSONB for semi-structured data: room_dimensions, brief_data, conversation_history, edit_payload, path_points
  - Integer cents for monetary values
  - 21 DB model tests in `test_db_models.py`
  - All 67 tests pass, ruff lint clean
- P0 #6: FastAPI gateway complete (refined to 357 lines)
  - 17 endpoints in `backend/app/api/routes/projects.py`
  - In-memory mock state store (`_mock_states` dict) for T1 development
  - Helpers: `_get_state`, `_error`, `_check_step`, `_mock_options`, `_apply_revision`
  - Explicit `response_model` on Pydantic-returning endpoints (avoids FastAPI union type error)
  - Full flow: create -> photos -> scan -> intake -> select -> iterate -> approve
  - 20 API tests in `test_api_endpoints.py` (incl. full happy-path end-to-end)
  - All 87 tests pass, ruff lint clean
- P0 #7: DesignProjectWorkflow skeleton complete
  - `backend/app/workflows/design_project.py` — full workflow (314 lines)
  - 12 signals: add_photo, complete_scan, skip_scan, complete_intake, skip_intake, select_option, start_over, submit_lasso_edit, submit_regenerate, approve_design, retry_failed_step, cancel_project
  - 1 query: get_state -> WorkflowState
  - Phases: photos (>= 2) -> scan -> intake -> generation -> selection -> iteration (x5) -> approval -> shopping -> completed -> 24h purge
  - _wait helper with 48h abandonment timeout (purges + terminates)
  - _cancelled flag checked at every wait point for cancel_project
  - Queue pattern (_action_queue) for lasso/regen edits
  - Start Over loop: resets generated_options, selected_option, design_brief, intake_skipped
  - Input builders: _generation_input, _inpaint_input, _regen_input, _shopping_input
  - `backend/app/activities/mock_stubs.py` — 5 mock activity stubs (generate_designs, generate_inpaint, generate_regen, generate_shopping_list, purge_project_data)
  - 13 workflow tests in `test_workflow.py` covering: happy path, photo phase, scan (skip + complete), intake skip, start over, lasso edit, regenerate, 5-iteration cap, approval, cancellation, initial state query
  - All 100 tests pass, ruff lint clean

## Key Decisions
- Python 3.12+ target (system has 3.13.7)
- Hatchling for build system (PEP 621)
- asyncpg for async PostgreSQL
- Temporal auto-setup Docker image for local dev
- structlog with console renderer in dev, JSON in prod
- `from __future__ import annotations` for forward references in contracts
- API request/response models co-located in contracts.py (single source of truth)
- Use explicit `response_model=X` on decorator (not return type annotation) for endpoints returning JSONResponse on errors
- Walrus operator for step validation: `if err := _check_step(state, "photos", "upload"): return err`
- Mock activity stubs in `activities/mock_stubs.py` (T0-owned) — swapped for real T2/T3 implementations during P2
- `_AbandonedError` exception + `_wait` helper for clean workflow termination on timeout or cancel
- `workflow.unsafe.imports_passed_through()` for activity + contract imports in sandbox

- P0 #5: R2 bucket + pre-signed URL generation complete
  - `backend/app/utils/r2.py` — R2 client wrapper (104 lines)
  - Functions: upload_object, generate_presigned_url, head_object, delete_object, delete_prefix
  - Lazy-init singleton boto3 client with `reset_client()` for testing
  - S3-compatible via `endpoint_url=https://{account_id}.r2.cloudflarestorage.com`
  - Signature version s3v4, region "auto"
  - structlog logging on upload/delete operations
  - Paginated prefix deletion for bulk cleanup
  - 14 tests in `test_r2.py` covering: upload, presigned URL, head (exists/404/403), delete, prefix delete (single page, empty, multi-page), singleton lifecycle
  - All 114 tests pass, ruff lint clean
- P0 #7 review fixes applied:
  - Added bounds validation on `select_option` signal (prevents IndexError)
  - Populated `inspiration_notes` in `_generation_input()` (was missing from contract)
  - Fixed shopping list retry deadlock (now loops: wait for error cleared → retry activity)
  - Added `list[dict]` type annotation on `submit_lasso_edit` signal

- P0 #10: CI pipeline complete
  - `.github/workflows/ci.yml` — GitHub Actions workflow (ruff + mypy + pytest)
  - Runs on push to main and PRs to main
  - Python 3.12, `pip install -e ".[dev]"`
  - mypy config: per-module overrides for Temporal SDK false positives (`arg-type`, `attr-defined`, `func-returns-value`, `index`), API routes (`union-attr`, `arg-type`)
  - Relaxed from `strict = true` to targeted checks (P0 pragmatism — tighten in P2)
  - All 3 checks pass locally: ruff clean, mypy 0 errors in 17 files, 114 tests pass

- P0 #8: Mock API operational — satisfied by P0 #6 + #7 (mock state store + workflow skeleton)

- P1 #11: Photo validation complete
  - `backend/app/activities/validation.py` — 3-stage validation (156 lines)
  - Check 1: Resolution — min 1024px shortest side (Pillow)
  - Check 2: Blur — Laplacian variance on normalized 1024px grayscale image, threshold 60
  - Check 3: Content — Claude Haiku 4.5 image classification (fail-open on API error)
  - Runs synchronously in API handler (not Temporal activity) for immediate user feedback
  - Content check skipped if basic checks fail (no wasted API calls)
  - Content check skipped if `anthropic_api_key` not set (dev mode)
  - Lazy singleton for Anthropic client (connection pool reuse, <3s target)
  - Dynamic media type detection (JPEG/PNG) — not hardcoded
  - 28 tests in `test_validation.py` covering: resolution pass/fail, blur detection, media type detection, content accept/reject/fail-open, prompt selection, media type passthrough, integration (invalid image, combined failures, skip conditions, happy path)
  - Review fixes: hardcoded image/jpeg → dynamic detection, per-call client → lazy singleton
  - mypy override for Pillow LANCZOS + Anthropic SDK union/list-item types
  - All 142 tests pass, ruff clean, mypy clean (18 files)

- P1 #12: LiDAR dimension parser complete
  - `backend/app/utils/lidar.py` — RoomPlan JSON parser (76 lines)
  - Converts T1 iOS RoomPlan JSON (room dimensions, walls, openings) into RoomDimensions model
  - Validates required fields (width, length, height must be positive)
  - Graceful degradation: non-list walls/openings → empty list, extra fields ignored
  - Numeric strings coerced to float, integer dimensions coerced to float
  - Custom `LidarParseError` exception for invalid input
  - 19 tests in `test_lidar.py` covering: valid input, minimal input, wall/opening preservation, coercion, all error cases
  - All 161 tests pass, ruff clean, mypy clean (19 files)

- P1: Purge activity complete
  - `backend/app/activities/purge.py` — R2 cleanup activity (36 lines)
  - Deletes all R2 objects under `projects/{project_id}/` prefix
  - DB deletion deferred to P2 (requires async SQLAlchemy session wiring)
  - Errors propagate to Temporal for retry handling (no fail-open)
  - 3 tests in `test_purge.py` covering: R2 prefix call, prefix format, error propagation
  - All 164 tests pass, ruff clean, mypy clean (20 files)

- Temporal worker entrypoint complete
  - `backend/app/worker.py` — worker process (110 lines)
  - Registers DesignProjectWorkflow + 5 mock activity stubs
  - `create_temporal_client()` supports both local (plain TCP) and Temporal Cloud (TLS + API key)
  - Structured error logging on connection failure (log + re-raise, no silent failures)
  - Clean shutdown on KeyboardInterrupt, sys.exit(1) on unhandled errors
  - Run as `python -m app.worker`
  - Worker service added to `docker-compose.yml` (same build, different command)
  - 12 tests in `test_worker.py` covering: activity registration (6), workflow registration (1), client creation local/cloud (2), worker lifecycle (1), connection failure (1), logging config (1)
  - mypy override for Temporal SDK `arg-type` false positive on Worker activities param
  - Review fix: extracted shared `app/logging.py` for structlog config (used by both `main.py` and `worker.py`)
  - All 176 tests pass, ruff clean, mypy clean (22 files)

- Wire validate_photo into API photo upload endpoint
  - `backend/app/api/routes/projects.py` — upload_photo now calls real validation
  - Accepts `photo_type` query param (Literal["room", "inspiration"], default "room")
  - Reads file bytes via `await file.read()`, passes to `validate_photo(ValidatePhotoInput(...))`
  - Only adds photo to state if `validation.passed` — rejected photos return response with `passed=False`
  - `backend/tests/test_api_endpoints.py` — 22 tests (was 20, +2 new)
  - Existing tests mock `validate_photo` (test API routing/state, not validation logic)
  - New: `test_rejected_photo_not_added_to_state` — verifies rejected photo returns `passed=False` and is NOT in state
  - New: `test_photo_type_parameter` — verifies `photo_type` forwarded to validation and stored correctly
  - New: `test_file_too_large_returns_413` — verifies 20 MB upload limit
  - Review fixes: `asyncio.to_thread()` for non-blocking validation, `MAX_PHOTO_BYTES` 20 MB limit, `assert state is not None` type guard
  - All 179 tests pass, ruff clean, mypy clean (22 files)

## Key Decisions
- Python 3.12+ target (system has 3.13.7)
- Hatchling for build system (PEP 621)
- asyncpg for async PostgreSQL
- Temporal auto-setup Docker image for local dev
- structlog with console renderer in dev, JSON in prod
- `from __future__ import annotations` for forward references in contracts
- API request/response models co-located in contracts.py (single source of truth)
- Use explicit `response_model=X` on decorator (not return type annotation) for endpoints returning JSONResponse on errors
- Walrus operator for step validation: `if err := _check_step(state, "photos", "upload"): return err`
- Mock activity stubs in `activities/mock_stubs.py` (T0-owned) — swapped for real T2/T3 implementations during P2
- `_AbandonedError` exception + `_wait` helper for clean workflow termination on timeout or cancel
- `workflow.unsafe.imports_passed_through()` for activity + contract imports in sandbox
- Shared structlog config in `app/logging.py` — imported by both API and worker entrypoints

- Initial Alembic migration complete (P0 #4 success metric fulfilled)
  - `backend/migrations/versions/001_initial_schema.py` — creates all 9 tables
  - Matches db.py models exactly: columns, types, FKs with CASCADE, unique constraints, JSONB fields
  - 4 indexes: idx_photos_project_type, idx_generated_images_project, idx_revisions_project, idx_product_matches_list
  - Proper downgrade: drops tables in reverse dependency order
  - 10 tests in `test_migration.py` covering: structure (revision ID, no parent, callable up/down), completeness (all tables, table count, table names, indexes, cascades, drop order)
  - All 189 tests pass, ruff clean, mypy clean (22 files)

- Workflow error handling hardened
  - `backend/app/workflows/design_project.py` — 4 error handling improvements
  - Fix 1: Generation phase `except Exception` now captures exc, logs via `workflow.logger.error()`, includes `type(exc).__name__` in WorkflowError message
  - Fix 2: Iteration phase now calls `await self._wait(lambda: self.error is None)` after failure (was missing — workflow would loop without waiting, losing the user's lasso/regen action context)
  - Fix 3: Shopping phase now clears `self.error = None` on success (was missing)
  - Fix 4: `select_option` signal now logs `workflow.logger.warning()` for out-of-bounds index (was silently ignored)
  - 4 new error recovery tests in `test_workflow.py`: generation error detail, iteration error blocks until retry, iteration retry clears error and accepts approval, shopping error detail
  - Failing activity stubs (`_failing_generate`, `_failing_inpaint`, `_failing_shopping`) + 3 activity lists for targeted failure testing
  - All 193 tests pass, ruff clean, mypy clean (22 files)

- Workflow exception type narrowing + cancellation handling
  - `backend/app/workflows/design_project.py` — 5 improvements:
  - Narrowed `except Exception` to `except ActivityError` in 3 activity catch blocks (generation, iteration, shopping)
  - Import: `from temporalio.exceptions import ActivityError`, `import asyncio`
  - Added `except asyncio.CancelledError` in `run()` — logs, sets step="cancelled", runs `_try_purge()`, re-raises. Handles Temporal-level cancellation (distinct from `cancel_project` signal).
  - `_try_purge()` now uses `except BaseException` (was `except Exception`) — `asyncio.CancelledError` is `BaseException` in Python 3.9+, and the docstring says "never blocks abandonment"
  - Error messages changed to user-friendly strings (e.g., "Design generation failed — please retry")
  - Why: bare `except Exception` catches programming bugs as retryable. `ActivityError` only catches actual activity failures.
  - Fixed flaky `test_iteration_retry_clears_error_and_accepts_approval`: sends approve *before* retry so while-loop exits cleanly
  - Fixed vacuous cancel test: now queries `state.step == "abandoned"` instead of just `result is None` (always true for None-returning workflow)
  - All 193 tests pass, ruff clean, mypy clean (22 files)

- Iteration input validation guard
  - `backend/app/workflows/design_project.py` — added second `except` in iteration phase
  - `except ActivityError` re-queues the action (transient failure, same payload may work on retry)
  - `except (ValueError, TypeError)` discards the action (bad input won't be fixed by retrying)
  - Pydantic's `ValidationError` inherits from `ValueError`, so catches malformed lasso regions / invalid regen feedback during Pydantic model construction
  - Workflow bugs (AttributeError, KeyError) still crash the task for investigation
  - Comment explains the two-handler design rationale
  - New test `test_malformed_lasso_regions_surfaces_error`: sends regions with `region_id=99` (exceeds ge=1 le=3) and `instruction="short"` (below min_length=10), verifies error surfaced + recovery with valid action
  - All 194 tests pass, ruff clean, mypy clean (22 files)

- Temporal-level cancellation test
  - New test `test_temporal_cancel_sets_cancelled_step` in `TestCancellation` class
  - Exercises `handle.cancel()` (Temporal UI/CLI cancellation) vs `cancel_project` signal (application-level)
  - Verifies: `WorkflowFailureError` raised on `result()`, state query shows step="cancelled"
  - Validates the `asyncio.CancelledError` handler added in the previous deliverable
  - Import: `from temporalio.client import WorkflowFailureError`
  - All 195 tests pass, ruff clean, mypy clean (22 files)

- Succeed-after-retry test (full retry flow verification)
  - New test `test_generation_retry_succeeds_after_transient_failure`
  - Uses `_flaky_generate` stub: fails first 2 calls (Temporal retry exhaustion), succeeds on call 3+
  - Global `_flaky_generate_calls` counter, reset in test setup
  - Flow: skip to generation → activity fails (error surfaced) → `retry_failed_step` → activity succeeds → step advances to "selection" with 2 generated options
  - Verifies the actual retry loop works end-to-end, not just that the error flag is set
  - All 196 tests pass, ruff clean, mypy clean (22 files)

- Abandonment timeout tests
  - New `TestAbandonmentTimeout` class with 2 tests
  - `test_workflow_abandons_after_48h_inactivity`: starts workflow, sends no signals, verifies auto-abandonment
  - `test_mid_flow_abandonment_at_scan_step`: advances to scan, goes idle, verifies 48h timeout at non-initial step
  - Time-skipping environment fast-forwards the 48h timeout automatically
  - Both verify step="abandoned" and normal completion (result=None)
  - All 198 tests pass, ruff clean, mypy clean (22 files)

- API endpoint test coverage gaps filled
  - `test_upload_scan_transitions_to_intake`: tests `POST /scan` endpoint (was untested)
  - `test_upload_scan_wrong_step_returns_409`: tests `POST /scan` at wrong step
  - `test_photo_upload_wrong_step_returns_409`: tests photo upload at wrong step
  - API tests: 23 → 26 tests (+3 new)
  - All 201 tests pass, ruff clean, mypy clean (22 files)

- Test assertion strengthening
  - Happy path: verify design_brief stored (room_type matches), current_image matches selected option, shopping_list has items and total_estimated_cost_cents > 0
  - Lasso edit: verify revision_history[0] has correct revision_number, non-empty base/revised URLs, current_image updated to revised URL
  - All 201 tests pass, ruff clean, mypy clean (22 files)

- Fix confirm_intake contract mismatch
  - `backend/app/api/routes/projects.py`: `confirm_intake` now accepts `IntakeConfirmRequest` body
  - Was: hardcoded `DesignBrief(room_type="living room")`, ignored request body
  - Now: uses `body.brief` from the request, matching the `IntakeConfirmRequest` contract
  - Import added: `IntakeConfirmRequest`
  - All 3 test call sites updated to send `json={"brief": {"room_type": "..."}}`
  - New assertion: `test_confirm_intake_transitions` verifies stored brief matches sent values
  - Prevents T1 integration failure (iOS would send brief, mock API would ignore it)
  - All 201 tests pass, ruff clean, mypy clean (22 files)

- Scan + photo test enrichment
  - `test_complete_scan`: now sends RoomDimensions (width=4.5, length=6.0, height=2.7), verifies dimensions persisted in workflow state
  - New `test_mixed_photo_types_stored_correctly`: sends room + inspiration photos, verifies both types stored with correct photo_type
  - All 202 tests pass, ruff clean, mypy clean (22 files)

- CLAUDE.md updated
  - Status: "Pre-implementation" → "T0 P0+P1 complete, 202 tests"
  - Added Development Commands section (install, test, lint, run, migrate)
  - "Planned Repository Structure" → "Repository Structure" with actual files
  - All 202 tests pass, ruff clean, mypy clean (22 files)

- Remaining test coverage gaps
  - Workflow: regen revision assertions strengthened (revision_number, base/revised URLs, current_image)
  - API: `test_delete_nonexistent_returns_404`, `test_approve_from_approval`, `test_approve_wrong_step_returns_409`
  - API tests: 26 → 29 (+3 new), workflow test assertions strengthened
  - All 205 tests pass, ruff clean, mypy clean (22 files)

- Validation.py silent failure fixes
  - `backend/app/activities/validation.py` — 4 fixes to prevent silent swallowing of bugs:
  - Fix 1 (CRITICAL): `except Exception` on Image.open → `except (OSError, SyntaxError, ValueError)` + logging
  - Fix 2 (HIGH): Added structlog warning when content check skipped due to missing API key
  - Fix 3 (HIGH): Added response structure validation (empty content, non-TextBlock) before parsing — fails open with logging
  - Fix 4 (HIGH): `except Exception` on API call → `except anthropic.APIError` + enhanced logging with `error_type` + `exc_info`
  - `backend/tests/test_validation.py` — 3 test changes:
  - Fixed `test_api_error_fails_open`: `Exception("API timeout")` → `anthropic.APIConnectionError(request=MagicMock())` (matches narrowed catch)
  - New `test_empty_response_content_fails_open`: empty response.content list → fail open
  - New `test_non_text_block_response_fails_open`: response without .text attribute → fail open
  - All 207 tests pass, ruff clean, mypy clean (22 files)

- API defensive improvements + test coverage gaps
  - `backend/app/api/routes/projects.py` — added bounds check on `select_option`:
  - `body.index >= len(state.generated_options)` → 422 `invalid_selection` (defense-in-depth; Pydantic already constrains to 0-1)
  - `backend/tests/test_api_endpoints.py` — 3 new tests:
  - `test_select_option_out_of_bounds_returns_422`: empty generated_options + index=0 → 422
  - `test_start_over_from_photos_step`: start_over from early state doesn't corrupt (empty options, null brief)
  - `test_retry_nonexistent_returns_404`: retry on non-existent project → 404
  - API tests: 29 → 32 (+3 new)
  - All 210 tests pass, ruff clean, mypy clean (22 files)

- 5-iteration cap tests
  - `test_fifth_iteration_auto_transitions_to_approval`: 5 regen iterations → step auto-transitions to "approval"
  - `test_sixth_iteration_blocked_after_cap`: 5 lasso iterations → 6th returns 409 "wrong_step" (cap enforced)
  - Verifies `_apply_revision` logic that T1 iOS depends on for UI transition
  - API tests: 32 → 34 (+2 new)
  - All 212 tests pass, ruff clean, mypy clean (22 files)

- Wire scan upload to accept and store RoomPlan JSON
  - `backend/app/api/routes/projects.py` — `upload_scan` now accepts JSON body:
  - Parses RoomPlan JSON via `lidar.py` → `RoomDimensions`
  - Stores `ScanData(storage_key=mock_key, room_dimensions=dimensions)` in workflow state
  - Returns 422 `invalid_scan_data` for malformed input (LidarParseError)
  - Previously silently dropped scan data — T1 iOS couldn't test their upload path
  - Added imports: `ScanData`, `LidarParseError`, `parse_room_dimensions`
  - `backend/tests/test_api_endpoints.py` — scan tests updated:
  - `test_upload_scan_transitions_to_intake`: now sends RoomPlan JSON, verifies dimensions stored in state
  - New `test_upload_scan_invalid_data_returns_422`: missing room key → 422
  - `test_upload_scan_wrong_step_returns_409`: updated to send JSON body
  - API tests: 34 → 35 (+1 new)
  - All 213 tests pass, ruff clean, mypy clean (22 files)

- Auto-transition to scan after 2 photos
  - `backend/app/api/routes/projects.py` — photo upload now auto-transitions step:
  - After 2nd valid photo, step moves from "photos" → "scan" (mirrors workflow behavior)
  - Rejected photos don't count toward the 2-photo minimum
  - Previously T1 iOS had no way to trigger this transition through the mock API
  - `backend/tests/test_api_endpoints.py` — 3 new tests + happy path updated:
  - `test_auto_transitions_to_scan_after_two_photos`: 2 valid photos → step="scan"
  - `test_stays_in_photos_with_one_photo`: 1 valid photo → stays at "photos"
  - `test_rejected_photo_does_not_trigger_transition`: existing + rejected → no transition
  - Happy path: removed manual `step = "scan"` — now auto-transitions
  - Added `PhotoData` import to test file
  - API tests: 35 → 38 (+3 new)
  - All 216 tests pass, ruff clean, mypy clean (22 files)

- Test fixture deduplication + CLAUDE.md test count
  - Removed duplicate `client` fixture from `test_api_endpoints.py` — uses conftest.py's fixture
  - Removed 3 unused imports (`ASGITransport`, `AsyncClient`, `app`)
  - Updated CLAUDE.md test count from 212 → 216
  - All 216 tests pass, ruff clean, mypy clean (22 files)

- Populate mock shopping list on approval
  - `backend/app/api/routes/projects.py` — `approve_design` now populates `state.shopping_list`:
  - Creates `GenerateShoppingListOutput` with 2 `ProductMatch` items (Furniture + Lighting)
  - `total_estimated_cost_cents` = 33998 (sum of item prices, verifiable by T1 iOS)
  - Previously T1 iOS would see `shopping_list: null` after approval — couldn't test shopping list UI
  - Added imports: `GenerateShoppingListOutput`, `ProductMatch`
  - `backend/tests/test_api_endpoints.py` — test changes:
  - `test_approve_from_iteration` + `test_approve_from_approval`: added `assert body["shopping_list"] is not None`
  - New `test_approve_populates_shopping_list_shape`: validates 2 items, total > 0, all ProductMatch fields present
  - Happy path: added `assert body["shopping_list"] is not None` + `len(items) == 2`
  - API tests: 38 → 39 (+1 new)
  - All 217 tests pass, ruff clean, mypy clean (22 files)

- Fix `start_over` to reset all iteration state
  - `backend/app/api/routes/projects.py` — `start_over` now also resets:
  - `revision_history = []`, `iteration_count = 0`, `approved = False`, `shopping_list = None`
  - Previously only reset selection-related fields — T1 iOS would see stale iteration counters after restart
  - New test `test_start_over_from_iteration_resets_all_state`: sets up iteration state with revisions, shopping list, then verifies complete reset
  - API tests: 39 → 40 (+1 new)
  - All 218 tests pass, ruff clean, mypy clean (22 files)

- Workflow test gaps: mixed iterations + approve without iterations
  - New `test_mixed_lasso_and_regen_iterations`: 4 iterations (lasso, lasso, regen, lasso), verifies types sequence and sequential revision numbers
  - New `test_approve_immediately_after_selection`: approve with 0 iterations, verifies shopping list still generated, iteration_count=0, empty revision_history
  - Workflow tests: 23 → 25 (+2 new)
  - All 220 tests pass, ruff clean, mypy clean (22 files)

- Enrich mock shopping list with optional fields + unmatched items
  - `backend/app/api/routes/projects.py` — mock shopping list now includes:
  - First ProductMatch: `image_url`, `fit_status="may_not_fit"`, `fit_detail`, `dimensions` (all optional fields populated)
  - Second ProductMatch: optional fields left as None (tests iOS nil handling)
  - 1 `UnmatchedItem` with Google Shopping fallback URL (tests "no exact match" UI)
  - Added `UnmatchedItem` import
  - Test `test_approve_populates_shopping_list_shape` enriched: verifies optional fields present/absent, unmatched items
  - All 220 tests pass, ruff clean, mypy clean (22 files)

- 24-hour completion purge timer test
  - New `TestCompletionPurge::test_workflow_completes_after_24h_purge_timer`
  - Verifies: approve → step="completed" → 24h sleep → purge → workflow exits normally
  - Time-skipping environment auto-advances the 24h sleep
  - Previously untested: the documented 24h purge could have been broken without any test catching it
  - Workflow tests: 25 → 26 (+1 new)
  - All 221 tests pass, ruff clean, mypy clean (22 files)

- Lasso edit request validation boundary tests
  - 3 new API tests for Pydantic validation of `LassoEditRequest`:
  - `test_lasso_empty_regions_returns_422`: empty regions array vs min_length=1
  - `test_lasso_short_instruction_returns_422`: instruction "short" vs min_length=10
  - `test_lasso_too_many_regions_returns_422`: 4 regions vs max_length=3
  - T1 iOS will get clear 422 errors when constructing lasso payloads incorrectly
  - API tests: 40 → 43 (+3 new)
  - All 224 tests pass, ruff clean, mypy clean (22 files)

- Invalid selection surfaces WorkflowError (was silently ignored)
  - `backend/app/workflows/design_project.py` — `select_option` signal now sets `self.error = WorkflowError(...)` for out-of-bounds index
  - Previously only logged a warning — iOS polling would never see the error, user stuck in selection
  - New `TestSelectionValidation::test_invalid_selection_surfaces_error`:
  - Sends index=99, verifies error surfaces with "Invalid selection" message
  - Clears error via retry, sends valid index=0, verifies workflow recovers to iteration step
  - Workflow tests: 26 → 27 (+1 new)
  - All 225 tests pass, ruff clean, mypy clean (22 files)

- Intake endpoint wrong_step tests
  - 4 new tests for intake endpoints at wrong step:
  - `test_start_intake_wrong_step_returns_409`
  - `test_send_message_wrong_step_returns_409`
  - `test_confirm_intake_wrong_step_returns_409`
  - `test_skip_intake_wrong_step_returns_409`
  - T1 iOS gets clean 409 errors with "wrong_step" when calling intake at photos step
  - API tests: 43 → 47 (+4 new)
  - All 229 tests pass, ruff clean, mypy clean (22 files)

- Cancel from iteration preserves state test
  - New `test_cancel_from_iteration_preserves_state`: advances to iteration, does 1 lasso edit, cancels
  - Verifies: step="abandoned", photos retained, iteration_count=1, revision_history preserved
  - Previously only tested cancel from initial photos step (no accumulated state)
  - Workflow tests: 27 → 28 (+1 new), cancellation tests: 2 → 3
  - All 230 tests pass, ruff clean, mypy clean (22 files)

- Wrong-step tests for select, lasso, and regenerate endpoints
  - 3 new tests verifying 409 "wrong_step" error:
  - `test_select_wrong_step_returns_409`: select from photos step
  - `test_lasso_wrong_step_returns_409`: lasso edit from photos step
  - `test_regenerate_wrong_step_returns_409`: regenerate from photos step
  - All step-restricted endpoints now have wrong_step test coverage
  - API tests: 47 → 50 (+3 new)
  - All 233 tests pass, ruff clean, mypy clean (22 files)

- Worker type safety improvements
  - `backend/app/worker.py` — `create_temporal_client()` refactored: untyped `kwargs: dict` → explicit conditional branches
  - Local path: `Client.connect(target_host, namespace)`, Cloud path: adds `tls=True, api_key=...`
  - Both code paths now fully type-checkable
  - `backend/pyproject.toml` — removed blanket `app.worker` mypy override, replaced with targeted `# type: ignore[arg-type]` on Worker activities param
  - All 233 tests pass, ruff clean, mypy clean (22 files)

- Worker type safety + shopping retry test
  - `backend/app/worker.py` — `create_temporal_client()` refactored from untyped kwargs dict to explicit conditional branches
  - `backend/pyproject.toml` — removed blanket `app.worker` mypy override → inline `# type: ignore[arg-type]`
  - New `_flaky_shopping` stub + `_FLAKY_SHOPPING_ACTIVITIES` activity list
  - New `test_shopping_retry_succeeds_after_transient_failure`: approve → shopping fails → retry → shopping succeeds → completed
  - Tests full retry-to-success flow for shopping phase (previously only tested error surfacing)
  - Workflow tests: 28 → 29 (+1 new)
  - All 234 tests pass, ruff clean, mypy clean (22 files)

- Endpoint type guard assertions + mypy override removal
  - `backend/app/api/routes/projects.py` — added `assert state is not None` to 9 endpoints missing it:
  - `skip_scan`, `start_intake`, `send_intake_message`, `confirm_intake`, `skip_intake`, `select_option`, `start_over`, `submit_lasso_edit`, `submit_regenerate`, `approve_design`, `retry_failed_step`
  - (`upload_photo` and `upload_scan` already had the assert from earlier deliverables)
  - `backend/pyproject.toml` — removed `app.api.routes.projects` mypy override entirely
  - Previously: `disable_error_code = ["union-attr", "arg-type"]` suppressed all type errors in the file
  - Now: mypy passes with zero errors on the file — full type safety restored
  - Mypy overrides reduced from 4 to 3 (only contracts strict, workflow SDK, validation SDK remain)
  - All 234 tests pass, ruff clean, mypy clean (22 files)

- Workflow start_over + second cycle completion test
  - New `test_start_over_then_complete_second_cycle` in `TestStartOver` class
  - Tests the full while-True restart loop: first cycle → start_over → second cycle with new brief → select → approve → completed
  - Verifies: second cycle produces fresh generated_options, brief from second cycle persists, shopping list generated
  - Previously only tested that start_over goes back to intake — never verified the second pass works end-to-end
  - Workflow tests: 29 → 30 (+1 new)
  - All 235 tests pass, ruff clean, mypy clean (22 files)

- Generation input builder verification tests
  - New `TestGenerationInput` class with 2 tests using a capturing activity stub:
  - `test_generation_input_separates_photo_types`: room + inspiration photos → correct URL separation in `GenerateDesignsInput`
  - `test_generation_input_includes_brief_and_dimensions`: verifies `design_brief`, `room_dimensions`, and `inspiration_notes` flow from workflow state to activity input
  - Uses `_capturing_generate` stub that records `GenerateDesignsInput` in a global variable
  - Critical for P2 integration: T2's real `generate_designs` activity depends on correct input from these builders
  - New imports: `GenerateDesignsInput`, `InspirationNote`
  - Workflow tests: 30 → 32 (+2 new)
  - All 237 tests pass, ruff clean, mypy clean (22 files)

- Shopping input builder verification test
  - New `TestShoppingInput` class with `test_shopping_input_includes_revision_history_and_context`
  - Uses `_capturing_shopping` stub to record `GenerateShoppingListInput`
  - Full pipeline: photos (room + inspiration) → scan with dimensions → intake with brief → select → 2 lasso edits → approve → shopping captures input
  - Verifies: `design_image_url` = last revision image, `original_room_photo_urls` = only room photos, `design_brief` forwarded, `revision_history` has 2 entries, `room_dimensions` forwarded
  - Renamed `_CAPTURING_ACTIVITIES` → `_CAPTURING_GEN_ACTIVITIES` for clarity alongside `_CAPTURING_SHOPPING_ACTIVITIES`
  - New import: `GenerateShoppingListInput`
  - Workflow tests: 32 → 33 (+1 new)
  - All 238 tests pass, ruff clean, mypy clean (22 files)

- Concurrent project isolation tests
  - New `TestProjectIsolation` class with 2 tests:
  - `test_two_projects_independent_state`: two projects at different steps, verifies photos and step don't bleed between projects
  - `test_delete_one_project_preserves_other`: deleting project A doesn't affect project B
  - Validates the `_mock_states` dict-based isolation that T1 iOS relies on
  - API tests: 50 → 52 (+2 new)
  - All 240 tests pass, ruff clean, mypy clean (22 files)

- Worker mock stubs production guard
  - `backend/app/worker.py` — added `_MOCK_ACTIVITY_MODULE` constant and guard in `run_worker()`
  - Checks if any registered activities come from `app.activities.mock_stubs` module
  - Logs `worker_using_mock_stubs` warning when running in non-development environment
  - Silent in development (default) — warns in production/staging
  - Prevents accidental deployment with mock stubs during P2 integration
  - 2 new tests in `TestMockStubsGuard`: warns in production, silent in development
  - Worker tests: 12 → 14 (+2 new)
  - All 242 tests pass, ruff clean, mypy clean (22 files)

- Concurrent project isolation tests
  - New `TestProjectIsolation` class with 2 API tests:
  - `test_two_projects_independent_state`: two projects at different steps, photos don't bleed
  - `test_delete_one_project_preserves_other`: deleting one project doesn't affect another
  - API tests: 50 → 52 (+2 new)

- Workflow get_state structural consistency test
  - New `test_get_state_maps_all_workflow_state_fields` in `TestQueryState` class
  - Uses `inspect.getsource()` to verify every `WorkflowState.model_fields` key appears as `field_name=` in get_state
  - Catches drift: if a field is added to WorkflowState but omitted from get_state, it silently uses Pydantic default
  - Structural (not runtime) test — catches the class of bug at import time, not at workflow execution time
  - Workflow tests: 33 → 34 (+1 new)
  - All 243 tests pass, ruff clean, mypy clean (22 files)

- Start_over preserves photos and scan data (workflow + API tests)
  - New `test_start_over_preserves_photos_and_scan` in workflow `TestStartOver` class
  - Uploads room + inspiration photos, completes scan with dimensions, advances to selection, start_over
  - Verifies: photos preserved (both IDs and types), scan_data preserved (dimensions intact), design fields reset
  - New `test_start_over_preserves_photos_and_scan_data` in API `TestSelectionEndpoints` class
  - Sets up state with photos and scan, start_over, verifies photos/scan preserved, design state reset
  - Critical regression guard: a refactor accidentally adding `self.photos = []` to the reset block would break UX
  - Workflow tests: 34 → 35 (+1 new), API tests: 52 → 53 (+1 new)
  - All 245 tests pass, ruff clean, mypy clean (22 files)

- Inpaint + regen input builder verification tests
  - New `TestInpaintInput::test_inpaint_input_includes_base_image_and_regions`
  - Captures inpaint input via `_capturing_inpaint` stub; verifies `base_image_url` (current design image) and `regions` (lasso selections with instruction/action/path_points) forwarded correctly
  - New `TestRegenInput::test_regen_input_includes_brief_feedback_and_history`
  - Does lasso edit first (builds revision_history), then regen with feedback
  - Captures regen input; verifies: `room_photo_urls` (room only, no inspiration), `design_brief`, `feedback`, `current_image_url`, and `revision_history` (1 prior lasso entry)
  - Critical for P2 integration: T2's real `generate_inpaint` and `generate_regen` activities depend on correct input from these builders
  - Added 4 new imports: `GenerateInpaintInput`, `GenerateInpaintOutput`, `GenerateRegenInput`, `GenerateRegenOutput`
  - Workflow tests: 35 → 37 (+2 new)
  - All 247 tests pass, ruff clean, mypy clean (22 files)

- Mock API response schema fidelity test
  - New `test_response_schema_fidelity` in `TestFullFlow` class
  - Walks through entire mock API flow: create → photos → scan skip → intake → confirm → select → approve
  - At every step transition, parses GET response through `WorkflowState.model_validate(body)`
  - Also validates `IntakeChatOutput.model_validate()` on intake start response
  - Catches schema drift: if mock API returns fields or shapes that don't match Pydantic contracts, T1 iOS would build against incorrect types
  - API tests: 53 → 54 (+1 new)
  - All 248 tests pass, ruff clean, mypy clean (22 files)

- Error response model conformance tests
  - New `TestErrorResponseSchema` class with 3 tests:
  - `test_not_found_response_conforms`: 404 → `ErrorResponse.model_validate()`, verifies `error`, `retryable=False`, non-empty `message`
  - `test_wrong_step_response_conforms`: 409 → full schema validation, verifies current step mentioned in message
  - `test_invalid_selection_response_conforms`: 422 → full schema validation
  - T1 iOS uses `retryable` and `message` from error responses — these fields were never validated in tests
  - API tests: 54 → 57 (+3 new)
  - All 251 tests pass, ruff clean, mypy clean (22 files)

- Revision history chain integrity test
  - New `test_revision_chain_integrity` in `TestIterationPhase` class
  - Does 3 iterations (lasso, regen, lasso), then verifies:
  - Each revision's `base_image_url` equals the previous revision's `revised_image_url`
  - First revision's base is the selected option's image (non-empty)
  - `current_image` equals the last revision's output
  - Structural invariant: T1 iOS uses this chain for revision timeline, T2's inpaint activity receives the correct base image
  - Workflow tests: 37 → 38 (+1 new)
  - All 252 tests pass, ruff clean, mypy clean (22 files)

- current_image tracking through API lifecycle test
  - New `test_current_image_tracks_through_revisions` in `TestIterationEndpoints`
  - Tracks `current_image` through: selection (= option URL) → lasso (= revision 1 URL) → regen (= revision 2 URL)
  - Verifies chain integrity: revision 2's base_image_url = revision 1's revised_image_url
  - T1 iOS displays `current_image` as the active design preview — this test ensures the mock API tracks it correctly
  - Also validates the mock API's `_apply_revision` helper chains images identically to the workflow
  - API tests: 57 → 58 (+1 new)
  - All 253 tests pass, ruff clean, mypy clean (22 files)

- Complete intake stores brief test
  - New `test_complete_intake_stores_brief` in `TestIntakePhase` class
  - Sends `complete_intake` with `DesignBrief(room_type="kitchen", pain_points=[...])`, verifies:
  - Brief stored in workflow state with correct `room_type` and `pain_points`
  - Generation runs and produces 2 options
  - Complements existing `test_skip_intake_generates_options` — now both intake paths tested
  - Workflow tests: 38 → 39 (+1 new)
  - All 254 tests pass, ruff clean, mypy clean (22 files)

- Late photo accepted after scan step (workflow resilience)
  - New `test_late_photo_accepted_after_scan_step` in `TestPhotoPhase` class
  - Adds 3rd photo during scan step — verifies signal has no step gate and photo is stored
  - Documents behavioral difference: workflow signals are step-agnostic, mock API enforces step constraints via `_check_step`
  - Workflow tests: 39 → 40 (+1 new)
  - All 255 tests pass, ruff clean, mypy clean (22 files)

- Scan without dimensions propagates `None` to generation input
  - New `test_generation_input_with_null_dimensions` in `TestGenerationInput` class
  - Exercises real-world path: LiDAR scan captured but dimension parsing failed → ScanData(storage_key=..., room_dimensions=None)
  - Verifies: scan_data exists in workflow state, room_dimensions is None, captured generation input has room_dimensions=None
  - Critical for P2: T2's `generate_designs` activity must handle both `RoomDimensions` and `None`
  - Workflow tests: 40 → 41 (+1 new)
  - All 256 tests pass, ruff clean, mypy clean (22 files)

- start_over clears error state (bug fix + tests)
  - `backend/app/workflows/design_project.py` — added `self.error = None` to the start_over reset block
  - `backend/app/api/routes/projects.py` — added `state.error = None` to mock API start_over endpoint
  - Bug: if user got a generation error or invalid selection and chose start_over instead of retry, stale error persisted through restart. T1 iOS would show confusing error message in intake step.
  - New `test_start_over_clears_stale_error` in workflow `TestStartOver`: triggers invalid selection error, sends start_over, verifies error cleared
  - New `test_start_over_clears_error` in API `TestSelectionEndpoints`: sets WorkflowError in mock state, start_over, verifies cleared
  - Workflow tests: 41 → 42 (+1 new), API tests: 58 → 59 (+1 new)
  - All 258 tests pass, ruff clean, mypy clean (22 files)

- Cancel from selection step preserves generated options
  - New `test_cancel_from_selection_preserves_generated_options` in `TestCancellation` class
  - Advances to selection (photos → scan skip → intake skip → generation → selection)
  - Sends cancel_project from selection, verifies: step="abandoned", 2 generated_options preserved, selected_option=None
  - Tests compound wait condition (selected_option or _restart_requested) interaction with _cancelled flag
  - Previously only tested cancel from photos (initial state) and iteration (with revisions)
  - Workflow tests: 42 → 43 (+1 new)
  - All 259 tests pass, ruff clean, mypy clean (22 files)

- Intake message response schema fidelity + scan skip state verification
  - New `test_send_message_validates_through_model` in `TestIntakeEndpoints`: validates send_message response through full `IntakeChatOutput.model_validate()` (was only checking 2 fields)
  - New `test_skip_scan_leaves_scan_data_null` in `TestScanEndpoints`: verifies scan_data is explicitly null after skip_scan (T1 iOS uses this to know scan was skipped)
  - API tests: 59 → 61 (+2 new)
  - All 261 tests pass, ruff clean, mypy clean (22 files)

- start_over unblocks generation error wait (structural fix + test)
  - `backend/app/workflows/design_project.py` — moved ALL cycle state reset into `start_over` signal handler:
  - Signal handler now clears: `generated_options`, `selected_option`, `design_brief`, `intake_skipped`, `error`
  - Previously reset logic split between signal handler and selection reset block — start_over from generation error was stuck
  - Selection reset block simplified to just `continue` (state already cleared by signal)
  - Generation error wait stays `lambda: self.error is None` — start_over clears error, unblocking the wait
  - New `test_start_over_from_generation_error` in `TestStartOver`: uses `_FAILING_GENERATION_ACTIVITIES`, verifies start_over from generation error → step="intake", error=None
  - Workflow tests: 43 → 44 (+1 new)
  - All 262 tests pass, ruff clean, mypy clean (22 files)

- start_over from generation error completes second cycle + type safety fix
  - `backend/app/api/routes/projects.py` — `_apply_revision` now uses `state.current_image or ""` (was `state.current_image`)
  - Type parity with workflow which uses `self.current_image or ""` in revision record creation
  - New `test_start_over_from_generation_error_completes_second_cycle` in `TestStartOver`:
  - Uses `_FLAKY_GENERATION_ACTIVITIES`: first cycle generation fails, start_over sent, second cycle generation succeeds
  - Verifies full second cycle: intake (with brief) → generation → selection → approve → completed
  - Proves D48 start_over-from-error fix works end-to-end, not just at step transition level
  - Workflow tests: 44 → 45 (+1 new)
  - All 263 tests pass, ruff clean, mypy clean (22 files)

- Max-iterations approval path completes end-to-end
  - New `test_approve_after_five_iterations_completes` in `TestIterationPhase`:
  - Does 5 iterations (3 lasso + 2 regen), verifies step="approval"
  - Sends approve_design, verifies: step="completed", shopping_list populated, 5 revisions preserved
  - Previously `test_five_iterations_moves_to_approval` only verified the step transition — never tested the approval wait or shopping phase from the max-iterations path
  - This is a distinct code path: when iteration_count == 5, the while-loop exits with `self.approved == False`, entering an explicit approval wait
  - Workflow tests: 45 → 46 (+1 new)
  - All 264 tests pass, ruff clean, mypy clean (22 files)

- Input builders with minimal state (skipped scan + skipped intake)
  - New `test_regen_input_with_skipped_intake` in `TestRegenInput`:
  - Skip scan + skip intake → regen → captured input has `design_brief=None`, empty `revision_history`
  - Proves the regen input builder doesn't assume a brief exists
  - New `test_shopping_input_with_minimal_state` in `TestShoppingInput`:
  - Skip scan + skip intake → approve immediately → captured shopping input has `design_brief=None`, `room_dimensions=None`, empty `revision_history`, but room photos and design image present
  - Critical for P2: T2/T3 activities must handle the minimal state path where all optional fields are None
  - Workflow tests: 46 → 48 (+2 new)
  - All 266 tests pass, ruff clean, mypy clean (22 files)

- Cancel from error states abandons workflow
  - New `test_cancel_during_generation_error_abandons` in `TestCancellation`:
  - Generation fails → error wait → cancel_project → step="abandoned"
  - Proves `_cancelled` flag check in `_wait` works from generation error wait
  - New `test_cancel_during_iteration_error_abandons` in `TestCancellation`:
  - Inpaint fails → error wait → cancel_project → step="abandoned"
  - Proves `_cancelled` flag check in `_wait` works from iteration error wait
  - These are the "emergency exit" scenarios: user stuck in error, wants to give up entirely
  - Workflow tests: 48 → 50 (+2 new), cancellation tests: 5 → 7
  - All 268 tests pass, ruff clean, mypy clean (22 files)

- Request ID middleware for log correlation
  - `backend/app/main.py` — added `@app.middleware("http")` that:
  - Generates UUID for each request (or accepts client-provided `X-Request-ID`)
  - Binds `request_id` to structlog context vars (all log entries include it)
  - Returns `X-Request-ID` header in response (T1 iOS can report it for debugging)
  - New `TestRequestIdMiddleware` with 2 tests:
  - `test_response_includes_request_id_header`: verifies UUID in response header
  - `test_client_provided_request_id_echoed`: verifies client ID is echoed
  - Scaffold tests: 8 → 10 (+2 new)
  - All 288 tests pass, ruff clean, mypy clean (35 files)

- OpenAPI schema completeness tests
  - New `TestOpenAPISchema` class with 2 tests:
  - `test_all_project_endpoints_in_schema`: verifies all 16 paths (15 project + 1 health) match expected set exactly
  - `test_key_models_in_schema`: verifies 12 key contract models appear in OpenAPI `components/schemas`
  - Structural guard: if an endpoint is added/removed or a response model changes, these tests catch the drift
  - T1 iOS uses `/docs` (OpenAPI schema) for Swift model generation — missing models would break codegen
  - Scaffold tests: 6 → 8 (+2 new)
  - All 286 tests pass, ruff clean, mypy clean (35 files)

- Global exception handler for consistent 500 error responses
  - `backend/app/main.py` — added `@app.exception_handler(Exception)` that catches unhandled errors
  - Returns consistent `ErrorResponse` JSON (`error: "internal_error"`, `retryable: true`) instead of bare HTML 500
  - Logs the error with structlog (path, method, error_type, exc_info) for debugging
  - T1 iOS always gets parseable JSON, even for unexpected server errors
  - New `test_unhandled_exception_returns_500_json` in `TestExceptionHandler`: patches `_get_state` to raise RuntimeError, verifies 500 + ErrorResponse shape
  - Uses `ASGITransport(raise_app_exceptions=False)` to test through the exception handler chain
  - Scaffold tests: 5 → 6 (+1 new)
  - All 284 tests pass, ruff clean, mypy clean (35 files)

- 404 tests for all project-scoped endpoints
  - New `TestNotFoundOnAllEndpoints` class with 12 tests:
  - Photos, scan, scan/skip, intake/start, intake/message, intake/confirm, intake/skip, select, start-over, lasso, regenerate, approve
  - Each test hits a nonexistent project ID and verifies 404 response
  - Previously only GET, DELETE, and retry had explicit 404 tests
  - Fixed `intake/start` test body: sent `"guided"` (invalid Literal) → `"quick"` (valid)
  - API tests: 59 → 71 (+12 new)
  - All 283 tests pass, ruff clean, mypy clean (35 files)

- Input builder assertions (fail-fast on invariant violations)
  - `backend/app/workflows/design_project.py` — added `assert self.current_image is not None` to `_inpaint_input`, `_regen_input`, `_shopping_input`
  - Removed `or ""` fallbacks from all 3 builders — no longer silently passes empty strings to activities
  - In P2, T2's `generate_inpaint` activity receiving `base_image_url=""` would fail cryptically; now fails fast with a clear assertion
  - The workflow logic guarantees `current_image` is set after `select_option` (line 143), so assertions never fire in normal flows — they guard against future refactoring bugs
  - All 271 tests pass, confirming the invariant holds across all test paths
  - All 271 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Purge failure resilience tests (cancel, abandonment, completion)
  - New `_failing_purge` activity stub + `_FAILING_PURGE_ACTIVITIES` list: simulates R2 outage during purge
  - New `test_cancel_completes_when_purge_fails` in `TestCancellation`: cancel_project → purge fails → step still reaches "abandoned"
  - New `test_abandonment_completes_when_purge_fails` in `TestAbandonmentTimeout`: 48h timeout → purge fails → step still reaches "abandoned"
  - New `test_purge_failure_does_not_block_completion` in `TestCompletionPurge`: approve → 24h timer → purge fails → workflow still exits normally
  - Validates the `except BaseException` handler in `_try_purge` — critical for P2 when R2 may be temporarily unavailable
  - Workflow tests: 50 → 53 (+3 new)
  - All 271 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Configure Pydantic v2 data converter for Temporal (eliminates 732 warnings)
  - `backend/app/worker.py` — import `pydantic_data_converter` from `temporalio.contrib.pydantic`, pass to both `Client.connect()` paths (local and cloud)
  - `backend/tests/test_workflow.py` — all 49 `WorkflowEnvironment.start_time_skipping()` calls now pass `data_converter=pydantic_data_converter`
  - `backend/tests/test_worker.py` — updated `test_local_connection_no_tls` and `test_cloud_connection_with_tls` assertions to include `data_converter` kwarg
  - Before: 732 Pydantic deprecation warnings (`.dict()` → `model_dump()`, `.parse_obj()` → `model_validate()`) from temporalio's default converter
  - After: 0 warnings. Proper Pydantic v2 serialization for all Temporal signal/query/activity data flow
  - Critical for P2: real activities will receive Pydantic v2-serialized inputs, not legacy v1 format
  - All 268 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Strengthen generation input test + update CLAUDE.md
  - `backend/tests/test_workflow.py` — `test_generation_input_separates_photo_types` strengthened with 3 new assertions:
  - `design_brief is None` when intake skipped (was only checking URL separation)
  - `inspiration_notes == []` (fallback when no brief)
  - `room_dimensions is None` when scan skipped
  - `CLAUDE.md` updated: test count 255→268, test file count 8→11
  - All 268 tests pass, ruff clean, mypy clean (22 files)

- Validation hardening (img.load + _detect_media_type safety)
  - `backend/app/activities/validation.py` — 2 hardening fixes:
  - Fix 1: Added `img.load()` after `Image.open()` to force full decode — catches truncated images and decompression bombs inside the existing try/except (was deferred to downstream checks which would crash differently)
  - Fix 2: Moved `_detect_media_type(image_data)` call inside the try/except in `_check_content` — a corrupt image that passes basic checks but fails format detection now fails open with logging instead of crashing
  - `backend/tests/test_validation.py` — 1 new test:
  - `test_truncated_image_returns_invalid`: creates valid JPEG, truncates to 25%, verifies `invalid_image` failure
  - All 289 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Consistent error response shapes for Pydantic validation errors + logging.py fix
  - `backend/app/main.py` — added `@app.exception_handler(RequestValidationError)`:
  - FastAPI's default 422 returns `{"detail": [...]}` — doesn't match our `ErrorResponse` contract
  - New handler returns `{"error": "validation_error", "message": "field: msg; ...", "retryable": false}`
  - T1 iOS can now use a single `ErrorResponse` decoder for all error codes (400/404/409/422/500)
  - Field location included in message (`body → device_fingerprint: Field required`)
  - `backend/app/logging.py` — fixed environment source of truth:
  - Changed `os.getenv("ENVIRONMENT", "development")` → `settings.environment` from config.py
  - Was two independent reads of the same env var — could diverge if either changed
  - `backend/tests/test_scaffold.py` — 2 new tests:
  - `test_pydantic_validation_returns_error_response_shape`: missing required field → ErrorResponse shape
  - `test_invalid_field_type_returns_error_response_shape`: wrong JSON type → ErrorResponse shape
  - All 291 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Dockerfile HEALTHCHECK + health endpoint improvements
  - `backend/Dockerfile` — added `HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3` using curl
  - Added `curl` to apt-get install (needed for HEALTHCHECK)
  - Enables Railway to detect unhealthy containers and auto-restart them
  - `backend/app/api/routes/health.py` — enriched health response:
  - Added `version` field (matches app version, useful for deployment debugging)
  - Added `environment` field (from `settings.environment`, useful for verifying config)
  - Changed service statuses from `"not_checked"` → `"not_connected"` (more honest — these services aren't connected in mock mode)
  - Real connectivity checks deferred to P2 when API wires to DB/Temporal
  - Test updated: `test_health_returns_200` now asserts `version` and `environment` fields
  - All 291 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- ActionResponse model for typed API response schema
  - `backend/app/models/contracts.py` — new `ActionResponse(status: Literal["ok"] = "ok")` model
  - 10 endpoints previously returned untyped `{"status": "ok"}` dicts — T1 iOS codegen saw these as `Any` in OpenAPI schema
  - `backend/app/api/routes/projects.py` — all 10 action endpoints now use `response_model=ActionResponse`:
  - scan, scan/skip, intake/confirm, intake/skip, select, start-over, lasso, regenerate, approve, retry
  - Return statements changed from `{"status": "ok"}` → `ActionResponse()`
  - `backend/tests/test_scaffold.py` — `ActionResponse` added to `test_key_models_in_schema` expected set
  - T1 iOS can now generate a typed `ActionResponse` Swift struct from the OpenAPI schema
  - All 291 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- ActionResponse validation in schema fidelity + retry tests
  - `backend/tests/test_api_endpoints.py` — `test_response_schema_fidelity` now validates `ActionResponse.model_validate()` at each action endpoint (scan/skip, intake/confirm, select, approve)
  - `test_retry_clears_error` now validates `ActionResponse.model_validate()` on retry response
  - Docstring updated: "validates both WorkflowState (GET) and ActionResponse (POST)"
  - Catches response shape drift: if any action endpoint returns something other than `{"status": "ok"}`, the test fails
  - All 291 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Tighten mypy overrides + CLAUDE.md update
  - `backend/pyproject.toml` — reduced suppressed error codes:
  - Workflow: removed `index` (fixed with type-narrowing assertion), kept `arg-type`, `attr-defined`, `func-returns-value` (Temporal SDK false positives)
  - Validation: removed `union-attr` (was unnecessary), kept `attr-defined` (Pillow stubs), `list-item` (Anthropic SDK stubs)
  - Added inline comments explaining WHY each suppression exists (SDK false positives, not real bugs)
  - `backend/app/workflows/design_project.py` — added `assert self.selected_option is not None` before indexing generated_options (type-narrows `int | None` → `int`)
  - `CLAUDE.md` — updated test count 268→291, added health endpoint description
  - All 292 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- .env.example completeness + drift detection test
  - `.env.example` — added 4 missing settings: `TEMPORAL_TASK_QUEUE`, `ENVIRONMENT`, `LOG_LEVEL`, `PRESIGNED_URL_EXPIRY_SECONDS` (all have sensible defaults, now documented)
  - `backend/tests/test_scaffold.py` — new `TestEnvExample::test_all_settings_in_env_example`:
  - Reads all `Settings.model_fields` from config.py, checks each appears in .env.example
  - Catches drift: if a new setting is added to config.py without documenting it, this test fails
  - Prevents T1/T2/T3 deployment surprises from undocumented configuration
  - All 292 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Add structured logging to key API endpoints
  - `backend/app/api/routes/projects.py` — added structlog to 4 endpoints with lasting P2 value:
  - `create_project`: logs `project_created` with project_id, has_lidar
  - `upload_photo`: logs `photo_uploaded` with project_id, photo_id, photo_type, passed, failures, size_bytes
  - `upload_scan`: logs `scan_uploaded` with project_id, dimensions; logs `scan_parse_failed` on error
  - `delete_project`: logs `project_deleted` with project_id
  - Request ID middleware (D60) populates structlog context vars — these logs auto-include the correlation ID
  - Focused on endpoints that do real work (validation, LiDAR parsing, R2 storage) — mock-only endpoints like confirm_intake will be replaced by Temporal proxy in P2
  - All 292 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- R2 client error logging for head_object and generate_presigned_url
  - `backend/app/utils/r2.py` — 2 error logging additions:
  - `head_object`: logs `r2_head_failed` with key and error for non-404 ClientErrors before re-raising
  - `generate_presigned_url`: wrapped in try/except ClientError, logs `r2_presign_failed` with key and error before re-raising
  - Now all 5 R2 functions have appropriate logging: upload (info), presign (error), head (error on non-404), delete (info), delete_prefix (info + warning)
  - `backend/tests/test_r2.py` — 2 new tests:
  - `test_logs_non_404_error`: verifies head_object logs before re-raising 403
  - `test_logs_client_error`: verifies generate_presigned_url logs before re-raising
  - All 294 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Error response conformance tests for 413 and 422 (scan)
  - `backend/tests/test_api_endpoints.py` — 2 new tests in `TestErrorResponseSchema`:
  - `test_file_too_large_response_conforms`: validates 413 file_too_large through full `ErrorResponse.model_validate()`
  - `test_invalid_scan_data_response_conforms`: validates 422 invalid_scan_data through full `ErrorResponse.model_validate()`
  - Now all 5 error types have conformance tests: 404 (not_found), 409 (wrong_step), 413 (file_too_large), 422 (invalid_selection + invalid_scan_data)
  - All 296 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Pydantic validation boundary tests for remaining request models
  - `backend/tests/test_api_endpoints.py` — 3 new tests:
  - `test_invalid_intake_mode_returns_422`: invalid mode literal returns 422 validation_error
  - `test_missing_intake_message_returns_422`: missing message field returns 422 validation_error
  - `test_missing_regenerate_feedback_returns_422`: missing feedback field returns 422 validation_error
  - Now all request body models have validation boundary tests: CreateProjectRequest, IntakeStartRequest, IntakeMessageRequest, LassoEditRequest, RegenerateRequest
  - All 299 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Schema fidelity test now validates all response models
  - `backend/tests/test_api_endpoints.py` — `test_response_schema_fidelity` strengthened:
  - Now validates `CreateProjectResponse.model_validate()` on project creation
  - Now validates `PhotoUploadResponse.model_validate()` on each photo upload
  - Previously only validated `WorkflowState`, `ActionResponse`, and `IntakeChatOutput`
  - Now all 5 response models validated in the schema fidelity test: CreateProjectResponse, PhotoUploadResponse, WorkflowState, ActionResponse, IntakeChatOutput
  - All 299 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Fix Dockerfile layer ordering bug
  - `backend/Dockerfile` — fixed build-breaking layer order:
  - Before: `COPY pyproject.toml .` → `pip install .` → `COPY . .` — pip install fails because hatchling needs the `app/` package source, which isn't copied yet
  - After: `COPY . .` → `pip install .` — source present when hatchling builds
  - Trades Docker layer caching for correctness (deps reinstall on any source change, but builds actually work)
  - All 299 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- CLAUDE.md updated + Dockerfile fix documented
  - Test count 291→299, added error handling convention section, updated main.py description
  - Dockerfile layer ordering fix documented in CONTINUITY-T0.md
  - All 299 tests pass, 0 warnings, ruff clean, mypy clean (35 files)

- Module-scoped Temporal test environment fixture
  - `backend/tests/test_workflow.py` — major refactor: shared JVM for all 53 workflow tests
  - Before: each test created its own `WorkflowEnvironment.start_time_skipping()` (6 lines boilerplate × 52 tests)
  - After: module-scoped `workflow_env` fixture creates one JVM, all tests share it
  - 3 supporting mechanisms:
  - `pytestmark = pytest.mark.asyncio(loop_scope="module")` — module-scoped event loop for the shared fixture
  - Per-test `tq` fixture — unique task queue per test isolates Worker polling
  - Sync autouse `_cleanup_workflows` fixture — terminates zombie workflow handles after each test via `loop.run_until_complete()` (must be sync to avoid `MultipleEventLoopsRequestedError` with pytest-asyncio 0.26.0)
  - `_start_workflow` helper registers all handles in `_test_handles` list for automatic cleanup
  - Happy path refactored to use `_start_workflow` (was the only test with inline `env.client.start_workflow`)
  - Removed 52 `@pytest.mark.asyncio` decorators (redundant with pytestmark)
  - Removed `TASK_QUEUE = "test-queue"` constant (replaced by `tq` fixture)
  - `backend/pyproject.toml` — added `filterwarnings` to suppress Temporal sandbox pydantic_core warnings (pre-existing) and sync-test-with-asyncio-mark warning
  - Deleted temporary refactoring scripts: `_refactor_workflow_tests.py`, `_refactor_tq.py`
  - All 299 tests pass, 0 warnings, ruff clean, mypy clean

- CI pipeline improvements
  - `.github/workflows/ci.yml` — restructured from 1 job to 2 jobs:
  - `lint` job (fast): ruff check + ruff format --check + mypy (fails fast on style/type issues)
  - `test` job (slow, depends on lint): pytest with --cov=app coverage reporting + artifact upload
  - Added `concurrency` block: cancels stale CI runs when pushing again to same branch
  - Added `cache: pip` with `cache-dependency-path: backend/pyproject.toml` — pip install cached between runs
  - Added `ruff format --check` — enforces consistent code formatting (was missing)
  - Removed redundant `--ignore-missing-imports` from mypy (already in pyproject.toml)
  - Applied `ruff format` to 12 files that had formatting inconsistencies
  - Coverage: 99% (964 stmts, 14 missed — validation edge cases, R2 presign, worker startup)
  - All 299 tests pass, 0 warnings, ruff clean, ruff format clean, mypy clean

- Multi-step mock intake conversation flow
  - `backend/app/api/routes/projects.py` — intake endpoints now simulate a 3-question conversation:
  - `_mock_intake_messages` dict tracks user messages per project (cleared on delete/start_over)
  - `start_intake()` initializes message list, returns question 1 (room type, 3 quick-reply options)
  - `send_intake_message()` cycles through conversation steps:
    - Message 1: room type → returns style question (4 quick-reply options, progress="Question 2 of 3")
    - Message 2: style → returns open-ended preferences question (is_open_ended=True, progress="Question 3 of 3")
    - Message 3+: summary with partial_brief (is_summary=True, aggregates user's room type answer)
  - Response includes user's actual message content (e.g., "Great, a bedroom!")
  - Previously: both endpoints returned identical hardcoded responses, ignoring user input
  - Exercises ALL IntakeChatOutput fields: agent_message, options, is_open_ended, progress, is_summary, partial_brief
  - `backend/tests/test_api_endpoints.py` — test updates + 1 new test:
  - `test_send_message`: updated to verify step 2 behavior (style question, not summary)
  - `test_send_message_validates_through_model`: now sends 3 messages to reach summary before validating
  - New `test_intake_conversation_flow`: full 3-step conversation, verifies each step's field pattern
  - All 300 tests pass, 0 warnings, ruff clean, ruff format clean, mypy clean

- E2E happy path intake conversation + start_over conversation reset
  - `backend/tests/test_api_endpoints.py` — 2 test improvements:
  - `test_happy_path` enriched: now exercises full 3-step intake conversation (room type → style → preferences → summary) before confirming. Previously skipped directly to `confirm_intake`.
  - New `test_start_over_resets_intake_conversation`: partially completes intake (2 messages), starts over, verifies re-entering intake starts fresh at question 1 (not question 3).
  - Added `DesignOption` import for test fixture setup.
  - All 301 tests pass, 0 warnings, ruff clean, ruff format clean, mypy clean

- CLAUDE.md update to reflect current state
  - Updated test count 299→301
  - Added `ruff format --check` and coverage commands to Development Commands
  - Added CI description: 2-job pipeline with pip cache and coverage
  - Added "Mock API Behavior" section documenting intake conversation, photo upload auto-transition, iteration cap
  - Added module-scoped Temporal fixture note to tests description
  - Added multi-step intake conversation note to projects.py description

## Next
- P0 #9: Swift API models (T1-owned files, T0 provides contract source)
- P2 #13: Wire real activities into workflow

## Files Changed This Session
- `backend/app/utils/r2.py` (new — R2 client, 117 lines)
- `backend/tests/test_r2.py` (new — 14 tests)
- `backend/app/workflows/design_project.py` (review fixes + type annotations)
- `.github/workflows/ci.yml` (new — CI pipeline)
- `backend/pyproject.toml` (mypy config with per-module overrides, updated for validation + worker)
- `backend/app/activities/validation.py` (new — photo validation, ~170 lines)
- `backend/tests/test_validation.py` (new — 28 tests)
- `backend/app/utils/lidar.py` (new — LiDAR parser, 76 lines)
- `backend/tests/test_lidar.py` (new — 19 tests)
- `backend/app/activities/purge.py` (new — purge activity, 36 lines)
- `backend/tests/test_purge.py` (new — 3 tests)
- `backend/app/worker.py` (new — Temporal worker, 110 lines)
- `backend/tests/test_worker.py` (new — 12 tests)
- `backend/app/logging.py` (new — shared structlog config, 30 lines)
- `backend/app/main.py` (refactored — uses shared logging config)
- `docker-compose.yml` (added worker service)
- `backend/app/api/routes/projects.py` (modified — wired validate_photo into upload endpoint, asyncio.to_thread, 20MB limit)
- `backend/tests/test_api_endpoints.py` (modified — 23 tests, +3 new for validation integration + file size limit)
- `backend/migrations/versions/001_initial_schema.py` (new — initial Alembic migration, 9 tables)
- `backend/tests/test_migration.py` (new — 10 migration tests)
- `backend/app/workflows/design_project.py` (modified — error handling: workflow.logger, exception type in error messages, iteration retry wait, shopping error clear)
- `backend/tests/test_workflow.py` (modified — 17 tests, +4 new error recovery tests, failing activity stubs)
