# T3 Continuity Ledger

## Goal
Build T3 AI Agents — intake chat agent and shopping list pipeline.

## State
- **Done**:
  - **ALL P1 DELIVERABLES COMPLETE (#1-11)**
  - P1 #1: Design intelligence integration into system prompt
  - P1 #2: Quick Intake prompt + structured output via tool use
  - P1 #3: `run_intake_chat` activity (Quick mode)
  - P1 #4: Intake eval harness (DesignBrief Quality Rubric)
  - P1 #5: Shopping: anchored item extraction
  - P1 #6: Shopping: Exa search integration
  - P1 #7: Shopping: rubric-based scoring
  - P1 #8: Shopping pipeline eval criteria
  - P1 #9: `generate_shopping_list` activity
  - P1 #10-11: Golden test suite for intake (11 integration tests)
    - Translation engine tests (cozy→design params, modern→design params)
    - Brief validity tests (populated fields, style profile)
    - Diagnostic probing (vague answer follow-up)
    - Quick-reply option tests
    - Room-specific guidance (bedroom sleep optimization)
    - Multi-domain answer handling
    - Summary generation
    - Constraint detection (pets, kids)
  - Refactored `_run_intake_core` for direct testability
  - Registered `integration` pytest marker in pyproject.toml
  - 86 unit tests + 19 integration tests = 105 total T3 tests
  - 408 total tests pass (322 existing + 86 new unit), 19 integration skip (no API key)
  - Hardened: `_strip_code_fence` helper for robust JSON parsing, `strict=True` in zip
  - ruff clean, format clean, mypy clean

- **Done (P2 #12)**: Full Intake mode integration tests (5 tests)
    - Brief accumulation across turns
    - Summary within ~10 turn budget
    - Detailed constraint capture (ergonomic/health)
    - Contradiction detection (minimalism + collections)
    - Probing beneath surface answers ("feels off" → root cause)
  - **Done (P3 #13)**: Open Conversation mode integration tests (3 tests)
    - Open-ended prompt start
    - Follows user tangents (restaurant inspiration)
    - Produces brief from free-form conversation

- **Done**: Multimodal intake — room photos injected as image content blocks on first turn
    - `build_messages()` accepts `room_photo_urls`, builds multimodal content on turn 1
    - `_run_intake_core()` extracts `room_photos` from `project_context` and passes to `build_messages`
    - System prompt updated with "Room Photos" section — grounds design reasoning in what Claude sees
    - 4 new unit tests (multimodal first turn, no injection on turn 2, empty/None photo lists)
    - 408 total tests pass (322 existing + 86 new unit), 19 integration skip

- **Done**: Brief accumulation safety — previous brief injected into system prompt on turn 2+
    - `load_system_prompt()` accepts `previous_brief`, injects "GATHERED SO FAR" section
    - `_format_brief_context()` renders brief fields as readable context
    - `_run_intake_core()` extracts `previous_brief` from `project_context`
    - Prevents brief regression (model forgetting earlier information)
    - 5 new unit tests (no brief, turn 1 skip, turn 2+ injection, all fields, empty brief)
    - 413 total tests pass (322 existing + 91 new unit), 19 integration skip

- **Done**: Structured logging (structlog) for both intake and shopping activities
    - Intake: turn_start, turn_complete (with token usage), rate_limited, content_policy, api_error, missing tools
    - Shopping: pipeline_start/complete, items_extracted, search_complete, rate_limited, parse_error
    - Logger names: `t3.intake`, `t3.shopping`

- **Done**: Exa search with content retrieval + shopping intent steering
    - Added `contents.text.maxCharacters=1000` to Exa requests for actual product descriptions
    - Added "buy"/"shop" to search queries to steer toward product pages (not blogs)
    - Failed Exa searches now logged with status code + query
    - 1 new unit test (shopping intent in queries)
    - 414 total tests pass

- **Done**: Inspiration photo support — inspiration photos + user notes injected alongside room photos
    - `build_messages()` accepts `inspiration_photo_urls` and `inspiration_notes`
    - `_get_inspiration_note()` helper matches notes to photos by index
    - `_run_intake_core()` extracts inspiration data from `project_context`
    - System prompt updated with "Inspiration Photos" section
    - 10 new unit tests (inspo with notes, inspo without notes, inspo-only, note matching by index)
    - 423 total tests pass, mypy clean, ruff clean

- **Done**: Robust JSON extraction — `_extract_json()` handles preamble, postamble, and code fences
    - Brace-matching parser finds outermost JSON object in Claude's free-form text
    - Replaces raw `json.loads()` in `extract_items()` and `score_product()`
    - No more `JSONDecodeError` crashes from Claude wrapping JSON with explanatory text
    - Removed dead `JSONDecodeError` handler from `generate_shopping_list` activity
    - 12 new unit tests (pure JSON, code fence, preamble, nested braces, escaped quotes, etc.)
    - 435 total tests pass, mypy clean, ruff clean

- **Done**: Populate `fit_detail` for close matches — explains which sub-scores are weak
    - `_build_fit_detail()` identifies sub-scores < 0.5 and names them (material, color, etc.)
    - Populated only for `fit_status == "tight"` (0.5-0.79 confidence)
    - 6 new unit tests (_build_fit_detail + confidence filtering integration)
    - 441 total tests pass, mypy clean, ruff clean

- **Done**: DRY refactor — removed duplicated `_strip_code_fence` from intake_eval.py, uses `_extract_json` from shopping.py
- **Done**: Error handling hardening
    - Shopping: catches `APIStatusError` in extraction and scoring (content policy = non-retryable, others = retryable)
    - Intake: empty message guard (non-retryable error before API call)
    - Intake: safe `.get("message", "")` instead of `["message"]` KeyError risk
    - 2 new unit tests (empty message, non-empty passes validation)
    - 443 total tests pass, mypy clean, ruff clean

- **Done**: Prompt template caching — all 3 prompt file reads cached on first load
- **Done**: Tool schema enrichment — descriptions with elevation guidance on all brief properties
    - lighting: "All three layers + Kelvin temps"
    - colors: "60/30/10 proportions + application"
    - textures: "Professional material descriptors (min 3)"
    - mood: "Spatial and sensory terms"
    - domains_covered: "10-domain notepad" list
    - 2 new unit tests (property descriptions, style_profile descriptions)
    - 445 total tests pass, mypy clean, ruff clean

- **Done**: Test coverage expansion — added edge case tests for `_extract_json`, scoring prompt, extraction messages
    - 16 `_extract_json` tests (up from 12): escaped quotes with preamble, escaped backslashes, malformed JSON, unclosed braces
    - `_build_scoring_prompt` tests: item fields, no-brief case
    - `_build_extraction_messages` tests: multimodal messages, no room photos
    - Shopping coverage: 56% → 63%
    - 453 total tests pass (131 T3 unit + 19 integration + 303 existing), mypy clean, ruff clean

- **Done**: `respond_to_user` tool schema enrichment — descriptions with behavioral guidance
    - `message`: "Reference specific observations, show translations"
    - `options`: "2-4, classifiable, specific and distinct"
    - `is_open_ended`: "True for pain points/lifestyle/emotions"
    - `is_summary`: "True ONLY on final turn"
    - `label`/`value` sub-fields described
    - 453 total tests pass

- **Done**: Price extraction from Exa content
    - `_extract_price_text()` regex extracts dollar amounts from page text (e.g., "$1,299.00")
    - `_price_to_cents()` converts to integer cents for `ProductMatch.price_cents`
    - Scoring prompt now shows actual price instead of "Unknown"
    - `score_product()` populates `price_cents` from extracted price
    - 12 new unit tests for price extraction and conversion
    - 465 total tests pass (143 T3 unit + 19 integration + 303 existing)

- **Done**: Async client + parallel scoring pipeline
    - Switched `anthropic.Anthropic` → `anthropic.AsyncAnthropic` for truly async API calls
    - `extract_items` and `score_product` now use `await client.messages.create()`
    - `score_all_products` parallelized: flattens all item×product pairs, runs with `asyncio.gather`
    - `MAX_CONCURRENT_SCORES = 5` semaphore prevents rate limit hits
    - ~3-5x speedup for typical 6-item × 3-product pipeline (18 sequential → 5-concurrent batches)
    - 4 new unit tests (concurrency config, structure, sorting, empty results)
    - 469 total tests pass (147 T3 unit + 19 integration + 303 existing)

- **Done**: Intake async client — consistent with shopping pipeline
    - Switched `_run_intake_core` to `async def` with `anthropic.AsyncAnthropic`
    - `await client.messages.create()` instead of sync blocking call
    - No longer blocks Temporal worker event loop during API calls
    - Updated all 5 test files (2 unit + 3 integration) to use `asyncio.run()`
    - 469 total tests pass (no new tests — same behavior, async transport)

- **Done**: Mock-based test coverage expansion — intake 93%, shopping 84%, combined 87%
    - 5 new intake tests: mocked `_run_intake_core` (complete output, missing respond_to_user fallback, missing both tools, summary turn, turn counter from history)
    - 4 new shopping tests: extract_items parsing (items, empty, no text, wrapped JSON)
    - 2 new shopping tests: revision history formatting, search dedup + failure handling
    - 2 new shopping tests: extraction prompt with/without revisions
    - 482 total tests pass (160 T3 unit + 19 integration + 303 existing)

- **Done**: Pipeline orchestrator test coverage — `generate_shopping_list` activity + score edge cases
    - 4 new tests: full happy-path pipeline, empty extraction early return, missing API keys
    - 2 new tests: `score_product` empty scores fallback, price extraction from Exa content
    - Shopping coverage: 84% → 93%, Intake: 93%, Combined: 93%
    - 488 total tests pass (166 T3 unit + 19 integration + 303 existing)

- **Done**: Summary turn enforcement — conversations always terminate within budget
    - Server-side check: `turn_number >= max_turns` forces `is_summary=True`
    - Prevents runaway conversations (model ignoring turn budget instruction)
    - Logged as `intake_forced_summary` for observability
    - 2 new unit tests (forced on final turn, not forced before max)
    - 490 total tests pass (168 T3 unit + 19 integration + 303 existing)

- **Done**: Search quality improvements — description queries + priority-based result counts
    - Description-based query added for all source tags (skipped when same as source_reference)
    - `_num_results_for_item()`: HIGH=5, MEDIUM=3, LOW=2 results per Exa query
    - `_PRIORITY_NUM_RESULTS` mapping drives `search_products_for_item`
    - 7 new unit tests (description queries: added, skipped, different-desc, priority: H/M/L/default)
    - 497 total tests pass (175 T3 unit + 19 integration + 303 existing)

- **Done**: Error handler test coverage — both intake and shopping error paths fully tested
    - Intake: 3 tests (RateLimitError retryable, BadRequestError non-retryable, InternalServerError retryable)
    - Shopping extraction: 3 tests (RateLimitError, BadRequestError, InternalServerError)
    - Shopping scoring: 3 tests (RateLimitError, BadRequestError, InternalServerError)
    - Shopping search: 1 test (generic failure → retryable)
    - All error classification verified: 400=non-retryable, 429/500=retryable
    - Search dedup BaseException skip tested, retailer extraction edge case tested
    - 509 total tests pass (187 T3 unit + 19 integration + 303 existing)
    - Coverage: intake 99%, shopping 98%, combined 99%

- **Done**: Token usage logging for shopping pipeline — cost observability
    - `extract_items`: logs `shopping_extraction_tokens` (input/output tokens + model name)
    - `score_all_products`: aggregates all scoring call tokens, logs `shopping_scoring_tokens` (totals + count + model)
    - Token fields stripped from scored results before output (internal-only, not leaked to clients)
    - Enables cost monitoring: 1 Opus extraction + N Sonnet scoring calls per pipeline run
    - 509 total tests pass, mypy clean, ruff clean

- **Done**: Exa search retry logic — recovers from transient failures
    - Retries once on 429 (rate limit), 500/502/503 (server errors), and timeouts
    - No retry on 400/401/403 (client errors — permanent failures)
    - Configurable: `EXA_MAX_RETRIES = 1`, `EXA_RETRY_DELAY = 1.0`
    - Structured logging: `exa_search_retrying`, `exa_search_timeout`, `exa_search_timeout_final`
    - 6 new tests (429 retry, 500 retry, 400 no-retry, timeout retry, exhausted retries, config)
    - 515 total tests pass (193 T3 unit + 19 integration + 303 existing)

- **Done**: Cross-item product dedup — same product URL can't match multiple items
    - `apply_confidence_filtering` tracks used URLs, skips duplicates for later items
    - Falls back to next-best product when preferred is already claimed
    - Prevents duplicate products in the shopping list UI
    - 2 new tests (dedup skips duplicate, dedup picks fallback)
    - 517 total tests pass (195 T3 unit + 19 integration + 303 existing)

- **Done**: Well-known retailer name mapping — professional display names
    - `_RETAILER_NAMES` dict: 25 retailers (Amazon, IKEA, CB2, Pottery Barn, West Elm, RH, etc.)
    - Falls back to domain capitalization for unknown retailers
    - 6 new tests (CB2, Pottery Barn, West Elm, IKEA, Crate & Barrel, RH, fallback)
    - 523 total tests pass (201 T3 unit + 19 integration + 303 existing)

- **Done**: Extraction output validation — drops malformed items before pipeline
    - `_validate_extracted_items()` requires non-empty `category` + `description`
    - Normalizes `source_tag` (→ IMAGE_ONLY) and `search_priority` (→ MEDIUM) on invalid values
    - Logs `shopping_item_dropped` per dropped item + `shopping_items_validated` summary
    - Prevents garbage-in-garbage-out through search and scoring steps
    - 9 new tests (valid pass-through, missing fields, None/int types, normalization, mixed)
    - 532 total tests pass (210 T3 unit + 19 integration + 303 existing)

- **Done**: Final coverage push — timeout exhaustion test + validation tests
    - Added all-timeouts test for Exa search (both attempts timeout → return [])
    - Coverage: intake 99%, shopping 98%, combined 98% (8 remaining = infra/defensive only)
    - 533 total tests pass (211 T3 unit + 19 integration + 303 existing)
    - ruff clean, format clean, mypy clean

- **Done**: Null safety for extraction items — `data.get("items") or []` handles `{"items": null}`
    - Prevents TypeError when model returns null instead of empty array
    - 1 new unit test (null items → empty list)
    - 534 total tests pass (212 T3 unit + 19 integration + 303 existing)

- **Done**: Scoring pipeline resilience — individual score failures no longer crash entire pipeline
    - `asyncio.gather(*tasks, return_exceptions=True)` tolerates partial failures
    - Failed scores logged as `shopping_score_failed` with error detail, then skipped
    - Summary `shopping_scoring_failures` log shows failed/succeeded/total counts
    - Previously: 1 rate-limited score out of 18 → entire pipeline crash + all scores lost
    - Now: 1 failure → 17 scores preserved, pipeline continues gracefully
    - 2 new unit tests (partial failure tolerance, all-fail returns empty)
    - 536 total tests pass (214 T3 unit + 19 integration + 303 existing)

- **Now**: FEATURE-COMPLETE + PRODUCTION-HARDENED + FULLY-ASYNC + 98% COVERAGE + SEARCH-OPTIMIZED + ERROR-PATHS-TESTED + COST-OBSERVABLE + RETRY-RESILIENT + DEDUP-ACROSS-ITEMS + RETAILER-NAMES + INPUT-VALIDATED + NULL-SAFE + SCORING-RESILIENT

- **Next**: Integration test validation (needs API key) or further enhancements

## Files Created/Modified
- `backend/prompts/intake_system.txt` (NEW) — system prompt with design intelligence + Room Photos + Inspiration Photos sections
- `backend/prompts/item_extraction.txt` (NEW) — shopping extraction prompt
- `backend/prompts/product_scoring.txt` (NEW) — shopping scoring rubric prompt
- `backend/app/activities/intake.py` (NEW) — run_intake_chat activity
- `backend/app/activities/intake_eval.py` (NEW) — eval harness
- `backend/app/activities/shopping.py` (NEW) — generate_shopping_list activity
- `backend/tests/test_intake.py` (NEW) — 80 unit tests
- `backend/tests/test_shopping.py` (NEW) — 95 unit tests
- `backend/tests/test_intake_golden.py` (NEW) — 11 integration tests (quick mode)
- `backend/tests/test_intake_full_mode.py` (NEW) — 5 integration tests (full mode)
- `backend/tests/test_intake_open_mode.py` (NEW) — 3 integration tests (open mode)
- `backend/pyproject.toml` (MODIFIED) — added `integration` marker

## Key Decisions
- Mode instructions via `{mode_instructions}` placeholder
- MAX_TURNS: quick=4, full=11, open=16
- `_run_intake_core` extracted for direct testing without Temporal
- Integration tests auto-skip when ANTHROPIC_API_KEY is not set
- Eval uses Claude Sonnet (cheaper) for scoring

## Open Questions
- Integration test results pending (needs API key in CI or manual run)
- Exa search quality for furniture queries (needs live testing)
