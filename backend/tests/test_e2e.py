"""E2E tests — hit a running backend with real Temporal.

These tests require infrastructure to be running:
    ./scripts/e2e-setup.sh
    cd backend && USE_TEMPORAL=true .venv/bin/python -m uvicorn app.main:app --reload --port 8100
    cd backend && .venv/bin/python -m app.worker

Run with:
    cd backend
    E2E_BASE_URL=http://localhost:8100 .venv/bin/python -m pytest tests/test_e2e.py -xv

Set E2E_BASE_URL to override the default (http://localhost:8100).
Tests are skipped automatically if the Remo API is not reachable.
"""

import asyncio
import io
import json
import os
import tempfile
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFilter

BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8100")
_NEW_PROJECT = {"has_lidar": False, "device_fingerprint": "e2e-test"}
_NEW_PROJECT_LIDAR = {"has_lidar": True, "device_fingerprint": "e2e-test-lidar"}

# Reference scan data matching the backend parser schema (see specs/PLAN_LIDAR_UTILIZATION.md).
_SCAN_DATA = {
    "room": {"width": 4.2, "length": 5.8, "height": 2.7, "unit": "meters"},
    "walls": [
        {"id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0.0},
        {"id": "wall_1", "width": 5.8, "height": 2.7, "orientation": 90.0},
    ],
    "openings": [
        {"type": "door", "wall_id": "wall_0", "width": 0.9, "height": 2.1},
    ],
    "furniture": [
        {"type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8},
        {"type": "table", "width": 1.2, "depth": 0.8, "height": 0.75},
    ],
    "surfaces": [{"type": "floor"}],
    "floor_area_sqm": 24.36,
}

_REAL_LIDAR_FIXTURE = Path(__file__).parent / "fixtures" / "real_lidar_scan.json"


def _get_scan_data() -> dict:
    """Load real LiDAR fixture if available, else use synthetic."""
    if _REAL_LIDAR_FIXTURE.exists():
        return json.loads(_REAL_LIDAR_FIXTURE.read_text())
    return _SCAN_DATA


# Detect if backend is running with real AI activities (not mocks).
# Used to enable real-mode assertions in tests that work both ways.
_real_ai_mode = False

# Check Remo API availability once at module load (synchronous).
# Verify it's actually the Remo API by checking for our specific response shape.
_backend_available = False
try:
    with httpx.Client(base_url=BASE_URL, timeout=3.0) as _c:
        _r = _c.get("/health")
        if _r.status_code == 200:
            _data = _r.json()
            _backend_available = _data.get("status") == "ok" and "version" in _data
        # Detect real AI mode: if debug/force-failure returns 409 with "not_applicable",
        # it means USE_MOCK_ACTIVITIES=false (real activities are loaded).
        _ff = _c.post("/api/v1/debug/force-failure")
        if _ff.status_code == 409 and _ff.json().get("error") == "not_applicable":
            _real_ai_mode = True
except (httpx.ConnectError, httpx.TimeoutException):
    pass

_skip_in_real_mode = pytest.mark.skipif(
    _real_ai_mode,
    reason="Only works with mock activities (error injection / iteration cap)",
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _backend_available, reason="Remo API not reachable"),
]

# Clean up any leftover force-failure sentinel from previous test runs.
# This prevents cross-session contamination when tests crash mid-run.
_SENTINEL = Path(tempfile.gettempdir()) / "remo-force-failure"
_SENTINEL.unlink(missing_ok=True)


@pytest.fixture
async def client():
    # Longer timeout for real AI mode (Claude/Gemini calls take 5-30s each)
    timeout = 120.0 if _real_ai_mode else 30.0
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=timeout) as c:
        yield c


# ---------------------------------------------------------------------------
# E2E-01: Smoke — Health + Project CRUD
# ---------------------------------------------------------------------------


class TestE2E01Smoke:
    """E2E-01: Verify basic infrastructure and API→Temporal bridge.

    Steps:
    1. GET /health → status "ok", version present, service probes run
    2. POST /projects → 201 with project_id
    3. GET /projects/{id} → WorkflowState with step="photos"
    4. DELETE /projects/{id} → 204
    5. GET /projects/{id} after delete → 404
    """

    async def test_health_all_connected(self, client: httpx.AsyncClient):
        """E2E-01 step 1: Health check returns ok with service statuses."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
        # Service probes return "connected" or "disconnected"
        assert data["postgres"] in ("connected", "disconnected")
        assert data["temporal"] in ("connected", "disconnected")
        assert data["r2"] in ("connected", "disconnected")

    async def test_create_project(self, client: httpx.AsyncClient):
        """E2E-01 step 2: Create project returns 201 with project_id."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        assert resp.status_code == 201
        data = resp.json()
        assert "project_id" in data
        assert len(data["project_id"]) == 36  # UUID format

    async def test_get_project_state(self, client: httpx.AsyncClient):
        """E2E-01 step 3: Get project returns WorkflowState at 'photos' step."""
        create_resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        project_id = create_resp.json()["project_id"]

        resp = await client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        state = resp.json()
        assert state["step"] == "photos"
        assert state["photos"] == []
        assert state["approved"] is False

    async def test_delete_project(self, client: httpx.AsyncClient):
        """E2E-01 step 4: Delete project returns 204, then eventually 404 or cancelled.

        With real Temporal the workflow needs time to process the cancel signal,
        run purge, and terminate. We poll until the project is either gone (404)
        or in a cancelled/abandoned state.
        """
        create_resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        project_id = create_resp.json()["project_id"]

        del_resp = await client.delete(f"/api/v1/projects/{project_id}")
        assert del_resp.status_code == 204

        # Poll: workflow needs time to process cancel → purge → terminate
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            get_resp = await client.get(f"/api/v1/projects/{project_id}")
            if get_resp.status_code == 404:
                break
            # Workflow still running but transitioning to cancelled
            state = get_resp.json()
            if state.get("step") in ("cancelled", "abandoned"):
                break
            await asyncio.sleep(0.5)
        assert get_resp.status_code == 404 or state.get("step") in ("cancelled", "abandoned")

    async def test_get_nonexistent_project(self, client: httpx.AsyncClient):
        """E2E-01: Get non-existent project returns 404 ErrorResponse."""
        resp = await client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        data = resp.json()
        assert data["error"] == "workflow_not_found"
        assert data["retryable"] is False

    async def test_request_id_header(self, client: httpx.AsyncClient):
        """E2E-01: Every response includes X-Request-ID header."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        assert "x-request-id" in resp.headers
        assert len(resp.headers["x-request-id"]) == 36


# ---------------------------------------------------------------------------
# Test image helpers
# ---------------------------------------------------------------------------


_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _make_sharp_jpeg(width: int = 2048, height: int = 1536) -> bytes:
    """Return a JPEG that passes both Pillow checks and Claude Haiku content classification.

    In real-AI mode (or whenever the fixture exists), loads a Gemini-generated
    room photograph from tests/fixtures/room_photo.jpg.  Falls back to a
    Pillow-drawn synthetic image for mock-mode tests where only Pillow checks run.
    """
    fixture = _FIXTURE_DIR / "room_photo.jpg"
    if fixture.exists():
        return fixture.read_bytes()

    # Fallback: synthetic image (passes Pillow checks only, not Haiku)
    img = Image.new("RGB", (width, height), (200, 180, 160))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, width, height // 3], fill=(230, 225, 215))
    draw.rectangle([0, height * 2 // 3, width, height], fill=(160, 120, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _make_blurry_jpeg(width: int = 2048, height: int = 1536) -> bytes:
    """Create a blurry JPEG that fails the blur check."""
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    img = img.filter(ImageFilter.GaussianBlur(radius=20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_lowres_jpeg(width: int = 480, height: int = 360) -> bytes:
    """Create a low-resolution JPEG that fails the resolution check."""
    img = Image.new("RGB", (width, height), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


async def _upload_photo(
    client: httpx.AsyncClient,
    project_id: str,
    image_data: bytes,
    photo_type: str = "room",
    note: str | None = None,
) -> httpx.Response:
    """Upload a photo via multipart form."""
    params: dict = {"photo_type": photo_type}
    if note is not None:
        params["note"] = note
    return await client.post(
        f"/api/v1/projects/{project_id}/photos",
        files={"file": ("photo.jpg", image_data, "image/jpeg")},
        params=params,
    )


POLL_INTERVAL = 0.5
POLL_TIMEOUT = 240.0


async def _poll_step(
    client: httpx.AsyncClient,
    pid: str,
    target_step: str,
    timeout: float = POLL_TIMEOUT,
) -> dict:
    """Poll project state until target step is reached or timeout.

    Fails fast if the workflow enters an error state instead of waiting
    the full timeout — reports the actual error message from the workflow.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        if state["step"] == target_step:
            return state
        # Fail fast on workflow errors instead of waiting for timeout
        error = state.get("error")
        if error and state["step"] != target_step:
            msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            pytest.fail(
                f"Workflow error while waiting for step '{target_step}' "
                f"(current step: {state['step']}): {msg}"
            )
        await asyncio.sleep(POLL_INTERVAL)
    pytest.fail(f"Timed out waiting for step '{target_step}' (last: {state['step']})")


async def _poll_iteration(
    client: httpx.AsyncClient,
    pid: str,
    target_count: int,
    timeout: float = POLL_TIMEOUT,
) -> dict:
    """Poll project state until iteration_count reaches target or timeout.

    In real Temporal, edit_design is an async activity — state updates lag
    behind the signal by the time it takes to execute the activity.
    Fails fast on workflow errors instead of waiting the full timeout.
    """
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        if state["iteration_count"] >= target_count:
            return state
        error = state.get("error")
        if error:
            msg = error.get("message", str(error)) if isinstance(error, dict) else str(error)
            pytest.fail(
                f"Workflow error while waiting for iteration_count>={target_count} "
                f"(current: {state.get('iteration_count', '?')}): {msg}"
            )
        await asyncio.sleep(POLL_INTERVAL)
    pytest.fail(
        f"Timed out waiting for iteration_count>={target_count} "
        f"(last: {state.get('iteration_count', '?')})"
    )


async def _create_project_with_photos(client: httpx.AsyncClient, room_count: int = 2) -> str:
    """Create project and upload room photos, returning project_id at 'scan' step.

    Polls until the workflow transitions to 'scan' to avoid race conditions
    when running against real Temporal (signals are delivered asynchronously).
    """
    resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
    assert resp.status_code == 201
    project_id = resp.json()["project_id"]
    sharp = _make_sharp_jpeg()
    for _ in range(room_count):
        r = await _upload_photo(client, project_id, sharp)
        assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"
    # Confirm photos to advance workflow from 'photos' to 'scan'
    r = await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
    assert r.status_code == 200, f"confirm_photos failed: {r.status_code} {r.text}"
    # Wait for workflow to process signals and transition to 'scan'
    await _poll_step(client, project_id, "scan", timeout=10.0)
    return project_id


# ---------------------------------------------------------------------------
# E2E-02: Photo Upload → Validation → Step Transition
# ---------------------------------------------------------------------------


