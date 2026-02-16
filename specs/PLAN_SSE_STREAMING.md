# SSE Streaming + Text Input Fix + Exa Search Logging

## Context

The app has terrible perceived latency in two places:
1. **Intake chat** — user sends a message, waits 5-15s for Claude to respond, then gets the entire response dumped at once
2. **Shopping list** — user sees a blank spinner for 60-180s while the shopping pipeline runs, then gets all products at once
3. **Text input bug** — design refinement text input (iteration screen) truncates long text display

SSE (Server-Sent Events) streaming will make:
- Intake chat messages appear word-by-word (like ChatGPT)
- Shopping products appear one-by-one as they're found

## TODO List

### Part 0: Text Input Truncation Fix (Quick Win)

- [x] **TODO 0.1**: ~~Fix text input truncation~~ **DONE** — Replaced `.roundedBorder` with `.plain` + manual `RoundedRectangle` border, increased `lineLimit(3...12)`. Swift tests pass.

### Part 1: Intake Chat SSE Backend

**Current flow:** iOS → `POST /projects/{id}/intake/message` → `_real_intake_message()` (projects.py:826) → `_run_intake_core()` (intake.py:796) → `client.messages.create()` (intake.py:872) → returns full `IntakeChatOutput`

**Streaming challenge:** Claude is forced to use tools (`tool_choice={"type": "any"}`), so the response comes as `input_json_delta` events (streaming the tool input JSON), not `text_delta`. The `message` field is buried inside the JSON being streamed incrementally.

- [x] **TODO 1.1**: ~~Extract shared setup~~ **DONE** — Created `_IntakeCallParams` dataclass and `_prepare_intake_call()` in intake.py. Both `_run_intake_core` and `_stream_intake_sse` reuse it.

- [x] **TODO 1.2**: ~~Add streaming generator~~ **DONE** — Created `_MessageExtractor` (incremental JSON message parser with escape handling) and `_stream_intake_sse()` async generator yielding `delta`/`done`/`error` SSE events. Uses `_process_intake_response()` for shared post-processing.

- [x] **TODO 1.3**: ~~Add streaming endpoint~~ **DONE** — `POST /projects/{id}/intake/message/stream` returns `StreamingResponse(text/event-stream)`. Parses done event before yielding, updates session state after streaming. Error handling for presigned URLs and post-processing.

- [x] **TODO 1.4**: ~~Add backend tests~~ **DONE** — 16 new tests: `TestPrepareIntakeCall` (4), `TestProcessIntakeResponse` (2), `TestMessageExtractor` (8), `TestStreamIntakeSSE` (2). All 589 backend tests pass.

### Part 2: Intake Chat SSE iOS

- [x] **TODO 2.1**: ~~Add SSE event types~~ **DONE** — `IntakeSSEEvent` enum (`.delta`/`.done`) and `SSELineParser` struct in Models.swift. 7 new unit tests.

- [x] **TODO 2.2**: ~~Add streaming client~~ **DONE** — `streamIntakeMessage()` in RealWorkflowClient using `URLSession.bytes(for:)`. Decodes HTTP error body (matching `checkHTTPResponse` pattern), wraps errors as `APIError`, cancels on stream termination.

- [x] **TODO 2.3**: ~~Add protocol method~~ **DONE** — `streamIntakeMessage` in `WorkflowClientProtocol` with default extension fallback that wraps non-streaming result in `.done()`. MockWorkflowClient inherits it automatically. onTermination cancels the inner Task.

- [x] **TODO 2.4**: ~~Update chat UI for streaming~~ **DONE** — `sendMessage()` uses streaming with progressive text rendering. Index-based rollback on error (no content matching). Only falls back to non-streaming if zero deltas received (prevents double-send). CancellationError properly rethrown.

### Part 3: Shopping List SSE Backend

**Current flow:** Workflow (design_project.py:276) → `execute_activity(generate_shopping_list)` → 5-step pipeline in shopping.py:1542 (extract → search ALL → score ALL → dim filter → confidence filter) → returns full `GenerateShoppingListOutput` → workflow sets `shopping_list`, transitions to "completed"

**Per-item streaming:** API runs pipeline directly (not as Temporal activity), streams each item to iOS as search+score completes. Signals workflow with full result at the end.

- [x] **TODO 3.1**: ~~Add signals~~ **DONE** — `handle_shopping_streaming` + `receive_shopping_result` signals in workflow. Shopping phase checks `_shopping_streaming` flag inside while loop: no SSE → immediate activity, SSE claimed → 300s wait for result, timeout → fallback. 3 unit tests for signals.

