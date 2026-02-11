# T2 Continuity Ledger

## Goal
Build T2 image generation pipeline: generate_designs + edit_design activities, annotation utility, Gemini chat manager, prompt templates.

## Constraints/Assumptions
- Model: `gemini-3-pro-image-preview` (confirmed by P0 spike)
- SDK: `google-genai` v1.62.0 (new unified SDK)
- `as_image()` returns `google.genai.types.Image`, not PIL — must convert via `Image.open(BytesIO(img.image_bytes))`
- Both models return thought signatures (even Flash)
- Annotation artifacts in output need stronger prompting (two-step approach in edit.txt + retry)

## Key Decisions
- **Model**: `gemini-3-pro-image-preview` — 14 input images, higher photorealism
- **Fallback**: `gemini-2.5-flash-image` — for rate limit overflow or cost optimization
- **Anti-artifact strategy**: Strong CRITICAL instruction in edit.txt + retry with "Remove ALL annotations" if needed
- **Chat continuation**: Uses `generate_content` with full history (not `chat.send_message`) since history is deserialized from R2
- **Project ID**: Extracted from R2 storage key pattern `projects/{id}/...` (GenerateDesignsInput has no project_id field)
- **Error retryability**: HTTP 4xx = non-retryable, 5xx = retryable, rate limits = retryable, safety = non-retryable

## State
- Done: P0 #1 Gemini quality spike (all 4 scenarios pass both models, 8/8 total)
- Done: P0 #2 Model selection decision (`spike/results/MODEL_DECISION.md`)
- Done: P1 #3 Annotation drawing utility (`backend/app/utils/image.py`)
- Done: P1 #4 Gemini chat session manager (`backend/app/utils/gemini_chat.py`)
- Done: P1 #5 Prompt template library (`backend/prompts/`)
- Done: P1 #6 `generate_designs` activity (`backend/app/activities/generate.py`)
- Done: P1 #7 `edit_design` activity (`backend/app/activities/edit.py`)
- Done: P2 #8 Quality test suite — 6 real API integration tests + 91 unit tests (413 total in repo)
- Done: 2 rounds of code review — 11 issues found and all fixed
- Done: Mock-based test coverage for all activity error paths (72% coverage)
- Now: All deliverables complete, ready for PR
- Next: T0 P2 #13 (wire real activities into workflow)

## Quality Metrics
- 138 T2-specific tests (132 unit + 6 integration)
- 460 total tests in repo (all pass)
- Ruff lint: clean
- Ruff format: clean
- Mypy: clean (no errors in T2 files)
- Coverage: 100% on ALL T2 source files (generate.py, edit.py, gemini_chat.py, image.py)

## Files Created/Modified
- `spike/gemini_spike.py` — spike test script
- `spike/create_test_image.py` — synthetic test room generator
- `spike/results/` — all spike output images and reports
- `spike/results/MODEL_DECISION.md` — model selection rationale
- `backend/app/utils/image.py` — annotation drawing utility
- `backend/app/utils/gemini_chat.py` — Gemini chat session manager
- `backend/app/activities/generate.py` — generate_designs activity
- `backend/app/activities/edit.py` — edit_design activity
- `backend/prompts/generation.txt` — initial generation prompt
- `backend/prompts/edit.txt` — annotation edit prompt
- `backend/prompts/room_preservation.txt` — shared preservation clause
- `backend/tests/test_image_utils.py` — 21 tests
- `backend/tests/test_gemini_chat.py` — 36 tests
- `backend/tests/test_generate.py` — 36 tests
- `backend/tests/test_edit.py` — 36 tests
- `backend/tests/test_image_utils.py` — 23 tests
- `backend/tests/test_integration_generate.py` — 6 integration tests
- `backend/pyproject.toml` — added integration marker

## Code Review Issues Fixed (11 total)
1. project_id randomly generated → extracted from R2 storage key path with validation
2. Missing user turn in chat history continuation → now includes both user turn + model response
3. Unsafe deserialization → validates turn structure and inline_data format
4. Annotations/feedback mutually exclusive → supports both simultaneously
5. No specific HTTP error handling → distinguishes 4xx (non-retryable) from 5xx (retryable)
6. No content-type validation → verifies downloaded content is image/*
7. Duplicated serialization logic → shared `serialize_contents_to_r2()` helper
8. R2 get_object errors unhandled → catches `NoSuchKey` + `ClientError`
9. JSON deserialization errors unhandled → catches `JSONDecodeError` with logging
10. Project ID regex too permissive → rejects path traversal and empty strings
11. Mypy errors → null-safe access for all optional Gemini SDK fields

## Open Questions
- Optimal anti-artifact prompt wording (needs real-image testing beyond spike)
- Chat session reset frequency (every 3 edits? evaluate empirically)