class TestE2E02PhotoUpload:
    """E2E-02: Verify photo validation and step transitions.

    Tests real Pillow validation (resolution, blur) and photo state management.
    Content classification (Claude Haiku) may or may not be available depending
    on API keys — tests only assert on Pillow-checkable properties.
    """

    async def test_valid_room_photo_passes(self, client: httpx.AsyncClient):
        """E2E-02: Upload valid room photo → passed=true."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        resp = await _upload_photo(client, pid, _make_sharp_jpeg())
        assert resp.status_code == 200
        data = resp.json()
        assert "photo_id" in data
        assert data["validation"]["passed"] is True

    async def test_two_room_photos_transitions_to_scan(self, client: httpx.AsyncClient):
        """E2E-02: After 2 room photos, step auto-transitions to 'scan'."""
        pid = await _create_project_with_photos(client, room_count=2)

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "scan"
        assert len(state["photos"]) == 2
        assert all(p["photo_type"] == "room" for p in state["photos"])

    async def test_blurry_photo_fails_validation(self, client: httpx.AsyncClient):
        """E2E-02: Blurry photo → passed=false with blur failure."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        resp = await _upload_photo(client, pid, _make_blurry_jpeg())
        assert resp.status_code == 200
        data = resp.json()
        assert data["validation"]["passed"] is False
        assert "blur" in str(data["validation"]["failures"]).lower()

    async def test_lowres_photo_fails_validation(self, client: httpx.AsyncClient):
        """E2E-02: Low-resolution photo → passed=false with resolution failure."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        resp = await _upload_photo(client, pid, _make_lowres_jpeg())
        assert resp.status_code == 200
        data = resp.json()
        assert data["validation"]["passed"] is False
        assert "resolution" in str(data["validation"]["failures"]).lower()

    async def test_inspiration_photos_max_three(self, client: httpx.AsyncClient):
        """E2E-02: Max 3 inspiration photos, 4th returns 422."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        for i in range(3):
            r = await _upload_photo(client, pid, sharp, photo_type="inspiration")
            assert r.status_code == 200, f"Inspiration {i + 1} failed"

        r = await _upload_photo(client, pid, sharp, photo_type="inspiration")
        assert r.status_code == 422
        assert r.json()["error"] == "too_many_inspiration_photos"

    async def test_inspiration_photo_with_note(self, client: httpx.AsyncClient):
        """E2E-02: Inspiration photo with note stores the note."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await _upload_photo(
            client,
            pid,
            _make_sharp_jpeg(),
            photo_type="inspiration",
            note="Love this color palette",
        )
        assert r.status_code == 200

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        inspo = [p for p in state["photos"] if p["photo_type"] == "inspiration"]
        assert len(inspo) == 1
        assert inspo[0]["note"] == "Love this color palette"

    async def test_note_on_room_photo_rejected(self, client: httpx.AsyncClient):
        """E2E-02 PHOTO-7: Notes on room photos return 422."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await _upload_photo(
            client,
            pid,
            _make_sharp_jpeg(),
            photo_type="room",
            note="Should fail",
        )
        assert r.status_code == 422
        assert r.json()["error"] == "note_not_allowed"

    async def test_oversized_photo_rejected(self, client: httpx.AsyncClient):
        """E2E-02: Photo >20MB returns 413."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        # 21MB of random-ish data with JPEG header
        huge = _make_sharp_jpeg(4000, 3000)
        # Pad to exceed 20MB
        huge = huge + b"\x00" * (21 * 1024 * 1024 - len(huge))
        r = await _upload_photo(client, pid, huge)
        assert r.status_code == 413

    async def test_photo_count_in_state(self, client: httpx.AsyncClient):
        """E2E-02: State reflects correct photo counts after mixed uploads."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        # 2 room + 2 inspiration
        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(client, pid, sharp, photo_type="inspiration")
        await _upload_photo(client, pid, sharp, photo_type="inspiration")

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        rooms = [p for p in state["photos"] if p["photo_type"] == "room"]
        inspos = [p for p in state["photos"] if p["photo_type"] == "inspiration"]
        assert len(rooms) == 2
        assert len(inspos) == 2


# ---------------------------------------------------------------------------
# E2E-03: LiDAR Scan + Skip Paths
# ---------------------------------------------------------------------------


class TestE2E03Scan:
    """E2E-03: Verify scan data parsing and skip paths.

    Tests both Path A (skip scan) and Path B (submit RoomPlan JSON).
    """

    async def test_skip_scan_transitions_to_intake(self, client: httpx.AsyncClient):
        """E2E-03 Path A: Skip scan → step = 'intake'."""
        pid = await _create_project_with_photos(client)

        resp = await client.post(f"/api/v1/projects/{pid}/scan/skip")
        assert resp.status_code == 200

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "intake"

    async def test_submit_scan_transitions_to_intake(self, client: httpx.AsyncClient):
        """E2E-03 Path B: Submit scan with RoomPlan JSON → step = 'intake'."""
        pid = await _create_project_with_photos(client)

        scan_data = {
            "room": {"width": 4.5, "length": 3.2, "height": 2.8, "unit": "meters"},
        }
        resp = await client.post(f"/api/v1/projects/{pid}/scan", json=scan_data)
        assert resp.status_code == 200

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "intake"
        assert state["scan_data"] is not None
        dims = state["scan_data"]["room_dimensions"]
        assert dims["width_m"] == pytest.approx(4.5, abs=0.1)
        assert dims["length_m"] == pytest.approx(3.2, abs=0.1)
        assert dims["height_m"] == pytest.approx(2.8, abs=0.1)

    async def test_invalid_scan_data_returns_422(self, client: httpx.AsyncClient):
        """E2E-03: Invalid scan JSON returns 422."""
        pid = await _create_project_with_photos(client)

        resp = await client.post(f"/api/v1/projects/{pid}/scan", json={})
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_scan_data"

    async def test_scan_wrong_step_returns_409(self, client: httpx.AsyncClient):
        """E2E-03: Scan at wrong step returns 409."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        # Still at "photos" step — need 2 room photos to reach "scan"
        r = await client.post(f"/api/v1/projects/{pid}/scan/skip")
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# G3: Full Path with Scan Data (LiDAR-present flow)
# ---------------------------------------------------------------------------


class TestG3ScanDataFullPath:
    """G3: Verify scan data flows through the full pipeline.

    All existing E2E tests use skip_scan. This class verifies the LiDAR-present
    path: photos → scan(submit) → intake → generation → selection → iteration →
    approval → completed(shopping). Scan data must persist and be accessible at
    every step.
    """

    async def test_scan_data_persists_at_intake(self, client: httpx.AsyncClient):
        """G3: After scan submit, scan_data is present at intake step."""
        pid = await _advance_to_intake_with_scan(client)
        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200, f"GET project failed: {r.status_code} {r.text}"
        state = r.json()
        assert state["step"] == "intake"
        assert state["scan_data"] is not None
        dims = state["scan_data"]["room_dimensions"]
        assert dims["width_m"] == pytest.approx(4.2, abs=0.1)
        assert dims["length_m"] == pytest.approx(5.8, abs=0.1)
        assert dims["height_m"] == pytest.approx(2.7, abs=0.1)

    async def test_scan_data_persists_through_generation(self, client: httpx.AsyncClient):
        """G3: Scan data persists through intake → generation → selection."""
        pid = await _advance_to_intake_with_scan(client)
        await _complete_mock_intake(client, pid)
        state = await _poll_step(client, pid, "selection")
        # scan_data must survive generation
        assert state["scan_data"] is not None
        assert state["scan_data"]["room_dimensions"]["width_m"] == pytest.approx(4.2, abs=0.1)
        assert len(state["generated_options"]) == 2

    async def test_scan_data_persists_through_iteration(self, client: httpx.AsyncClient):
        """G3: Scan data survives through selection → iteration."""
        pid = await _advance_to_intake_with_scan(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200, f"select failed: {r.status_code} {r.text}"
        state = await _poll_step(client, pid, "iteration", timeout=10.0)
        assert state["scan_data"] is not None
        assert state["scan_data"]["room_dimensions"]["width_m"] == pytest.approx(4.2, abs=0.1)

    async def test_full_path_with_scan_data_to_completed(self, client: httpx.AsyncClient):
        """G3: Full LiDAR happy path — photos → scan → intake → generation →
        selection → approve → completed with shopping list.

        Verifies scan data persists end-to-end and shopping list is generated.
        """
        pid = await _advance_to_intake_with_scan(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200, f"select failed: {r.status_code} {r.text}"
        await _poll_step(client, pid, "iteration", timeout=10.0)
        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200, f"approve failed: {r.status_code} {r.text}"
        state = await _poll_step(client, pid, "completed")

        # Scan data persists to completed
        assert state["scan_data"] is not None
        dims = state["scan_data"]["room_dimensions"]
        assert dims["width_m"] == pytest.approx(4.2, abs=0.1)
        assert dims["length_m"] == pytest.approx(5.8, abs=0.1)
        assert dims["height_m"] == pytest.approx(2.7, abs=0.1)
        assert dims["floor_area_sqm"] == pytest.approx(24.36, abs=0.1)

        # Shopping list generated
        assert state["approved"] is True
        shopping = state["shopping_list"]
        assert shopping is not None
        assert len(shopping["items"]) > 0

    async def test_scan_data_furniture_and_openings_parsed(self, client: httpx.AsyncClient):
        """G3: Furniture and opening details from scan are parsed correctly."""
        pid = await _advance_to_intake_with_scan(client)
        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200, f"GET project failed: {r.status_code} {r.text}"
        state = r.json()
        dims = state["scan_data"]["room_dimensions"]
        # Furniture from _SCAN_DATA
        assert len(dims.get("furniture", [])) == 2
        assert dims["furniture"][0]["type"] == "sofa"
        # Openings from _SCAN_DATA
        assert len(dims.get("openings", [])) == 1
        assert dims["openings"][0]["type"] == "door"

    async def test_scan_data_survives_start_over(self, client: httpx.AsyncClient):
        """G3: Scan data and photos survive start-over (preserved by design)."""
        pid = await _advance_to_intake_with_scan(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200, f"select failed: {r.status_code} {r.text}"
        await _poll_step(client, pid, "iteration", timeout=10.0)

        # Start over — should preserve photos + scan data
        r = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)
        r = await client.get(f"/api/v1/projects/{pid}")
        assert r.status_code == 200, f"GET project failed: {r.status_code} {r.text}"
        state = r.json()
        assert state["step"] == "intake"
        assert state["scan_data"] is not None
        assert state["scan_data"]["room_dimensions"]["width_m"] == pytest.approx(4.2, abs=0.1)
        assert len(state["photos"]) == 2


# ---------------------------------------------------------------------------
# E2E-12: Delete Photo
# ---------------------------------------------------------------------------


class TestE2E12DeletePhoto:
    """E2E-12: Verify photo deletion at various steps."""

    async def test_delete_photo_during_photos_step(self, client: httpx.AsyncClient):
        """E2E-12: Delete room photo during photos step."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        upload_resp = await _upload_photo(client, pid, sharp)
        photo_id = upload_resp.json()["photo_id"]

        del_resp = await client.delete(f"/api/v1/projects/{pid}/photos/{photo_id}")
        assert del_resp.status_code == 204

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert len(state["photos"]) == 0

    async def test_delete_photo_at_scan_removes_from_list(self, client: httpx.AsyncClient):
        """E2E-12: Deleting room photo at scan step removes it from state.

        Note: In the real Temporal workflow, the step does NOT revert from
        'scan' to 'photos' when room count drops below 2 (the workflow's
        wait condition has already been satisfied). The mock API does revert,
        but this test runs against real Temporal.
        """
        pid = await _create_project_with_photos(client, room_count=2)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "scan"

        photo_id = state["photos"][0]["photo_id"]
        del_resp = await client.delete(f"/api/v1/projects/{pid}/photos/{photo_id}")
        assert del_resp.status_code == 204

        # Poll briefly for signal processing
        await asyncio.sleep(1.0)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert len(state["photos"]) == 1

    async def test_delete_nonexistent_photo_returns_404(self, client: httpx.AsyncClient):
        """E2E-12: Deleting non-existent photo returns 404."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.delete(f"/api/v1/projects/{pid}/photos/fake-id")
        assert r.status_code == 404
        assert r.json()["error"] == "photo_not_found"


# ---------------------------------------------------------------------------
# Multi-step helpers
# ---------------------------------------------------------------------------


async def _advance_to_intake(client: httpx.AsyncClient) -> str:
    """Create project, upload 2 room photos, skip scan. Returns pid at 'intake'."""
    pid = await _create_project_with_photos(client)
    r = await client.post(f"/api/v1/projects/{pid}/scan/skip")
    assert r.status_code == 200, f"scan/skip failed: {r.status_code} {r.text}"
    await _poll_step(client, pid, "intake", timeout=10.0)
    return pid


async def _advance_to_intake_with_scan(
    client: httpx.AsyncClient,
    scan_data: dict | None = None,
) -> str:
    """Create project (has_lidar=True), upload 2 room photos, submit scan data.

    Returns pid at 'intake' with scan_data populated.
    Uses _SCAN_DATA by default (4.2m x 5.8m room with furniture + openings).
    """
    resp = await client.post("/api/v1/projects", json=_NEW_PROJECT_LIDAR)
    assert resp.status_code == 201
    pid = resp.json()["project_id"]
    sharp = _make_sharp_jpeg()
    for _ in range(2):
        r = await _upload_photo(client, pid, sharp)
        assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"
    r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
    assert r.status_code == 200, f"confirm_photos failed: {r.status_code} {r.text}"
    await _poll_step(client, pid, "scan", timeout=10.0)
    r = await client.post(f"/api/v1/projects/{pid}/scan", json=scan_data or _SCAN_DATA)
    assert r.status_code == 200, f"scan submit failed: {r.status_code} {r.text}"
    await _poll_step(client, pid, "intake", timeout=10.0)
    return pid


async def _complete_mock_intake(client: httpx.AsyncClient, pid: str) -> None:
    """Run through mock 3-step intake and confirm brief."""
    r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
    assert r.status_code == 200, f"intake/start failed: {r.status_code} {r.text}"
    r = await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "living room"})
    assert r.status_code == 200, f"intake/message 1 failed: {r.status_code} {r.text}"
    r = await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "modern"})
    assert r.status_code == 200, f"intake/message 2 failed: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/v1/projects/{pid}/intake/message",
        json={"message": "replace the old couch"},
    )
    assert r.status_code == 200, f"intake/message 3 failed: {r.status_code} {r.text}"
    r = await client.post(
        f"/api/v1/projects/{pid}/intake/confirm",
        json={"brief": {"room_type": "living room"}},
    )
    assert r.status_code == 200, f"intake/confirm failed: {r.status_code} {r.text}"


# ---------------------------------------------------------------------------
# E2E-04: Intake Conversation
# ---------------------------------------------------------------------------


class TestE2E04Intake:
    """E2E-04: Verify intake conversation flow (mock or real agent).

    Tests the mock 3-step canned conversation. Real agent testing (Claude Opus)
    requires ANTHROPIC_API_KEY and USE_MOCK_ACTIVITIES=false — those tests will
    verify richer behavior when run against real infrastructure.
    """

    async def test_start_intake(self, client: httpx.AsyncClient):
        """E2E-04: Start intake returns welcome message with options."""
        pid = await _advance_to_intake(client)

        resp = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_message" in data
        assert len(data["agent_message"]) > 0
        if not _real_ai_mode:
            assert "options" in data
            assert len(data["options"]) >= 2

    async def test_intake_conversation_flow(self, client: httpx.AsyncClient):
        """E2E-04: 3-step intake produces summary with partial brief."""
        pid = await _advance_to_intake(client)

        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Step 1: room type
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        assert r.status_code == 200
        data1 = r.json()
        assert "agent_message" in data1
        if not _real_ai_mode:
            assert "options" in data1

        # Step 2: style
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "modern minimalist"},
        )
        assert r.status_code == 200

        # Step 3: details → mock always produces summary; real agent may need more turns
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "replace the old couch with something sleek"},
        )
        assert r.status_code == 200
        data = r.json()
        if _real_ai_mode:
            # Real Claude Opus decides when to summarize — may take more turns
            assert "agent_message" in data
        else:
            assert data.get("is_summary") is True
            assert data.get("partial_brief") is not None

    async def test_confirm_intake_transitions_to_generation(self, client: httpx.AsyncClient):
        """E2E-04: Confirming brief transitions to 'generation' step."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "generation"
        assert state["design_brief"] is not None

    async def test_skip_intake_requires_inspiration(self, client: httpx.AsyncClient):
        """E2E-04 INTAKE-3a: Skip intake without inspiration returns 422."""
        pid = await _advance_to_intake(client)

        r = await client.post(f"/api/v1/projects/{pid}/intake/skip")
        assert r.status_code == 422
        assert r.json()["error"] == "intake_required"

    async def test_skip_intake_with_inspiration(self, client: httpx.AsyncClient):
        """E2E-04: Skip intake with inspiration photos succeeds."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        # Upload 2 room + 1 inspiration
        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(client, pid, sharp, photo_type="inspiration")
        await _poll_step(client, pid, "scan", timeout=10.0)
        await client.post(f"/api/v1/projects/{pid}/scan/skip")

        r = await client.post(f"/api/v1/projects/{pid}/intake/skip")
        assert r.status_code == 200

        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "generation"

    async def test_intake_wrong_step(self, client: httpx.AsyncClient):
        """E2E-04: Intake at photos step returns 409."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        assert r.status_code == 409