- [x] **TODO 3.2**: ~~Add streaming generator~~ **DONE** — `generate_shopping_list_streaming()` async generator. Sequential search for progress events (`status` → `item_search` per item), batch scoring for quality, individual `item` events per match, final `done` event. Error events for API key missing, extraction/scoring failures.

- [x] **TODO 3.3**: ~~Add shopping SSE endpoint~~ **DONE** — `GET /projects/{id}/shopping/stream` validates step=shopping + Temporal mode. Signals `handle_shopping_streaming` on connect, `receive_shopping_result` after completion. `StreamingResponse(text/event-stream)`.

- [x] **TODO 3.4**: ~~Add backend tests~~ **DONE** — 5 streaming generator tests (missing keys, full pipeline, empty extraction, extraction error), 3 workflow signal tests, scaffold updated. 1500 backend tests pass.

### Part 4: Shopping List SSE iOS

- [x] **TODO 4.1**: ~~Add shopping SSE event types~~ **DONE** — `ShoppingSSEEvent` enum (`.status`/`.itemSearch`/`.item`/`.done`) and `ShoppingSSELineParser` struct in Models.swift. 8 new unit tests.

- [x] **TODO 4.2**: ~~Add `streamShopping()` in `RealWorkflowClient.swift`~~ **DONE** — Returns `AsyncThrowingStream<ShoppingSSEEvent, Error>`, GETs `/projects/{id}/shopping/stream`, HTTP error body parsing, proper cancellation handling.

- [x] **TODO 4.3**: ~~Add protocol method for streaming shopping~~ **DONE** — `streamShopping` in `WorkflowClientProtocol` with default extension fallback (returns empty stream, triggers polling). MockWorkflowClient inherits automatically.

- [x] **TODO 4.4**: ~~Replace `ShoppingGeneratingScreen.swift` spinner with progressive product list~~ **DONE** — SSE-first with polling fallback. Status/itemSearch update progress text. Items animate into list via `ProductCard` (made public in RemoShoppingList). `done` event applies state to transition to completed. Empty-stream detection triggers automatic polling fallback.

### Part 5: Exa Search Parameter Logging via LangSmith

**Problem:** Shopping search results are poor and we have no visibility into what queries/params are actually being sent to Exa. Need to log every Exa API call with full parameters to LangSmith for debugging.

**Current state:** LangSmith tracing exists in `backend/app/utils/tracing.py` — wraps Anthropic and Gemini clients, plus a `@traceable` decorator for arbitrary functions. Shopping already uses `trace_thread(project_id, "shopping")` for Claude calls (scoring, extraction). But Exa HTTP calls (`_search_exa()` at shopping.py:636) are **not traced** — they use raw `httpx` POST to `https://api.exa.ai/search`.

**Key functions to trace in `backend/app/activities/shopping.py`:**
- `_build_search_queries()` (L505) — builds 6 query types from item + design brief. Log: item description, all generated queries, design brief context used
- `_search_exa()` (L636) — sends actual HTTP request to Exa. Log: full request payload (query, type, numResults, includeDomains, includeText, contents schema), response status, result count, result URLs
- `search_products_for_item()` (L781) — orchestrates dual-pass search. Log: item name, priority, pass 1 vs pass 2 results, dedup stats
- `score_all_products()` (L1122) — Claude batch scoring call. Already traced via `wrap_anthropic`, but log: item count, number of candidates, scores assigned. Individual scoring via `score_product()` (L1052).

- [x] **TODO 5.1**: ~~Add `@traceable` decorator to Exa search functions~~ **DONE** — Decorated `_build_search_queries` (chain), `_search_exa` (tool), `search_products_for_item` (chain). Import added at module level.
  - Import `traceable` from `app.utils.tracing`
  - Decorate `_build_search_queries()` with `@traceable(name="exa.build_queries", run_type="chain")`
    - Inputs already captured by decorator: `item`, `design_brief`, `room_dimensions`
    - Return value (list of queries) already captured
  - Decorate `_search_exa()` with `@traceable(name="exa.search_request", run_type="tool")`
    - Log the full payload dict as input (query, type, numResults, domains, text filters)
    - Log response: status code, result count, URLs returned
  - Decorate `search_products_for_item()` with `@traceable(name="exa.search_item", run_type="chain")`
    - Captures item name, priority, pass 1 vs pass 2 orchestration
  - Decorate `score_all_products()` with `@traceable(name="exa.score_batch", run_type="chain")` if not already traced
  - Optionally decorate `score_product()` with `@traceable(name="exa.score_item", run_type="chain")` for per-item visibility
  - All decorators are zero-cost no-ops when `LANGSMITH_API_KEY` is unset (existing `traceable` wrapper handles this)

