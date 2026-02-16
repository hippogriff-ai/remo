# Plan: Fix E2E Tests + Add Real AI LiDAR Golden Path

## Context

The E2E test suite (`backend/tests/test_e2e.py`) is **currently broken** against the real Temporal backend. A `confirm_photos` endpoint was added to the workflow (requiring explicit user confirmation before advancing from "photos" to "scan" step), but the E2E tests were never updated. Every test that uploads photos and expects to advance past the "photos" step hangs on a poll timeout.

The server on port 8001 is already running with **all real AI services configured**: Anthropic (Claude Opus 4.6), Gemini (gemini-3-pro-image-preview), Exa, R2, PostgreSQL, Temporal. No missing keys.

Additionally, the existing golden path test (`test_full_pipeline_real_ai`) skips LiDAR. We need a LiDAR variant that exercises the full pipeline with room dimensions flowing through generation, editing, and shopping.

---

## Bug: Missing `confirm_photos` in E2E Tests

### Root Cause

Workflow `design_project.py` line 116:
```python
await self._wait(
    lambda: (
        sum(1 for p in self.photos if p.photo_type == "room") >= 2 and self.photos_confirmed
    )
)
```

Requires BOTH `>= 2 room photos` AND `photos_confirmed = True`. The `confirm_photos` signal (line 358) sets this flag. The API endpoint is `POST /projects/{pid}/photos/confirm` (projects.py line 565).

The `test_workflow.py` tests were updated (47 locations), but `test_e2e.py` was missed.

### Fix Locations (5 total)

**Helper functions (fix cascades to ~20 tests each):**

1. **`_create_project_with_photos()`** — line 311
   - After: `for _ in range(room_count): r = await _upload_photo(...)`
   - Before: `await _poll_step(client, project_id, "scan", timeout=10.0)`
   - Add:
     ```python
     r = await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
     assert r.status_code == 200, f"confirm_photos failed: {r.status_code} {r.text}"
     ```

2. **`_advance_to_intake_with_scan()`** — line 701
   - After: `for _ in range(2): r = await _upload_photo(...)`
   - Before: `await _poll_step(client, pid, "scan", timeout=10.0)`
   - Add same `confirm_photos` POST

**Inline tests (3 locations):**

3. **`test_full_pipeline_real_ai`** — line ~2219
   - After the 2-photo upload loop, before `await _poll_step(client, pid, "scan", timeout=15.0)`

4. **`test_rapid_photo_uploads`** — line ~2108
   - After `results = await asyncio.gather(*tasks)`, before `await _poll_step(client, pid, "scan", timeout=10.0)`

5. **`test_intake_with_inspiration_photo_context`** — line ~1718
   - After room + inspiration photo uploads, before `await _poll_step(client, pid, "scan", timeout=10.0)`

---

## New Test: `test_full_pipeline_real_ai_with_lidar`

Add inside existing `TestGoldenPathRealAI` class (which already has `@_skip_unless_real` marker).

### Pipeline Steps

```
create (has_lidar=True) → upload 2 photos → confirm_photos →
submit scan data (4.2m x 5.8m, furniture, openings) →
real intake (Claude Opus, 4 messages) → confirm brief →
real generation (Gemini, 180s timeout) → select option →
real edit (Gemini, 120s timeout, dimension-aware feedback) → approve →
real shopping (Exa + Claude, 180s timeout) → verify → delete
```

### Assertions at Each Step

| Step | Assertions |
|------|-----------|
| Scan submit | `scan_data` parsed: width_m=4.2, length_m=5.8, height_m=2.7, floor_area=24.36, 2 furniture, 1 opening |
| Intake | Agent responses >20 chars each |
| Selection | 2 generated options, non-mock URLs, captions >10 chars |
| Selection | `scan_data` persists with correct dimensions |
| Selection | `room_context.enrichment_sources` includes "lidar" (if available) |
| Iteration | 1+ revision, non-mock revised URL |
| Iteration | `scan_data` still present |
| Completed | Shopping list: 3+ items, non-mock product names, valid URLs |
| Completed | Prices: at least 1 item with price_cents > 0, total > 0 |
| Completed | `scan_data` persists: width_m=4.2, floor_area=24.36, 2 furniture |
| Completed | `room_context` has both "lidar" and "photos" in enrichment_sources |
| Delete | Returns 204 |

### Existing Infrastructure to Reuse

- `_SCAN_DATA` constant (line 32) — reference LiDAR data (4.2m x 5.8m room)
- `_NEW_PROJECT_LIDAR` (line 29) — `{"has_lidar": True, ...}`
- `_make_sharp_jpeg()` — loads real room photo from `tests/fixtures/room_photo.jpg`
- `_poll_step()` / `_poll_iteration()` — polling with fail-fast on errors
- `_skip_unless_real` marker — skips when mock activities are loaded

### Intake Messages (LiDAR-aware)

```python
[
    "It's a living room, roughly 4 by 6 meters with a door on the east wall",
    "Modern Scandinavian with natural wood and neutral tones",
    "Replace the sofa with a sectional, keep the table, add floor plants",
    "That covers everything, please summarize",
]
```

### Edit Feedback (dimension-aware)

```python
"The sofa needs to fit the 4.2m wall. Add warmer lighting and place a tall plant by the door."
```

---

## Convenience Script: `scripts/run-e2e.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../backend"

URL="${E2E_BASE_URL:-http://localhost:8001}"

if ! curl -sf "${URL}/health" > /dev/null 2>&1; then
    echo "ERROR: Backend not reachable at ${URL}"
    echo "Start API server + worker first."
    exit 1
fi

echo "Running E2E tests against ${URL}..."
E2E_BASE_URL="${URL}" .venv/bin/python -m pytest tests/test_e2e.py -x -v --tb=short "$@"
```

---

## Execution Order

1. Fix `_create_project_with_photos` helper (line 311)
2. Fix `_advance_to_intake_with_scan` helper (line 701)
3. Fix `test_full_pipeline_real_ai` inline (line ~2219)
4. Fix `test_rapid_photo_uploads` inline (line ~2108)
5. Fix `test_intake_with_inspiration_photo_context` inline (line ~1718)
6. Add `test_full_pipeline_real_ai_with_lidar` in `TestGoldenPathRealAI` class
7. Create `scripts/run-e2e.sh`
8. Run: `E2E_BASE_URL=http://localhost:8001 .venv/bin/python -m pytest tests/test_e2e.py -x -v`

## Expected Timing

- Mock-mode tests (~70): 2-3 minutes
- Real-AI tests (~20): 5-10 minutes
- LiDAR golden path: 8-12 minutes (generation ~120s + edit ~60s + shopping ~60s)
- Total suite: ~15-20 minutes

## Files to Modify

- `backend/tests/test_e2e.py` — all fixes + new test
- `scripts/run-e2e.sh` — new convenience script (create)

## Risk

Low. The `confirm_photos` endpoint is well-tested (12 call sites in `test_api_endpoints.py`, 48 in `test_workflow.py`). The fix is purely additive — one POST call per location. Works in both mock and Temporal modes.