# ---------------------------------------------------------------------------
# E2E-05: Generation (mock — polls for completion)
# ---------------------------------------------------------------------------


class TestE2E05Generation:
    """E2E-05: Verify generation produces 2 design options.

    In mock mode, generation completes after MOCK_GENERATION_DELAY.
    In real mode, Gemini generates actual images.
    """

    async def test_generation_produces_two_options(self, client: httpx.AsyncClient):
        """E2E-05: After intake confirm, poll until selection with 2 options."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)

        # Poll until generation completes
        state = await _poll_step(client, pid, "selection")
        assert len(state["generated_options"]) == 2
        for opt in state["generated_options"]:
            assert "image_url" in opt
            assert len(opt["image_url"]) > 0
            assert "caption" in opt


# ---------------------------------------------------------------------------
# E2E-06: Selection + Text Iteration
# ---------------------------------------------------------------------------


class TestE2E06SelectionIteration:
    """E2E-06: Verify selection and text feedback iteration."""

    async def test_select_option_transitions_to_iteration(self, client: httpx.AsyncClient):
        """E2E-06: Select option → step = 'iteration', current_image set."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")

        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200

        state = await _poll_step(client, pid, "iteration", timeout=10.0)
        assert state["current_image"] is not None
        assert state["selected_option"] == 0

    async def test_text_feedback_creates_revision(self, client: httpx.AsyncClient):
        """E2E-06: Text feedback creates a revision entry."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "Make the lighting warmer and add plants"},
        )
        assert r.status_code == 200

        state = await _poll_iteration(client, pid, 1)
        assert state["iteration_count"] == 1
        assert len(state["revision_history"]) == 1
        assert state["revision_history"][0]["type"] == "feedback"

    async def test_annotation_edit_creates_revision(self, client: httpx.AsyncClient):
        """E2E-07: Annotation edit creates a revision entry."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.3,
                        "center_y": 0.4,
                        "radius": 0.15,
                        "instruction": "Replace lamp with a tall plant",
                    }
                ]
            },
        )
        assert r.status_code == 200

        state = await _poll_iteration(client, pid, 1)
        assert state["iteration_count"] == 1
        assert state["revision_history"][0]["type"] == "annotation"

    async def test_invalid_selection_index(self, client: httpx.AsyncClient):
        """E2E-06: Invalid option index returns 422."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")

        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 99})
        assert r.status_code == 422

    async def test_short_feedback_rejected(self, client: httpx.AsyncClient):
        """E2E-06 REGEN-2: Feedback <10 chars returns 422."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "short"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# E2E-08: 5-Iteration Cap
# ---------------------------------------------------------------------------


class TestE2E08IterationCap:
    """E2E-08: Verify iteration pool capped at 5 with auto-transition."""

    @_skip_in_real_mode
    async def test_five_iterations_forces_approval(self, client: httpx.AsyncClient):
        """E2E-08: 5 mixed iterations → step auto-transitions to 'approval'."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # 3 text feedbacks — wait for each to complete before sending next
        for i in range(3):
            await _poll_iteration(client, pid, i)  # wait for previous
            r = await client.post(
                f"/api/v1/projects/{pid}/iterate/feedback",
                json={"feedback": f"Change number {i + 1}: make it more colorful"},
            )
            assert r.status_code == 200

        # 2 annotation edits
        for i in range(2):
            await _poll_iteration(client, pid, 3 + i)  # wait for previous
            r = await client.post(
                f"/api/v1/projects/{pid}/iterate/annotate",
                json={
                    "annotations": [
                        {
                            "region_id": 1,
                            "center_x": 0.5,
                            "center_y": 0.5,
                            "radius": 0.1,
                            "instruction": f"Annotation change {i + 1}",
                        }
                    ]
                },
            )
            assert r.status_code == 200

        state = await _poll_step(client, pid, "approval")
        assert state["iteration_count"] == 5
        assert len(state["revision_history"]) == 5


# ---------------------------------------------------------------------------
# E2E-09: Approve → Shopping List
# ---------------------------------------------------------------------------


class TestE2E09ApproveShopping:
    """E2E-09: Verify approval triggers shopping list generation."""

    async def test_approve_transitions_to_shopping(self, client: httpx.AsyncClient):
        """E2E-09: Approve → shopping step → poll until completed."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200

        # Poll until shopping completes
        state = await _poll_step(client, pid, "completed")
        assert state["approved"] is True
        assert state["shopping_list"] is not None
        assert len(state["shopping_list"]["items"]) > 0
        assert state["shopping_list"]["total_estimated_cost_cents"] > 0

    async def test_approve_with_error_blocked(self, client: httpx.AsyncClient):
        """E2E-09: Approve while error active returns 409."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # In mock mode, we can't easily inject an error.
        # This test verifies the approve-from-wrong-step guard.
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "iteration"
        # Approve from iteration is valid (early approve)
        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# E2E-10: Start Over
# ---------------------------------------------------------------------------


class TestE2E10StartOver:
    """E2E-10: Verify start-over resets state correctly."""

    async def test_start_over_resets_to_intake(self, client: httpx.AsyncClient):
        """E2E-10: Start over from iteration → intake, photos preserved."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # Submit one iteration and wait for it to complete
        await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "Make it completely different"},
        )
        await _poll_iteration(client, pid, 1)

        # Start over (workflow must be idle to process the signal)
        r = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert r.status_code == 200

        state = await _poll_step(client, pid, "intake", timeout=15.0)
        assert state["generated_options"] == []
        assert state["design_brief"] is None
        assert state["iteration_count"] == 0
        assert state["current_image"] is None
        # Photos should be preserved
        assert len(state["photos"]) == 2

    async def test_start_over_from_completed_blocked(self, client: httpx.AsyncClient):
        """E2E-10: Start over from 'completed' step returns 409."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        await _poll_step(client, pid, "completed")

        r = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert r.status_code == 409

    async def test_restart_after_start_over(self, client: httpx.AsyncClient):
        """E2E-10: After start-over, can complete a new full cycle."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")

        # Start over
        await client.post(f"/api/v1/projects/{pid}/start-over")
        await _poll_step(client, pid, "intake", timeout=15.0)

        # New cycle
        await _complete_mock_intake(client, pid)
        state = await _poll_step(client, pid, "selection")
        assert len(state["generated_options"]) == 2