- [x] **TODO 5.2**: ~~Ensure `trace_thread` wraps the full shopping pipeline~~ **DONE** — Added `trace_thread(_project_id, "shopping_search")` around search phase in both `generate_shopping_list()` and `generate_shopping_list_streaming()`.
  - The `generate_shopping_list()` activity (L1542) already uses `trace_thread(project_id, "shopping")` for extraction and scoring phases
  - Verify that the Exa search phase (between extraction and scoring) is ALSO inside a `trace_thread` context
  - If not, extend the trace_thread scope to cover the full pipeline: extract → search → score → filter
  - This groups all Exa calls under a single LangSmith thread per project, making it easy to see the full search flow

- [x] **TODO 5.3**: ~~Add structured metadata to Exa search traces~~ **DONE** — `_search_exa()` adds `get_current_run_tree()` metadata on success: query, search_type, num_results, domains, text_filter, response_count, response_urls. Best-effort try/except.
  - In `_search_exa()`, before returning, add LangSmith metadata via `langsmith.run_trees.get_current_run_tree()` (if available):
    - `exa_query`: the search query string
    - `exa_search_type`: "auto" or "deep"
    - `exa_num_results`: requested count
    - `exa_domains`: domain whitelist (or "open_web")
    - `exa_text_filter`: text filter (or "none")
    - `exa_response_count`: actual results returned
    - `exa_response_urls`: list of result URLs
  - This makes LangSmith filtering/searching easy (e.g. "show me all deep searches that returned 0 results")
  - Wrap in try/except — metadata is best-effort, never fails the search

- [x] **TODO 5.4**: ~~Add test for Exa tracing decorators~~ **DONE** — `TestExaTracingDecorators` with 4 tests: return value preservation, DesignBrief parameter handling, async callable checks. All pass.
  - Test that `_build_search_queries` and `_search_exa` are callable with and without `LANGSMITH_API_KEY` set
  - Test that tracing decorators don't alter return values or behavior
  - Verify existing shopping tests still pass (decorators should be transparent)

### Part 6: End-to-End Verification

- [x] **TODO 6.1**: ~~E2E verification~~ **DONE (automated + real AI E2E + review fixes)** — 1509 backend + 154 Swift = 1663 total tests pass. Real AI E2E: 4 new SSE tests in test_e2e.py (intake single-turn 206 deltas/1432 chars, multi-turn 3 turns to summary, session persistence SSE↔non-streaming, shopping full pipeline 10 items streamed progressively in 10m16s). Ruff clean. Review fixes applied: narrowed `except Exception` to `ValidationError` in both SSE endpoints, protected `_prepare_intake_call`, wrapped filtering in try/except, improved error logging, hardened SSE parser with malformed event detection + multi-line data accumulation, added hard assertions in shopping SSE test, `pytest.skip` on race-lost path, delta-vs-done prefix comparison, project cleanup in all E2E tests.
  - Upload 3 room photos + LiDAR scan data to create a project
  - Test intake chat SSE streaming — send messages, verify word-by-word streaming works with real Claude responses
  - Walk through full pipeline: intake → generation → iteration
  - Test shopping SSE streaming — verify products appear one-by-one during shopping phase
  - Verify text input fix — enter long text in iteration screen, confirm scrolling works
  - Verify all fallbacks work (non-streaming paths still functional)
  - Run full test suite: `pytest -x -q` (1476+ tests must pass)
  - Run Swift tests: `swift test --package-path ios/Packages/RemoNetworking` etc.

---

## Key Files Reference

