# Plan: Fix E2E Tests + Add LiDAR Golden Path

## Context

The E2E test suite (`backend/tests/test_e2e.py`) is **currently broken** against the real Temporal backend. A `confirm_photos` endpoint was added to the workflow (requiring explicit user confirmation before advancing from "photos" to "scan" step), but the E2E tests were never updated. Every test that uploads photos and expects to advance past the "photos" step hangs on a poll timeout.

The server on port 8001 is already running with **all real AI services configured**: Anthropic, Gemini, Exa, R2, PostgreSQL, Temporal. No missing keys. No excuses.

Additionally, the existing golden path test (`test_full_pipeline_real_ai`) skips LiDAR. We need a LiDAR variant.

## Step 1: Fix `_create_project_with_photos` helper (line 311)

Add `confirm_photos` POST after the upload loop, before `_poll_step`:

```python
# After the upload loop:
r = await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
assert r.status_code == 200, f"confirm_photos failed: {r.status_code} {r.text}"
```

This cascades to ~20 tests that chain through this helper.

## Step 2: Fix `_advance_to_intake_with_scan` helper (line 701)

Same fix — add `confirm_photos` after uploads, before first `_poll_step`.

This fixes all `TestG3ScanDataFullPath` tests (LiDAR persistence in mock mode).

## Step 3: Fix inline photo uploads in 3 tests

| Test | Line | Where to add |
|------|------|-------------|
| `test_full_pipeline_real_ai` | ~2219 | After 2 photo uploads, before `_poll_step(scan)` |
| `test_rapid_photo_uploads` | ~2108 | After `asyncio.gather`, before `_poll_step(scan)` |
| `test_intake_with_inspiration_photo_context` | ~1718 | After room+inspiration uploads, before `_poll_step(scan)` |

## Step 4: Real LiDAR Data Capture Strategy

### Problem

The `_SCAN_DATA` in the test file and `reference_room.json` fixture are hand-crafted. We want to use **real RoomPlan data** from an actual device scan, but the user should only need to scan once.

### Solution: Capture-on-first-run, reuse forever

Add a **LiDAR fixture capture mechanism** to the backend scan endpoint:

1. **Fixture file path**: `backend/tests/fixtures/real_lidar_scan.json` (gitignored — real scan data stays local)

2. **Capture hook in scan endpoint** (`projects.py`): When the env var `CAPTURE_LIDAR_FIXTURE=true` is set, the `upload_scan` handler saves the raw `body` dict to the fixture path before processing. One-shot: if the file already exists, skip the save.

   ```python
   # At top of upload_scan(), after step check:
   if os.environ.get("CAPTURE_LIDAR_FIXTURE") and not _FIXTURE_PATH.exists():
       _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
       _FIXTURE_PATH.write_text(json.dumps(body, indent=2))
       logger.info("lidar_fixture_captured", path=str(_FIXTURE_PATH))
   ```

3. **E2E test loading**: `_SCAN_DATA` becomes a function that checks for `real_lidar_scan.json` first, falls back to the hand-crafted dict:

   ```python
   _FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_lidar_scan.json"

   def _get_scan_data() -> dict:
       """Load real LiDAR fixture if available, else use synthetic."""
       if _FIXTURE_PATH.exists():
           return json.loads(_FIXTURE_PATH.read_text())
       return _SYNTHETIC_SCAN_DATA  # the current hand-crafted dict
   ```

4. **Gitignore**: Add `backend/tests/fixtures/real_lidar_scan.json` to `.gitignore` so real device data isn't committed.

### One-time capture workflow (user does once)

```bash
# 1. Start backend with capture flag:
CAPTURE_LIDAR_FIXTURE=true E2E_BASE_URL=http://localhost:8001 uvicorn ...

# 2. On iOS device: scan any room (the app POSTs to /projects/{id}/scan)
#    Backend saves the raw JSON to backend/tests/fixtures/real_lidar_scan.json

# 3. Restart backend without the flag (or leave it — file won't be overwritten)

# 4. All subsequent E2E runs use the captured real data automatically
```

### Alternative: CLI capture (no env var needed)

Add a `scripts/capture-lidar.sh` that:
1. Creates a temp project via the API (`has_lidar=True`)
2. Uploads 2 dummy photos + confirms
3. Prints: "Open the app, scan a room, and point it at this project: {project_id}"
4. Polls the project until `scan_data` appears
5. Saves the raw scan JSON from the project state to the fixture file
6. Cleans up the project

This is cleaner but requires the iOS app to target a specific project ID. The env var approach is simpler.

## Step 5: Add `test_full_pipeline_real_ai_with_lidar`

New test inside `TestGoldenPathRealAI` class. Full LiDAR path:

1. Create project with `has_lidar=True`
2. Upload 2 room photos + confirm
3. Submit scan data (from `_get_scan_data()` — real if captured, synthetic fallback)
4. Real intake (Claude Opus) — 4 messages with LiDAR-aware content
5. Confirm brief → real generation (Gemini, 180s timeout)
6. Select option → real edit (Gemini, 120s timeout) with dimension-aware feedback
7. Approve → real shopping (Exa + Claude, 180s timeout)
8. Assertions at each step:
   - `scan_data` persists with correct dimensions
   - `room_context.enrichment_sources` includes `"lidar"` and `"photos"`
   - Shopping items are real (not mock), have prices
   - Generated/revised images are real URLs
9. Delete project

## Step 6: Create `scripts/run-e2e.sh` convenience script

Simple bash wrapper that:
- Checks backend health
- Runs pytest with proper `E2E_BASE_URL`
- Supports `--real-only` and `-k` filtering
- Detects if `real_lidar_scan.json` exists; if not, prints instructions for one-time capture

## Step 7: Run and verify

```bash
# Against existing server on 8001 (real AI mode):
E2E_BASE_URL=http://localhost:8001 .venv/bin/python -m pytest tests/test_e2e.py -x -v
```

Mock-mode tests (~70) should complete in ~2-3 minutes.
Real-AI tests (~20) take 5-10 minutes (generation ~60-120s, shopping ~30-60s each).
LiDAR golden path takes ~8-12 minutes.

## Files to modify

- `backend/tests/test_e2e.py` — confirm_photos fixes + new LiDAR test + `_get_scan_data()` loader
- `backend/app/api/routes/projects.py` — capture hook (3 lines, guarded by env var)
- `backend/tests/fixtures/real_lidar_scan.json` — captured by device (gitignored)
- `scripts/run-e2e.sh` — new convenience script
- `.gitignore` — add fixture path

## Verification

1. Run full suite against port 8001: all 90+ tests should pass
2. Specifically verify `TestGoldenPathRealAI::test_full_pipeline_real_ai` (skip-scan path)
3. Specifically verify `TestGoldenPathRealAI::test_full_pipeline_real_ai_with_lidar` (LiDAR path)
4. Verify mock-mode tests still pass (the `confirm_photos` endpoint works in both modes)
5. Verify fixture capture: set `CAPTURE_LIDAR_FIXTURE=true`, scan on device, confirm `real_lidar_scan.json` appears
6. Verify fixture reuse: unset env var, re-run LiDAR test, confirm it loads the captured file