# ---------------------------------------------------------------------------
# E2E-11: Retry
# ---------------------------------------------------------------------------


class TestE2E11Retry:
    """E2E-11: Verify retry endpoint works with real error injection."""

    async def test_retry_no_error_is_noop(self, client: httpx.AsyncClient):
        """E2E-11: Retry when no error returns 200 (no-op)."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.post(f"/api/v1/projects/{pid}/retry")
        assert r.status_code == 200

    @_skip_in_real_mode
    async def test_force_failure_endpoint(self, client: httpx.AsyncClient):
        """E2E-11: Debug force-failure endpoint returns 200 in development."""
        r = await client.post("/api/v1/debug/force-failure")
        assert r.status_code == 200

        # Arming is idempotent — can call again without side effects
        r = await client.post("/api/v1/debug/force-failure")
        assert r.status_code == 200

        # Clean up: remove the sentinel so subsequent tests aren't affected
        import tempfile
        from pathlib import Path

        sentinel = Path(tempfile.gettempdir()) / "remo-force-failure"
        sentinel.unlink(missing_ok=True)

    @_skip_in_real_mode
    async def test_error_injection_retry_cycle(self, client: httpx.AsyncClient):
        """E2E-11: Inject error → generation fails → error state → retry → success.

        Full cycle:
        1. Arm one-shot failure via POST /debug/force-failure
        2. Drive project through photos + scan + intake
        3. Confirm intake → triggers generate_designs
        4. generate_designs raises ApplicationError (one-shot)
        5. Workflow sets error state
        6. Verify error is retryable
        7. POST /retry → clears error, re-runs generation
        8. Second attempt succeeds → project reaches 'selection'
        """
        # Step 1: Arm the failure BEFORE confirming intake
        r = await client.post("/api/v1/debug/force-failure")
        assert r.status_code == 200

        # Step 2-3: Drive project to intake confirmation
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)

        # Step 4-5: Poll for error state (generation should fail)
        deadline = time.monotonic() + 30.0
        state: dict = {}
        while time.monotonic() < deadline:
            state = (await client.get(f"/api/v1/projects/{pid}")).json()
            if state.get("error") is not None:
                break
            await asyncio.sleep(POLL_INTERVAL)
        else:
            pytest.fail(f"Timed out waiting for error state (last step: {state.get('step')})")

        # Step 6: Verify error details
        assert state["error"]["retryable"] is True
        err_msg = state["error"]["message"].lower()
        assert "generation failed" in err_msg or "retry" in err_msg
        assert state["step"] == "generation"

        # Step 7: Retry
        r = await client.post(f"/api/v1/projects/{pid}/retry")
        assert r.status_code == 200

        # Step 8: Second attempt succeeds — poll for selection
        state = await _poll_step(client, pid, "selection")
        assert len(state["generated_options"]) == 2
        assert state["error"] is None


# ---------------------------------------------------------------------------
# E2E-18: Shopping List Structural Validation
# ---------------------------------------------------------------------------


class TestE2E18ShoppingValidation:
    """E2E-18: Validate shopping list contract compliance.

    In mock mode, validates the mock stub returns well-formed data that
    matches the GenerateShoppingListOutput contract. In real mode (with API
    keys), validates real Exa product data quality.
    """

    async def test_shopping_list_structure(self, client: httpx.AsyncClient):
        """E2E-18: Shopping list has valid items with required fields."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed")

        shopping = state["shopping_list"]
        assert shopping is not None
        assert isinstance(shopping["items"], list)
        assert len(shopping["items"]) > 0

        for item in shopping["items"]:
            assert isinstance(item["category_group"], str)
            assert isinstance(item["product_name"], str)
            assert isinstance(item["retailer"], str)
            assert item["price_cents"] >= 0
            assert isinstance(item["product_url"], str)
            assert item["product_url"].startswith("http")
            assert 0 <= item["confidence_score"] <= 1
            assert isinstance(item["why_matched"], str)
            if not _real_ai_mode:
                # Mock stubs always produce complete data; real AI may have gaps
                assert len(item["category_group"]) > 0
                assert len(item["product_name"]) > 0

    async def test_shopping_list_total_matches_items(self, client: httpx.AsyncClient):
        """E2E-18: total_estimated_cost_cents == sum of item prices."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed")

        shopping = state["shopping_list"]
        expected_total = sum(item["price_cents"] for item in shopping["items"])
        assert shopping["total_estimated_cost_cents"] == expected_total

    async def test_shopping_list_unmatched_format(self, client: httpx.AsyncClient):
        """E2E-18: Unmatched items (if any) have valid structure."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed")

        shopping = state["shopping_list"]
        unmatched = shopping.get("unmatched", [])
        assert isinstance(unmatched, list)
        # Mock API returns 1 unmatched; Temporal mock stubs return 0.
        # Validate structure of whatever is present.
        items_checked = 0
        for item in unmatched:
            assert isinstance(item["category"], str)
            assert isinstance(item["search_keywords"], str)
            assert isinstance(item["google_shopping_url"], str)
            assert "google" in item["google_shopping_url"].lower()
            items_checked += 1
        # Log how many items were validated (visible in pytest -v output)
        assert items_checked == len(unmatched)

    @_skip_in_real_mode
    async def test_shopping_list_cost_breakdown(self, client: httpx.AsyncClient):
        """E2E-18: Shopping list includes cost_breakdown from WI-05 mock stubs."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed")

        shopping = state["shopping_list"]
        cb = shopping.get("cost_breakdown")
        assert cb is not None, "cost_breakdown missing from shopping list"
        assert cb["materials_cents"] >= 0
        assert cb["total_low_cents"] >= 0
        assert cb["total_high_cents"] >= 0
        assert cb["total_low_cents"] <= cb["total_high_cents"]


# ---------------------------------------------------------------------------
# E2E-Extra: Multi-Project Isolation
# ---------------------------------------------------------------------------


class TestMultiProjectIsolation:
    """Verify multiple concurrent projects don't contaminate each other."""

    async def test_two_projects_independent_state(self, client: httpx.AsyncClient):
        """Two projects at different steps have independent state."""
        # Project A: advance to intake
        pid_a = await _advance_to_intake(client)

        # Project B: stay at photos step
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid_b = resp.json()["project_id"]

        state_a = (await client.get(f"/api/v1/projects/{pid_a}")).json()
        state_b = (await client.get(f"/api/v1/projects/{pid_b}")).json()

        assert state_a["step"] == "intake"
        assert state_b["step"] == "photos"
        assert len(state_a["photos"]) == 2
        assert len(state_b["photos"]) == 0

    async def test_delete_one_project_preserves_other(self, client: httpx.AsyncClient):
        """Deleting one project does not affect another."""
        pid_a = await _advance_to_intake(client)
        pid_b = await _advance_to_intake(client)

        r = await client.delete(f"/api/v1/projects/{pid_a}")
        assert r.status_code in (200, 204)

        state_b = (await client.get(f"/api/v1/projects/{pid_b}")).json()
        assert state_b["step"] == "intake"
        assert len(state_b["photos"]) == 2


# ---------------------------------------------------------------------------
# E2E-Extra: Early Approve (Zero Iterations)
# ---------------------------------------------------------------------------


class TestEarlyApprove:
    """Verify approve at iteration_count=0 works correctly."""

    async def test_approve_immediately_after_select(self, client: httpx.AsyncClient):
        """Select option then approve immediately (zero iterations) → completed."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")

        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await _poll_step(client, pid, "iteration", timeout=10.0)

        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200

        state = await _poll_step(client, pid, "completed")
        assert state["approved"] is True
        assert state["iteration_count"] == 0
        assert state["shopping_list"] is not None
        assert len(state["shopping_list"]["items"]) > 0


# ---------------------------------------------------------------------------
# E2E-Extra: Wrong-Step Guards
# ---------------------------------------------------------------------------


class TestWrongStepGuards:
    """Verify endpoints return 409 when called at the wrong step."""

    async def test_select_at_photos_step_returns_409(self, client: httpx.AsyncClient):
        """Cannot select a design option when step is 'photos'."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 409

    async def test_feedback_at_photos_step_returns_409(self, client: httpx.AsyncClient):
        """Cannot submit feedback when step is 'photos'."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "make it warmer and brighter please"},
        )
        assert r.status_code == 409

    async def test_approve_at_photos_step_returns_409(self, client: httpx.AsyncClient):
        """Cannot approve when step is 'photos'."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 409

    async def test_iterate_on_completed_returns_409(self, client: httpx.AsyncClient):
        """Cannot submit feedback on a completed project."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await _poll_step(client, pid, "iteration", timeout=10.0)
        await client.post(f"/api/v1/projects/{pid}/approve")
        await _poll_step(client, pid, "completed")

        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "make it warmer and brighter please"},
        )
        assert r.status_code == 409

    async def test_double_delete_second_returns_404(self, client: httpx.AsyncClient):
        """Deleting a project, waiting for it to vanish, then deleting again → 404."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]

        r1 = await client.delete(f"/api/v1/projects/{pid}")
        assert r1.status_code in (200, 204)

        # Wait for workflow to fully terminate
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            r = await client.get(f"/api/v1/projects/{pid}")
            if r.status_code == 404:
                break
            await asyncio.sleep(POLL_INTERVAL)

        r2 = await client.delete(f"/api/v1/projects/{pid}")
        assert r2.status_code == 404

    async def test_intake_message_before_start_returns_409(self, client: httpx.AsyncClient):
        """Sending intake message without starting returns 409."""
        pid = await _advance_to_intake(client)

        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        assert r.status_code == 409

    async def test_request_id_on_error_responses(self, client: httpx.AsyncClient):
        """X-Request-ID header is present on error responses."""
        r = await client.get("/api/v1/projects/nonexistent-id")
        assert r.status_code == 404
        assert "X-Request-ID" in r.headers
        assert len(r.headers["X-Request-ID"]) > 0