| File | Current Role | Changes |
|------|-------------|---------|
| `backend/app/activities/intake.py` | `_run_intake_core()` at L796, `client.messages.create()` at L872 | Add `_prepare_intake_call()`, `_stream_intake_sse()` |
| `backend/app/api/routes/projects.py` | `_real_intake_message()` at L826, `_signal_workflow()` at L225 | Add 2 SSE endpoints |
| `backend/app/workflows/design_project.py` | Shopping phase at L267-294, 15 existing signals | Add `receive_shopping_result` + `handle_shopping_streaming` signals |
| `backend/app/activities/shopping.py` | `generate_shopping_list()` at L1542, `search_products_for_item()` at L781, `_search_exa()` at L636, `_build_search_queries()` at L505, `score_all_products()` at L1122, `score_product()` at L1052 | Add `generate_shopping_list_streaming()`, add `@traceable` decorators to Exa search functions |
| `backend/app/utils/tracing.py` | `wrap_anthropic()`, `wrap_gemini()`, `traceable()`, `trace_thread()` | No changes needed (existing `@traceable` decorator used) |
| `backend/app/models/contracts.py` | `IntakeChatOutput` at L344, `GenerateShoppingListOutput` at L329 | No changes (contracts frozen) |
| `ios/.../RealWorkflowClient.swift` | `sendIntakeMessage()` at L105 | Add `streamIntakeMessage()`, `streamShopping()` |
| `ios/.../WorkflowClientProtocol.swift` | Protocol with 19 methods | Add 2 streaming methods with defaults |
| `ios/.../IntakeChatScreen.swift` | `sendMessage()` at L268 | Use streaming client, progressive text |
| `ios/.../ShoppingGeneratingScreen.swift` | Spinner + polling at L46-66 | Progressive product list via SSE |
| `ios/.../IterationScreen.swift` | `textControls` at L342-357, `lineLimit(2...6)` | Fix truncation |

## Implementation Order

1. **TODO 0.1** — Text input fix (5 min, quick win)
2. **TODOs 1.1-1.4** — Intake SSE backend
3. **TODOs 2.1-2.4** — Intake SSE iOS
4. **TODOs 3.1-3.4** — Shopping SSE backend
5. **TODOs 4.1-4.4** — Shopping SSE iOS
6. **TODOs 5.1-5.4** — Exa search parameter logging via LangSmith
7. **TODO 6.1** — E2E test with 3 room photos + LiDAR data

Each step is independently testable. Existing non-streaming paths remain as fallbacks.

## Task Dependencies

```
Part 0: [#1 Text Input Fix] ──────────────────────────────────────────┐
                                                                       │
Part 1: [#2 Prepare] → [#3 Stream Gen] → [#4 Endpoint] → [#5 Tests] ─┤
                                                                       │
Part 2: [#6 SSE Types] → [#7 Client] ──→ [#9 Chat UI]                 │
                    └──→ [#8 Protocol] ──┘                             │
                                                                       ├→ [E2E Test]
Part 3: [#10 Signal] → [#11 Stream Gen] → [#12 Endpoint] → [#13 Tests]│
                                                                       │
Part 4: [#14 SSE Types] → [#15 Client] ──→ [#17 Shopping UI] ─────────┤
                     └──→ [#16 Protocol] ──┘                           │
                                                                       │
Part 5: [Exa @traceable] → [trace_thread scope] → [metadata] → [test] ┘
```

## Parallelization Opportunities

- Parts 1+3 (backend intake + backend shopping) can be done in parallel
- Parts 2+4 (iOS intake + iOS shopping) can be done in parallel (after their backend parts)
- Part 0 and Part 5 (text input fix + Exa logging) are fully independent of each other and of Parts 1-4

## Verification

- **Backend unit tests**: Mock Anthropic streaming client, verify SSE event format and content
- **curl manual test**: `curl -N POST http://localhost:8000/api/v1/projects/{id}/intake/message/stream -d '...'` → see events stream
- **curl manual test**: `curl -N http://localhost:8000/api/v1/projects/{id}/shopping/stream` → see item events
- **Fallback test**: Existing non-streaming endpoints still work, MockWorkflowClient still works
- **iOS simulator**: Verify intake chat streams word-by-word, shopping shows items progressively
- **Text input**: Type long text in iteration screen, verify it scrolls/expands properly
- **Run full test suite**: `pytest -x -q` (1476+ tests must pass)
- **Swift tests**: `swift test --package-path ios/Packages/RemoNetworking`
- **Exa logging**: Set `LANGSMITH_API_KEY`, run shopping pipeline, verify in LangSmith dashboard: all Exa queries visible with full params (query, domains, text filter, search type), response URLs, and per-item thread grouping
- **Exa logging off**: Unset `LANGSMITH_API_KEY`, verify shopping pipeline still works identically (zero-cost decorators)
- **E2E with real data**: Use 3 provided room photos + LiDAR data, walk through full pipeline verifying streaming at each stage