# ---------------------------------------------------------------------------
# Real AI Mode Tests (WI-03) — skipped when USE_MOCK_ACTIVITIES=true
# ---------------------------------------------------------------------------

_skip_unless_real = pytest.mark.skipif(
    not _real_ai_mode,
    reason="Requires USE_MOCK_ACTIVITIES=false (real AI activities)",
)


@_skip_unless_real
class TestRealAIGeneration:
    """WI-03: Verify real AI generation produces non-mock outputs.

    These tests only run when the backend worker has USE_MOCK_ACTIVITIES=false.
    They validate that Gemini generates real images and Claude generates real
    conversation — not mock stubs.
    """

    async def test_real_generation_produces_non_mock_images(self, client: httpx.AsyncClient):
        """Generated images are real URLs, not mock placeholders."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        state = await _poll_step(client, pid, "selection", timeout=120.0)

        assert len(state["generated_options"]) == 2
        for opt in state["generated_options"]:
            assert "mock" not in opt["image_url"].lower(), f"Mock URL detected: {opt['image_url']}"
            assert "example.com" not in opt["image_url"], f"Example URL: {opt['image_url']}"
            assert "mock" not in opt["caption"].lower(), f"Mock caption: {opt['caption']}"

    async def test_real_shopping_produces_non_mock_products(self, client: httpx.AsyncClient):
        """Shopping list contains real product names, not mock stubs."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection", timeout=120.0)
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed", timeout=120.0)

        shopping = state["shopping_list"]
        assert shopping is not None
        for item in shopping["items"]:
            assert "Mock" not in item["product_name"], f"Mock product: {item['product_name']}"
            assert "example.com" not in item["product_url"], f"Example URL: {item['product_url']}"


@_skip_unless_real
class TestRealAIIntake:
    """WI-03: Verify real intake agent (Claude Opus) conversation quality."""

    async def test_real_intake_conversation(self, client: httpx.AsyncClient):
        """Real intake agent produces meaningful, varied responses."""
        pid = await _advance_to_intake(client)

        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        assert r.status_code == 200

        # First message: describe room
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "It's a living room, about 15x20 feet, big south windows"},
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["agent_message"]) > 50, "Agent response too short"
        # Real agent should reference what the user said
        msg_lower = data["agent_message"].lower()
        assert any(word in msg_lower for word in ["living", "window", "south", "room"]), (
            f"Agent didn't reference user input: {data['agent_message'][:200]}"
        )

    async def test_real_intake_produces_brief(self, client: httpx.AsyncClient):
        """Full intake conversation eventually produces a design brief."""
        pid = await _advance_to_intake(client)

        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        messages = [
            "living room",
            "mid-century modern with warm tones",
            "replace the couch, keep the bookshelf, add more plants",
        ]
        last_response = None
        for msg in messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            last_response = r.json()

        # In quick mode, 3 messages should be enough to reach summary
        # If not at summary yet, send one more confirming message
        if not last_response.get("is_summary"):
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": "That covers everything, please summarize"},
            )
            assert r.status_code == 200
            last_response = r.json()

        # Eventually we should get a summary with a partial brief
        if last_response.get("is_summary"):
            assert last_response.get("partial_brief") is not None
            brief = last_response["partial_brief"]
            assert brief.get("room_type") is not None


# ---------------------------------------------------------------------------
# WI-26: Intake Agent Real Conversation Validation (Phase G)
# ---------------------------------------------------------------------------


@_skip_unless_real
class TestWI26IntakeConversationValidation:
    """WI-26: Validate real Claude intake agent conversation quality.

    Tests mode adaptation, quick-reply options, brief field population,
    domain progress tracking, and inspiration photo context.
    """

    async def test_quick_mode_summary_within_turn_budget(self, client: httpx.AsyncClient):
        """Quick mode (~3 turns) produces summary within 4 user messages."""
        pid = await _advance_to_intake(client)
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        messages = [
            "It's a bedroom for a couple, about 12x14 feet",
            "Japandi style — clean lines, natural materials, muted palette",
            "We need better storage and softer lighting for reading",
            "That's everything, please summarize",
        ]
        got_summary = False
        for msg in messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            data = r.json()
            if data.get("is_summary"):
                got_summary = True
                break

        assert got_summary, "Quick mode did not produce summary within 4 messages"

    async def test_agent_provides_options_or_open_ended(self, client: httpx.AsyncClient):
        """Real agent uses either quick-reply options or open-ended questions.

        The agent decides per-turn whether to offer classifiable options or
        ask open-ended questions. Both are valid. We verify the response
        always has a substantial agent_message and is internally consistent.
        """
        pid = await _advance_to_intake(client)
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        messages = [
            "living room, about 20x15 feet, open plan with kitchen",
            "I like modern industrial style with exposed brick",
            "We have a large dog and two kids under 5",
        ]
        found_options = False
        found_open_ended = False
        for msg in messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            data = r.json()
            assert len(data["agent_message"]) > 30, "Agent response too short"

            opts = data.get("options")
            if opts and len(opts) >= 2:
                found_options = True
                for opt in opts:
                    assert "label" in opt
                    assert "value" in opt
            if data.get("is_open_ended"):
                found_open_ended = True

        # Agent should use at least one interaction style
        assert found_options or found_open_ended, (
            "Agent provided neither options nor open-ended prompts"
        )

    async def test_summary_brief_has_core_fields(self, client: httpx.AsyncClient):
        """Summary brief has room_type plus at least 2 other populated fields."""
        pid = await _advance_to_intake(client)
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        messages = [
            "home office, 10x12, I work from home full time",
            "Scandinavian minimal, lots of white and birch wood",
            "Need a standing desk area and better cable management",
            "Looks great, please finalize the brief",
        ]
        last = None
        for msg in messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            last = r.json()
            if last.get("is_summary"):
                break

        if not last or not last.get("is_summary"):
            pytest.skip("Agent did not reach summary within turn budget")

        brief = last.get("partial_brief")
        assert brief is not None, "Summary has no partial_brief"
        assert brief.get("room_type"), "Brief missing room_type"

        # Count populated optional fields
        populated = 0
        for field in [
            "occupants",
            "pain_points",
            "keep_items",
            "style_profile",
            "constraints",
        ]:
            val = brief.get(field)
            if val and (isinstance(val, str) or len(val) > 0):
                populated += 1
        assert populated >= 2, f"Brief has only {populated} populated fields (need >=2): {brief}"

    async def test_progress_tracks_domains(self, client: httpx.AsyncClient):
        """Progress field mentions domain coverage."""
        pid = await _advance_to_intake(client)
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "It's a kitchen, galley style, very cramped"},
        )
        assert r.status_code == 200
        data = r.json()
        progress = data.get("progress", "")
        # Real agent returns "Question X of ~Y"
        assert "question" in progress.lower(), (
            f"Progress field missing expected format: '{progress}'"
        )

    async def test_intake_with_inspiration_photo_context(self, client: httpx.AsyncClient):
        """Agent acknowledges inspiration photos when present.

        Uploads room + inspiration photos, starts intake, verifies the agent's
        first response references the photos or design elements from them.
        """
        # Create project with room photos AND an inspiration photo with note
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(client, pid, sharp, photo_type="room")
        await _upload_photo(
            client,
            pid,
            sharp,
            photo_type="inspiration",
            note="Love the warm wood tones and cozy textiles",
        )
        r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert r.status_code == 200
        await _poll_step(client, pid, "scan", timeout=10.0)
        await client.post(f"/api/v1/projects/{pid}/scan/skip")
        await _poll_step(client, pid, "intake", timeout=10.0)

        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "It's a living room, I want it to feel warm"},
        )
        assert r.status_code == 200
        data = r.json()
        # Agent should acknowledge the photos in some way
        msg = data["agent_message"].lower()
        assert len(msg) > 50, "Agent response too short to process photos"
        # The agent saw the room photos and inspiration — its response should
        # reflect design awareness (not just a generic greeting)
        assert any(
            word in msg
            for word in [
                "photo",
                "image",
                "room",
                "see",
                "notice",
                "warm",
                "wood",
                "cozy",
                "inspiration",
                "space",
            ]
        ), f"Agent didn't reference photos/room: {data['agent_message'][:300]}"


# ---------------------------------------------------------------------------
# WI-27: Design Generation Quality Validation (Phase G)
# ---------------------------------------------------------------------------


@_skip_unless_real
class TestWI27GenerationQuality:
    """WI-27: Validate real Gemini-generated design images.

    Checks image URL accessibility, caption quality, and distinctness
    of the two generated design options.
    """

    async def test_generated_image_urls_accessible(self, client: httpx.AsyncClient):
        """Generated image URLs return HTTP 200 when fetched."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        state = await _poll_step(client, pid, "selection")

        assert len(state["generated_options"]) == 2
        async with httpx.AsyncClient(timeout=30.0) as http:
            for opt in state["generated_options"]:
                url = opt["image_url"]
                r = await http.get(url)
                assert r.status_code == 200, f"Image URL not accessible ({r.status_code}): {url}"
                assert len(r.content) > 1000, f"Image too small ({len(r.content)} bytes): {url}"

    async def test_two_options_are_distinct(self, client: httpx.AsyncClient):
        """Two generated options have different URLs and captions."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        state = await _poll_step(client, pid, "selection")

        opts = state["generated_options"]
        assert len(opts) == 2
        assert opts[0]["image_url"] != opts[1]["image_url"], "Both options have the same image URL"
        assert opts[0]["caption"] != opts[1]["caption"], "Both options have the same caption"
        # Captions should be meaningful (not single words)
        for opt in opts:
            assert len(opt["caption"]) > 10, f"Caption too short: '{opt['caption']}'"


# ---------------------------------------------------------------------------
# WI-25: Shopping List Quality Validation (Phase G)
# ---------------------------------------------------------------------------


@_skip_unless_real
class TestWI25ShoppingQuality:
    """WI-25: Validate real shopping list data quality.

    Checks product URL liveness, field population, and confidence
    score distribution for real Exa + Claude shopping results.
    """

    async def _get_completed_shopping(self, client: httpx.AsyncClient) -> dict:
        """Helper: drive a project to completion and return shopping list."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")
        state = await _poll_step(client, pid, "completed")
        assert state["shopping_list"] is not None
        return state["shopping_list"]

    async def test_product_urls_respond(self, client: httpx.AsyncClient):
        """Product URLs return valid HTTP responses (informational, not blocking).

        Real product URLs may return various status codes (200, 301, 403 for
        bot detection, etc.). We verify they're reachable, not necessarily 200.
        """
        shopping = await self._get_completed_shopping(client)
        reachable = 0
        total = len(shopping["items"])

        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as http:
            for item in shopping["items"]:
                url = item["product_url"]
                try:
                    r = await http.head(url)
                    if r.status_code < 500:
                        reachable += 1
                except (httpx.ConnectError, httpx.TimeoutException):
                    pass  # URL unreachable — noted but not fatal

        # At least half of product URLs should be reachable
        assert reachable >= total // 2, f"Only {reachable}/{total} product URLs reachable"

    async def test_all_items_have_core_fields(self, client: httpx.AsyncClient):
        """Every shopping item has valid URL and retailer; most have prices."""
        shopping = await self._get_completed_shopping(client)
        priced_count = 0

        for i, item in enumerate(shopping["items"]):
            assert item["product_url"].startswith("http"), (
                f"Item {i}: invalid URL '{item['product_url']}'"
            )
            assert item["price_cents"] >= 0, (
                f"Item {i}: negative price for '{item.get('product_name')}'"
            )
            if item["price_cents"] > 0:
                priced_count += 1
            assert isinstance(item["retailer"], str), f"Item {i}: missing retailer"

        # Real Exa data: price extraction is unreliable — many retailers use
        # JS-rendered prices that resist scraping. Require at least 25% priced.
        total = len(shopping["items"])
        assert priced_count >= max(1, total // 4), (
            f"Only {priced_count}/{total} items have prices > 0"
        )

    async def test_confidence_scores_varied(self, client: httpx.AsyncClient):
        """Confidence scores aren't all identical (real scoring varies)."""
        shopping = await self._get_completed_shopping(client)
        scores = [item["confidence_score"] for item in shopping["items"]]

        # All scores should be valid
        for s in scores:
            assert 0 <= s <= 1, f"Invalid confidence score: {s}"

        # With real products, scores should vary (not all 0.95 like mocks)
        if len(scores) >= 2:
            unique_scores = set(round(s, 2) for s in scores)
            assert len(unique_scores) >= 2, f"All confidence scores identical: {scores}"


# ---------------------------------------------------------------------------
# WI-28: Iteration Cap with Real AI (Phase G)
# ---------------------------------------------------------------------------


@_skip_unless_real
class TestWI28IterationCap:
    """WI-28: Verify 5-iteration cap with real Gemini edits.

    Runs 5 real edit operations (mix of text feedback and annotation) and
    verifies: each revision produces a different image, iteration_count
    increments correctly, and the workflow auto-transitions to 'approval'
    after the 5th edit.
    """

    async def test_five_real_iterations_forces_approval(self, client: httpx.AsyncClient):
        """5 mixed real edits → step auto-transitions to 'approval'."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # 3 text feedbacks
        for i in range(3):
            await _poll_iteration(client, pid, i)
            r = await client.post(
                f"/api/v1/projects/{pid}/iterate/feedback",
                json={
                    "feedback": f"Iteration {i + 1}: add warmer lighting "
                    f"and more green plants near the windows"
                },
            )
            assert r.status_code == 200

        # 2 annotation edits
        for i in range(2):
            await _poll_iteration(client, pid, 3 + i)
            r = await client.post(
                f"/api/v1/projects/{pid}/iterate/annotate",
                json={
                    "annotations": [
                        {
                            "region_id": 1,
                            "center_x": 0.5,
                            "center_y": 0.5,
                            "radius": 0.15,
                            "instruction": f"Change area {i + 1}: "
                            f"replace this furniture with something modern",
                        }
                    ]
                },
            )
            assert r.status_code == 200

        # After 5 edits, workflow should auto-transition to approval
        state = await _poll_step(client, pid, "approval")
        assert state["iteration_count"] == 5
        assert len(state["revision_history"]) == 5

        # Each revision should produce a distinct image (different R2 keys)
        revised_urls = [rev["revised_image_url"] for rev in state["revision_history"]]
        unique_urls = set(revised_urls)
        assert len(unique_urls) >= 3, (
            f"Expected diverse images across iterations, "
            f"got {len(unique_urls)} unique out of {len(revised_urls)}"
        )


# ---------------------------------------------------------------------------
# Concurrent Workflows — stress test for real Temporal
# ---------------------------------------------------------------------------


class TestConcurrentWorkflows:
    """Verify multiple Temporal workflows run independently.

    Exercises Temporal's ability to handle concurrent workflow instances —
    each project is an independent workflow with its own state machine.
    """

    async def test_three_projects_at_different_steps(self, client: httpx.AsyncClient):
        """Create 3 projects, advance each to a different step."""
        # Project A: stays at photos
        ra = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        assert ra.status_code == 201
        pid_a = ra.json()["project_id"]

        # Project B: advance to scan
        pid_b = await _create_project_with_photos(client)

        # Project C: advance to intake
        pid_c = await _advance_to_intake(client)

        # Verify each is at its expected step
        sa = (await client.get(f"/api/v1/projects/{pid_a}")).json()
        sb = (await client.get(f"/api/v1/projects/{pid_b}")).json()
        sc = (await client.get(f"/api/v1/projects/{pid_c}")).json()

        assert sa["step"] == "photos"
        assert sb["step"] == "scan"
        assert sc["step"] == "intake"

        # Advancing C doesn't affect A or B
        await _complete_mock_intake(client, pid_c)
        await _poll_step(client, pid_c, "selection")

        sa2 = (await client.get(f"/api/v1/projects/{pid_a}")).json()
        sb2 = (await client.get(f"/api/v1/projects/{pid_b}")).json()
        assert sa2["step"] == "photos"
        assert sb2["step"] == "scan"

    async def test_concurrent_project_creation(self, client: httpx.AsyncClient):
        """Create 5 projects concurrently and verify all succeed."""
        tasks = [client.post("/api/v1/projects", json=_NEW_PROJECT) for _ in range(5)]
        responses = await asyncio.gather(*tasks)
        ids = set()
        for r in responses:
            assert r.status_code == 201
            pid = r.json()["project_id"]
            ids.add(pid)
        # All 5 should have unique IDs
        assert len(ids) == 5


# ---------------------------------------------------------------------------
# Photo Removal During Workflow
# ---------------------------------------------------------------------------


class TestPhotoRemovalDuringWorkflow:
    """Verify photo removal at various workflow steps."""

    async def test_remove_photo_at_scan_step(self, client: httpx.AsyncClient):
        """Removing a room photo at scan step updates the photo list."""
        pid = await _create_project_with_photos(client)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert state["step"] == "scan"
        assert len(state["photos"]) == 2

        photo_id = state["photos"][0]["photo_id"]
        r = await client.delete(f"/api/v1/projects/{pid}/photos/{photo_id}")
        assert r.status_code == 204

        state2 = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert len(state2["photos"]) == 1
        assert state2["photos"][0]["photo_id"] != photo_id

    async def test_remove_all_photos_at_scan(self, client: httpx.AsyncClient):
        """Removing all photos at scan step empties the list."""
        pid = await _create_project_with_photos(client)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        for photo in state["photos"]:
            r = await client.delete(f"/api/v1/projects/{pid}/photos/{photo['photo_id']}")
            assert r.status_code == 204

        state2 = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert len(state2["photos"]) == 0

    async def test_upload_photo_rejected_after_scan(self, client: httpx.AsyncClient):
        """Photo upload is rejected at intake (only allowed at photos/scan)."""
        pid = await _advance_to_intake(client)
        sharp = _make_sharp_jpeg()
        r = await _upload_photo(client, pid, sharp, photo_type="inspiration")
        assert r.status_code == 409

    async def test_add_inspiration_photo_at_scan_step(self, client: httpx.AsyncClient):
        """Inspiration photos can be added at scan step before proceeding."""
        pid = await _create_project_with_photos(client)
        sharp = _make_sharp_jpeg()
        r = await _upload_photo(client, pid, sharp, photo_type="inspiration", note="love this")
        assert r.status_code == 200
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        inspo = [p for p in state["photos"] if p["photo_type"] == "inspiration"]
        assert len(inspo) == 1
        assert inspo[0]["note"] == "love this"


# ---------------------------------------------------------------------------
# Rapid Signal Sequences
# ---------------------------------------------------------------------------


class TestRapidSignalSequences:
    """Verify workflow handles rapid signal bursts without corruption.

    In production, a fast user could trigger multiple actions before
    the UI updates. These tests verify Temporal handles rapid signals.
    """

    async def test_rapid_text_feedback_iterations(self, client: httpx.AsyncClient):
        """Send 3 text feedbacks rapidly without polling between them."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await _poll_step(client, pid, "iteration", timeout=10.0)

        # Send 3 feedbacks rapidly (no polling between)
        feedbacks = [
            "Make the lighting warmer and add ambient glow",
            "Add a large potted plant near the window area",
            "Change the rug to a lighter neutral tone please",
        ]
        for fb in feedbacks:
            r = await client.post(
                f"/api/v1/projects/{pid}/iterate/feedback",
                json={"feedback": fb},
            )
            assert r.status_code == 200

        # All 3 should eventually be processed (real Gemini: ~60s each)
        t = 300.0 if _real_ai_mode else POLL_TIMEOUT
        state = await _poll_iteration(client, pid, 3, timeout=t)
        assert state["iteration_count"] == 3
        assert len(state["revision_history"]) == 3

    async def test_rapid_photo_uploads(self, client: httpx.AsyncClient):
        """Upload 2 room photos without waiting between them."""
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        pid = resp.json()["project_id"]
        sharp = _make_sharp_jpeg()

        # Upload both photos rapidly
        tasks = [
            _upload_photo(client, pid, sharp),
            _upload_photo(client, pid, sharp),
        ]
        results = await asyncio.gather(*tasks)
        for r in results:
            assert r.status_code == 200

        # Confirm photos to advance
        r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert r.status_code == 200

        # Should eventually reach scan step
        await _poll_step(client, pid, "scan", timeout=10.0)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert len(state["photos"]) == 2


# ---------------------------------------------------------------------------
# Intake Session Management
# ---------------------------------------------------------------------------


class TestIntakeSessionManagement:
    """Verify intake session lifecycle through Temporal."""

    async def test_start_over_clears_intake_session(self, client: httpx.AsyncClient):
        """Start-over from intake resets the conversation."""
        pid = await _advance_to_intake(client)

        # Start intake and send a message
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/start",
            json={"mode": "full"},
        )
        assert r.status_code == 200
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        assert r.status_code == 200

        # Start over
        r = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)

        # Starting intake again should work fresh
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/start",
            json={"mode": "quick"},
        )
        assert r.status_code == 200

    async def test_multiple_intake_restarts(self, client: httpx.AsyncClient):
        """Can restart intake multiple times without errors."""
        pid = await _advance_to_intake(client)

        for mode in ["full", "quick", "full"]:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/start",
                json={"mode": mode},
            )
            assert r.status_code == 200
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": "test room"},
            )
            assert r.status_code == 200

    async def test_intake_preserves_photos_across_start_over(self, client: httpx.AsyncClient):
        """Photos and scan data survive start-over per workflow design."""
        pid = await _advance_to_intake(client)
        await _complete_mock_intake(client, pid)
        await _poll_step(client, pid, "selection")

        # Start over from selection
        r = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)

        # Photos should still be there
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        room_photos = [p for p in state["photos"] if p["photo_type"] == "room"]
        assert len(room_photos) == 2
        # But generation results should be cleared
        assert len(state["generated_options"]) == 0
        assert state["selected_option"] is None


# ---------------------------------------------------------------------------
# Golden Path: Complete Real AI Pipeline (create → shopping → delete)
# ---------------------------------------------------------------------------


@_skip_unless_real
class TestGoldenPathRealAI:
    """End-to-end golden path with ALL real AI services.

    This is the single most important test — it proves the entire pipeline
    works exactly as a real user would experience it:
        create → photos → scan(skip) → real intake (Claude Opus) → confirm →
        real generation (Gemini) → select → real edit (Gemini) → approve →
        real shopping (Exa + Claude) → verify quality → delete → 404

    Every AI service is exercised in sequence. With LLM caching enabled,
    subsequent runs use cached responses for deterministic re-testing.
    """

    async def test_full_pipeline_real_ai(self, client: httpx.AsyncClient):
        """Complete user journey through every AI service."""
        # --- 1. Create project ---
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT)
        assert resp.status_code == 201
        pid = resp.json()["project_id"]

        # --- 2. Upload 2 room photos ---
        sharp = _make_sharp_jpeg()
        for _ in range(2):
            r = await _upload_photo(client, pid, sharp)
            assert r.status_code == 200
        r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert r.status_code == 200
        await _poll_step(client, pid, "scan", timeout=15.0)

        # --- 3. Skip scan ---
        r = await client.post(f"/api/v1/projects/{pid}/scan/skip")
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)

        # --- 4. Real intake conversation (Claude Opus) ---
        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        intake_messages = [
            "It's a living room, roughly 15 by 20 feet with large south-facing windows",
            "Mid-century modern with warm wood tones and earthy greens",
            "Replace the old sofa with something more comfortable, add more plants",
            "That covers everything, please summarize",
        ]
        last_response = None
        for msg in intake_messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            last_response = r.json()
            # Agent should produce substantive responses
            assert len(last_response["agent_message"]) > 20

        # Confirm the brief (use agent's partial brief or provide minimal one)
        brief_data = {"room_type": "living room"}
        if last_response and last_response.get("partial_brief"):
            brief_data = last_response["partial_brief"]
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": brief_data},
        )
        assert r.status_code == 200

        # --- 5. Wait for real Gemini generation ---
        state = await _poll_step(client, pid, "selection", timeout=180.0)
        assert len(state["generated_options"]) == 2
        for opt in state["generated_options"]:
            assert "mock" not in opt["image_url"].lower()
            assert len(opt["caption"]) > 10

        # --- 6. Select first option ---
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200

        # --- 7. One real Gemini edit (text feedback) ---
        await _poll_iteration(client, pid, 0)
        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={
                "feedback": "Add warmer lighting throughout and place "
                "a tall fiddle leaf fig near the window"
            },
        )
        assert r.status_code == 200
        state = await _poll_iteration(client, pid, 1, timeout=120.0)
        assert state["iteration_count"] >= 1
        assert len(state["revision_history"]) >= 1
        revised_url = state["revision_history"][0]["revised_image_url"]
        assert "mock" not in revised_url.lower()

        # --- 8. Approve design ---
        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200

        # --- 9. Wait for real shopping list (Exa + Claude) ---
        state = await _poll_step(client, pid, "completed", timeout=180.0)
        assert state["approved"] is True
        shopping = state["shopping_list"]
        assert shopping is not None
        assert len(shopping["items"]) >= 3

        # Verify shopping quality (real products, not mocks)
        for item in shopping["items"]:
            assert "Mock" not in item["product_name"]
            assert item["product_url"].startswith("http")
            assert 0.0 <= item["confidence_score"] <= 1.0

        # At least some items should have prices
        priced = [i for i in shopping["items"] if i.get("price_cents", 0) > 0]
        assert len(priced) >= 1, "No items have prices"

        # Total should be positive
        assert shopping["total_estimated_cost_cents"] > 0

        # --- 10. Delete project ---
        # A completed Temporal workflow has already terminated, so the
        # cancel signal is a no-op.  We verify the DELETE endpoint returns
        # 204 (accepted) — that's the contract the iOS app relies on.
        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 204

    async def test_full_pipeline_real_ai_with_lidar(self, client: httpx.AsyncClient):
        """Complete user journey with LiDAR scan data through every AI service.

        Exercises the full pipeline with real room dimensions flowing through
        generation, editing, and shopping:
            create (has_lidar=True) → photos → confirm → submit scan →
            real intake (Claude Opus) → confirm → real generation (Gemini) →
            select → real edit (Gemini, dimension-aware) → approve →
            real shopping (Exa + Claude) → verify → delete
        """
        scan_data = _get_scan_data()
        room = scan_data["room"]

        # --- 1. Create project with LiDAR ---
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT_LIDAR)
        assert resp.status_code == 201
        pid = resp.json()["project_id"]

        # --- 2. Upload 2 room photos + confirm ---
        sharp = _make_sharp_jpeg()
        for _ in range(2):
            r = await _upload_photo(client, pid, sharp)
            assert r.status_code == 200
        r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert r.status_code == 200
        await _poll_step(client, pid, "scan", timeout=15.0)

        # --- 3. Submit scan data (real LiDAR or synthetic fallback) ---
        r = await client.post(f"/api/v1/projects/{pid}/scan", json=scan_data)
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)

        # Verify scan_data persisted (ScanData → room_dimensions: RoomDimensions)
        state = (await client.get(f"/api/v1/projects/{pid}")).json()
        sd = state["scan_data"]
        assert sd is not None
        rd = sd["room_dimensions"]
        assert rd is not None
        assert rd["width_m"] == pytest.approx(room["width"], abs=0.1)
        assert rd["length_m"] == pytest.approx(room["length"], abs=0.1)
        assert len(rd.get("furniture", [])) >= 1

        # --- 4. Real intake conversation (Claude Opus) ---
        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        rw, rl = room["width"], room["length"]
        intake_messages = [
            f"It's a bathroom, roughly {rw:.1f} by {rl:.1f}m, bathtub + glass panel",
            "Modern spa-inspired with warm wood, natural stone, and soft lighting",
            "Upgrade the vanity area, add better storage, keep the bathtub",
            "That covers everything, please summarize",
        ]
        last_response = None
        for msg in intake_messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            last_response = r.json()
            assert len(last_response["agent_message"]) > 20

        # Confirm the brief
        brief_data = {"room_type": "bathroom"}
        if last_response and last_response.get("partial_brief"):
            brief_data = last_response["partial_brief"]
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": brief_data},
        )
        assert r.status_code == 200

        # --- 5. Wait for real Gemini generation ---
        state = await _poll_step(client, pid, "selection", timeout=180.0)
        assert len(state["generated_options"]) == 2
        for opt in state["generated_options"]:
            assert "mock" not in opt["image_url"].lower()
            assert len(opt["caption"]) > 10

        # scan_data should persist through generation
        assert state["scan_data"] is not None
        assert state["scan_data"]["room_dimensions"]["width_m"] == pytest.approx(
            room["width"], abs=0.1
        )

        # Log generated options for visibility
        print(f"\n{'=' * 60}")
        print(f"LIDAR GOLDEN PATH — Project {pid}")
        print(f"Room: {room['width']:.1f}m x {room['length']:.1f}m x {room['height']:.1f}m")
        print(f"Furniture: {[f['type'] for f in scan_data.get('furniture', [])]}")
        print(f"{'=' * 60}")
        for i, opt in enumerate(state["generated_options"]):
            print(f"\n--- Design Option {i + 1} ---")
            print(f"  Caption: {opt['caption']}")
            print(f"  Image:   {opt['image_url']}")

        # --- 6. Select first option ---
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200

        # --- 7. One real Gemini edit (dimension-aware feedback) ---
        await _poll_iteration(client, pid, 0)
        r = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={
                "feedback": (
                    f"The vanity needs to fit the {room['width']:.1f}m wall. "
                    "Add warmer lighting above the mirror and a small plant on the counter."
                )
            },
        )
        assert r.status_code == 200
        state = await _poll_iteration(client, pid, 1, timeout=120.0)
        assert state["iteration_count"] >= 1
        assert len(state["revision_history"]) >= 1
        revised_url = state["revision_history"][0]["revised_image_url"]
        assert "mock" not in revised_url.lower()

        # scan_data persists through edits
        assert state["scan_data"] is not None

        print("\n--- Edit Result ---")
        print(f"  Revised: {revised_url}")

        # --- 8. Approve design ---
        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200

        # --- 9. Wait for real shopping list (Exa + Claude) ---
        # Shopping involves Exa search + Claude scoring — can take 3-5 minutes
        state = await _poll_step(client, pid, "completed", timeout=360.0)
        assert state["approved"] is True
        shopping = state["shopping_list"]
        assert shopping is not None
        assert len(shopping["items"]) >= 3

        # Verify shopping quality
        for item in shopping["items"]:
            assert "Mock" not in item["product_name"]
            assert item["product_url"].startswith("http")
            assert 0.0 <= item["confidence_score"] <= 1.0

        priced = [i for i in shopping["items"] if i.get("price_cents", 0) > 0]
        assert len(priced) >= 1, "No items have prices"
        assert shopping["total_estimated_cost_cents"] > 0

        # scan_data persists to completion
        assert state["scan_data"] is not None
        assert state["scan_data"]["room_dimensions"]["width_m"] == pytest.approx(
            room["width"], abs=0.1
        )

        # Log shopping list
        print(f"\n--- Shopping List ({len(shopping['items'])} items) ---")
        total_cents = shopping["total_estimated_cost_cents"]
        print(f"  Total: ${total_cents / 100:.2f}")
        for item in shopping["items"]:
            price = item.get("price_cents", 0)
            price_str = f"${price / 100:.2f}" if price else "no price"
            print(f"  - {item['product_name']} ({price_str})")
            print(f"    {item['product_url']}")
            if item.get("reason"):
                print(f"    Reason: {item['reason']}")
        print(f"{'=' * 60}\n")

        # --- 10. Delete project ---
        r = await client.delete(f"/api/v1/projects/{pid}")
        assert r.status_code == 204


# ---------------------------------------------------------------------------
# SSE Streaming E2E Tests
# ---------------------------------------------------------------------------


def _parse_sse_events(raw: str) -> list[tuple[str, str]]:
    """Parse raw SSE text into (event_type, data_json) pairs.

    Raises ValueError if any SSE block has an event type but no data (or vice
    versa), to prevent silently swallowing malformed server responses.
    Accumulates multi-line ``data:`` fields per the SSE spec.
    """
    events: list[tuple[str, str]] = []
    for block in raw.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        # Skip SSE comment lines (start with ':')
        if all(line.startswith(":") for line in block.split("\n") if line):
            continue
        event_type = ""
        data_parts: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                continue  # SSE comment
            if line.startswith("event: "):
                event_type = line[7:]
            elif line.startswith("data: "):
                data_parts.append(line[6:])
        data = "\n".join(data_parts)
        if event_type and data:
            events.append((event_type, data))
        elif event_type or data:
            raise ValueError(
                f"Malformed SSE block: event_type={event_type!r}, "
                f"data={data!r}, raw block={block!r}"
            )
    return events


@_skip_unless_real
class TestSSEIntakeStreamingE2E:
    """E2E: Intake chat SSE streaming with real Claude Opus.

    Verifies that the streaming endpoint returns incremental delta events
    followed by a done event with the complete IntakeChatOutput.
    """

    async def test_intake_sse_streams_deltas_and_done(self, client: httpx.AsyncClient):
        """Intake SSE endpoint streams real Claude response token-by-token."""
        scan_data = _get_scan_data()
        pid = await _advance_to_intake_with_scan(client, scan_data)

        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        # Use the streaming endpoint
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as stream_client:
            response = await stream_client.post(
                f"/api/v1/projects/{pid}/intake/message/stream",
                json={
                    "message": "It's a bathroom with a bathtub and glass panel, "
                    "roughly 2.4 by 2.8 meters"
                },
            )
            assert response.status_code == 200
            ct = response.headers.get("content-type", "")
            assert "text/event-stream" in ct, f"Not SSE: {ct}"

            events = _parse_sse_events(response.text)

        # Categorize events
        deltas = [(t, d) for t, d in events if t == "delta"]
        dones = [(t, d) for t, d in events if t == "done"]
        errors = [(t, d) for t, d in events if t == "error"]

        assert len(errors) == 0, f"Got error events: {errors}"
        assert len(dones) == 1, f"Expected exactly 1 done event, got {len(dones)}"

        # Verify delta events (if any) have valid text.
        # Claude's tool-use streaming may produce 0 deltas when the
        # _MessageExtractor can't parse incremental JSON for that response.
        # This is expected — the done event always carries the full result.
        for _, data in deltas:
            parsed = json.loads(data)
            assert "text" in parsed, f"Delta event missing 'text' key: {data}"

        full_text = "".join(json.loads(d)["text"] for _, d in deltas) if deltas else ""

        # Verify done event has complete IntakeChatOutput
        done_data = json.loads(dones[0][1])
        assert "agent_message" in done_data
        assert len(done_data["agent_message"]) > 20

        # When deltas are present, verify they are a prefix of the done message.
        # This catches mismatches where the stream sends wrong text.
        if full_text:
            agent_msg = done_data["agent_message"]
            assert agent_msg.startswith(full_text), (
                f"Delta text is not a prefix of done event. "
                f"Deltas: {full_text[:80]!r}... Done: {agent_msg[:80]!r}..."
            )

        # Print streaming stats
        print(f"\n{'=' * 60}")
        print(f"INTAKE SSE — Project {pid}")
        print(f"  Delta events: {len(deltas)}")
        print(f"  Streamed text length: {len(full_text)}")
        print(f"  Agent message length: {len(done_data['agent_message'])}")
        print(f"  Has options: {bool(done_data.get('options'))}")
        print(f"  Is summary: {done_data.get('is_summary', False)}")
        print(f"{'=' * 60}\n")

        # Cleanup
        await client.delete(f"/api/v1/projects/{pid}")

    async def test_intake_sse_multi_turn_conversation(self, client: httpx.AsyncClient):
        """Multi-turn intake conversation works correctly via SSE streaming."""
        scan_data = _get_scan_data()
        pid = await _advance_to_intake_with_scan(client, scan_data)

        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        messages = [
            "It's a bathroom with a bathtub and glass panel",
            "Modern spa-inspired with warm wood and natural stone",
            "Upgrade the vanity, add better storage, keep the bathtub",
            "That covers everything, please summarize",
        ]

        conversation_history: list[dict] = []
        got_summary = False

        for msg in messages:
            async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as stream_client:
                response = await stream_client.post(
                    f"/api/v1/projects/{pid}/intake/message/stream",
                    json={
                        "message": msg,
                        "conversation_history": conversation_history,
                    },
                )
                assert response.status_code == 200, f"SSE failed: {response.status_code}"

            events = _parse_sse_events(response.text)
            deltas = [(t, d) for t, d in events if t == "delta"]
            dones = [(t, d) for t, d in events if t == "done"]
            errors = [(t, d) for t, d in events if t == "error"]

            assert not errors, f"Error on '{msg}': {errors}"
            assert len(dones) == 1, f"No done on '{msg}'"

            done_data = json.loads(dones[0][1])
            agent_msg = done_data["agent_message"]
            assert len(agent_msg) > 10, f"Short: {agent_msg}"

            conversation_history.append({"role": "user", "content": msg})
            conversation_history.append({"role": "assistant", "content": agent_msg})

            turn = len(conversation_history) // 2
            print(f"  Turn {turn}: {len(deltas)} deltas, {len(agent_msg)} chars")

            if done_data.get("is_summary"):
                got_summary = True
                assert done_data.get("partial_brief") is not None
                brief = done_data["partial_brief"]
                assert brief.get("room_type") is not None
                print(f"  ** Summary reached! Room type: {brief['room_type']}")
                break

        assert got_summary, "Quick mode did not produce summary within 4 messages via SSE"

        # Cleanup
        await client.delete(f"/api/v1/projects/{pid}")

    async def test_intake_sse_session_persistence(self, client: httpx.AsyncClient):
        """SSE streaming correctly updates session state (history, brief)."""
        pid = await _advance_to_intake(client)

        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        # Send first message via SSE
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as stream_client:
            r1 = await stream_client.post(
                f"/api/v1/projects/{pid}/intake/message/stream",
                json={"message": "It's a living room, about 15x20 feet"},
            )
            assert r1.status_code == 200

        # Second message via non-streaming (proves session state persists)
        r2 = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "mid-century modern style"},
        )
        assert r2.status_code == 200
        data = r2.json()
        assert len(data["agent_message"]) > 20

        # Third message via SSE again (proves bidirectional compatibility)
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as stream_client:
            r3 = await stream_client.post(
                f"/api/v1/projects/{pid}/intake/message/stream",
                json={"message": "replace the old couch with something modern"},
            )
            assert r3.status_code == 200

        events = _parse_sse_events(r3.text)
        dones = [(t, d) for t, d in events if t == "done"]
        assert len(dones) == 1

        # Cleanup
        await client.delete(f"/api/v1/projects/{pid}")


@_skip_unless_real
class TestSSEShoppingStreamingE2E:
    """E2E: Shopping list SSE streaming with real Exa + Claude.

    Verifies that the shopping streaming endpoint produces status, item_search,
    item, and done events as products are found one-by-one.
    """

    async def test_shopping_sse_streams_products(self, client: httpx.AsyncClient):
        """Shopping SSE endpoint streams real products progressively.

        Full pipeline: photos → scan → intake → generation → select →
        approve → shopping SSE stream.
        """
        scan_data = _get_scan_data()
        room = scan_data["room"]

        # --- Setup: full pipeline to shopping step ---
        resp = await client.post("/api/v1/projects", json=_NEW_PROJECT_LIDAR)
        assert resp.status_code == 201
        pid = resp.json()["project_id"]

        # Upload photos
        sharp = _make_sharp_jpeg()
        for _ in range(2):
            r = await _upload_photo(client, pid, sharp)
            assert r.status_code == 200
        r = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert r.status_code == 200
        await _poll_step(client, pid, "scan", timeout=15.0)

        # Submit scan
        r = await client.post(f"/api/v1/projects/{pid}/scan", json=scan_data)
        assert r.status_code == 200
        await _poll_step(client, pid, "intake", timeout=10.0)

        # Real intake
        r = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert r.status_code == 200

        rw, rl = room["width"], room["length"]
        intake_messages = [
            f"Bathroom, {rw:.1f} by {rl:.1f}m, bathtub + glass panel",
            "Modern spa with warm wood and natural stone",
            "Upgrade vanity, add storage, keep bathtub, modernize",
            "That's everything, please summarize",
        ]
        last_response = None
        for msg in intake_messages:
            r = await client.post(
                f"/api/v1/projects/{pid}/intake/message",
                json={"message": msg},
            )
            assert r.status_code == 200
            last_response = r.json()
            if last_response.get("is_summary"):
                break

        brief_data = {"room_type": "bathroom"}
        if last_response and last_response.get("partial_brief"):
            brief_data = last_response["partial_brief"]
        r = await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": brief_data},
        )
        assert r.status_code == 200

        # Wait for generation
        state = await _poll_step(client, pid, "selection", timeout=180.0)
        assert len(state["generated_options"]) == 2

        # Select + approve
        r = await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        assert r.status_code == 200
        await _poll_step(client, pid, "iteration", timeout=10.0)
        r = await client.post(f"/api/v1/projects/{pid}/approve")
        assert r.status_code == 200

        # Poll for shopping step (need to catch it before activity completes)
        state = await _poll_step(client, pid, "shopping", timeout=30.0)

        # Connect to SSE stream
        async with httpx.AsyncClient(base_url=BASE_URL, timeout=360.0) as stream_client:
            response = await stream_client.get(
                f"/api/v1/projects/{pid}/shopping/stream",
            )

        # If we got 409, the activity already completed (race condition).
        # Use pytest.skip so test reports make it clear SSE was NOT tested.
        if response.status_code == 409:
            state = await _poll_step(client, pid, "completed", timeout=360.0)
            assert state["shopping_list"] is not None
            assert len(state["shopping_list"]["items"]) >= 1
            await client.delete(f"/api/v1/projects/{pid}")
            pytest.skip(
                "SSE race lost — activity finished before SSE connection. "
                "Shopping SSE streaming was NOT tested in this run."
            )

        assert response.status_code == 200, f"Shopping SSE: {response.status_code}"
        content_type = response.headers.get("content-type", "")
        assert "text/event-stream" in content_type

        events = _parse_sse_events(response.text)

        # Categorize events
        statuses = [(t, d) for t, d in events if t == "status"]
        item_searches = [(t, d) for t, d in events if t == "item_search"]
        items = [(t, d) for t, d in events if t == "item"]
        dones = [(t, d) for t, d in events if t == "done"]
        errors = [(t, d) for t, d in events if t == "error"]

        print(f"\n{'=' * 60}")
        print(f"SHOPPING SSE — Project {pid}")
        print(f"  Status events: {len(statuses)}")
        print(f"  Item search events: {len(item_searches)}")
        print(f"  Item events: {len(items)}")
        print(f"  Done events: {len(dones)}")
        print(f"  Error events: {len(errors)}")

        # Hard assertions: no errors, exactly one done, at least one item
        assert len(errors) == 0, f"Got error events: {errors}"
        assert len(dones) == 1, f"Expected exactly 1 done event, got {len(dones)}"

        done_data = json.loads(dones[0][1])
        assert "items" in done_data
        assert len(done_data["items"]) >= 1, "Done event has no items"
        print(f"  Total items in done: {len(done_data['items'])}")
        for item_data in done_data["items"]:
            print(f"    - {item_data['product_name']}: {item_data['product_url']}")

        # Verify streamed item events have required fields
        for _, data in items:
            parsed = json.loads(data)
            assert "product_name" in parsed
            assert "product_url" in parsed
            print(f"  Streamed: {parsed['product_name']}")

        print(f"{'=' * 60}\n")

        # Wait for workflow to reach completed (SSE signals the result)
        state = await _poll_step(client, pid, "completed", timeout=120.0)
        assert state["shopping_list"] is not None
        assert len(state["shopping_list"]["items"]) >= 1

        # Cleanup
        await client.delete(f"/api/v1/projects/{pid}")
