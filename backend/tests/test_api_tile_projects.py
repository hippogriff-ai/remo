"""Integration tests for tile-projects FastAPI endpoints.

Two scopes in one file:

1. **Mock-mode** (conftest default, USE_TEMPORAL=false): every tile route must
   return 501 so a local dev never sees a silent no-op. Body-level Pydantic
   validation still fires (422) because FastAPI validates the request body
   before the handler runs.

2. **Temporal-mode** (TestAPIAgainstWorkflow): start a real in-process
   WorkflowEnvironment + Worker, flip USE_TEMPORAL=true on app.state, and
   exercise the route → signal → workflow-query round trip. This is where
   file-size, JSON parsing, and scan-to-surface conversion are checked
   end-to-end.
"""

from __future__ import annotations

import asyncio
import io
import json
import uuid

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities.mock_stubs import purge_project_data
from app.activities.tile_cutsheet import generate_cut_sheet_pdf
from app.activities.tile_render import render_tile_design
from app.workflows.tile_project import TileProjectWorkflow


def _valid_scan_json(*, include_window: bool = False) -> bytes:
    """Minimal RoomPlan JSON that parse_room_dimensions accepts."""
    openings = [
        {
            "type": "door",
            "wall_id": "wall_2",
            "width": 0.9,
            "height": 2.1,
            "position": {"x": 0.3},
        }
    ]
    if include_window:
        openings.append(
            {
                "type": "window",
                "wall_id": "wall_1",
                "width": 1.2,
                "height": 0.9,
                "position": {"x": 0.3},
            }
        )
    return json.dumps(
        {
            "room": {"width": 2.4, "length": 1.8, "height": 2.4, "unit": "meters"},
            "walls": [
                {"id": "wall_0", "width": 2.4, "height": 2.4},
                {"id": "wall_1", "width": 1.8, "height": 2.4},
                {"id": "wall_2", "width": 2.4, "height": 2.4},
                {"id": "wall_3", "width": 1.8, "height": 2.4},
            ],
            "openings": openings,
            "furniture": [],
            "surfaces": [],
            "floor_area_sqm": 4.32,
        }
    ).encode()


# ---------------------------------------------------------------------------
# Mock-mode: every tile route must refuse with 501.
# ---------------------------------------------------------------------------


class TestMockModeReturns501:
    """Without Temporal, tile routes have no state store — they must fail
    loudly rather than pretending the signal landed."""

    @pytest.mark.asyncio
    async def test_create_returns_501(self, client) -> None:
        resp = await client.post("/api/v1/tile-projects", json={"device_fingerprint": "x"})
        assert resp.status_code == 501
        assert resp.json()["error"] == "not_implemented"

    @pytest.mark.asyncio
    async def test_get_returns_501(self, client) -> None:
        resp = await client.get("/api/v1/tile-projects/any-id")
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_scan_returns_501(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any-id/scan",
            files={"file": ("scan.json", io.BytesIO(_valid_scan_json()), "application/json")},
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_materials_returns_501(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any-id/materials",
            json={
                "wall": {"width_cm": 30, "height_cm": 60, "per_box": 10},
                "floor": {"width_cm": 60, "height_cm": 60, "per_box": 5},
            },
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_policies_returns_501(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any-id/policies",
            json={
                "overage_policy": "flat_10",
                "starting_point_rule": "centered_focal_wall",
            },
        )
        assert resp.status_code == 501

    @pytest.mark.asyncio
    async def test_render_retry_export_confirm_cancel_return_501(self, client) -> None:
        for path, method in [
            ("/api/v1/tile-projects/x/render", "post"),
            ("/api/v1/tile-projects/x/retry-render", "post"),
            ("/api/v1/tile-projects/x/export", "post"),
            ("/api/v1/tile-projects/x/confirm-estimate", "post"),
            ("/api/v1/tile-projects/x", "delete"),
        ]:
            resp = await getattr(client, method)(path)
            assert resp.status_code == 501, path


# ---------------------------------------------------------------------------
# Body validation: Pydantic runs before the handler, so these still return 422
# even in mock mode.
# ---------------------------------------------------------------------------


class TestBodyValidation:
    @pytest.mark.asyncio
    async def test_create_missing_fingerprint_returns_422(self, client) -> None:
        resp = await client.post("/api/v1/tile-projects", json={})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_materials_rejects_non_positive_dimensions(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any/materials",
            json={
                "wall": {"width_cm": 0, "height_cm": 60, "per_box": 10},
                "floor": {"width_cm": 60, "height_cm": 60, "per_box": 5},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_materials_rejects_zero_per_box(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any/materials",
            json={
                "wall": {"width_cm": 30, "height_cm": 60, "per_box": 0},
                "floor": {"width_cm": 60, "height_cm": 60, "per_box": 5},
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_policies_rejects_invalid_enum(self, client) -> None:
        resp = await client.post(
            "/api/v1/tile-projects/any/policies",
            json={
                "overage_policy": "bogus",
                "starting_point_rule": "centered_focal_wall",
            },
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Temporal-mode: route → signal → query round-trips using the real workflow
# + in-process Worker.
# ---------------------------------------------------------------------------


_TILE_ACTIVITIES = [render_tile_design, generate_cut_sheet_pdf, purge_project_data]

pytestmark_module_scoped = pytest.mark.asyncio(loop_scope="module")


class TestAPIAgainstWorkflow:
    """These tests flip USE_TEMPORAL=true on the FastAPI app and point it at
    a real WorkflowEnvironment. Covers the file-size cap, JSON parsing, scan
    conversion, and signal-to-workflow wiring via HTTP.
    """

    @pytest.fixture(scope="class")
    async def workflow_env(self):
        async with await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter,
        ) as env:
            yield env

    @pytest.fixture
    async def live_client(self, client, workflow_env):
        """Point the FastAPI app at the real workflow env + enable Temporal."""
        from app.config import settings
        from app.main import app

        prior_use_temporal = settings.use_temporal
        prior_temporal_client = getattr(app.state, "temporal_client", None)
        prior_task_queue = settings.temporal_task_queue

        settings.use_temporal = True
        app.state.temporal_client = workflow_env.client
        tq = f"tile-api-{uuid.uuid4()}"
        settings.temporal_task_queue = tq
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[TileProjectWorkflow],
            activities=_TILE_ACTIVITIES,
        ):
            try:
                yield client
            finally:
                settings.use_temporal = prior_use_temporal
                settings.temporal_task_queue = prior_task_queue
                if prior_temporal_client is None:
                    # Leave app.state clean — later mock-mode tests don't use it.
                    app.state.temporal_client = None  # type: ignore[assignment]
                else:
                    app.state.temporal_client = prior_temporal_client

    @pytest.mark.asyncio
    async def test_create_then_get_returns_state(self, live_client) -> None:
        resp = await live_client.post("/api/v1/tile-projects", json={"device_fingerprint": "d1"})
        assert resp.status_code == 201, resp.text
        project_id = resp.json()["project_id"]

        # Give the worker a moment to enter the run method.
        state = None
        for _ in range(20):
            await asyncio.sleep(0.01)
            state_resp = await live_client.get(f"/api/v1/tile-projects/{project_id}")
            if state_resp.status_code == 200:
                state = state_resp.json()
                if state["step"] == "replace_material_scan":
                    break
        assert state is not None
        assert state["step"] == "replace_material_scan"

    @pytest.mark.asyncio
    async def test_get_unknown_project_returns_404(self, live_client) -> None:
        resp = await live_client.get(f"/api/v1/tile-projects/{uuid.uuid4()}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_scan_happy_path_signals_surfaces(self, live_client) -> None:
        create_resp = await live_client.post(
            "/api/v1/tile-projects", json={"device_fingerprint": "d2"}
        )
        project_id = create_resp.json()["project_id"]

        scan_resp = await live_client.post(
            f"/api/v1/tile-projects/{project_id}/scan",
            files={
                "file": ("scan.json", io.BytesIO(_valid_scan_json()), "application/json"),
            },
        )
        assert scan_resp.status_code == 200, scan_resp.text
        assert scan_resp.json()["surface_count"] == 5  # 1 floor + 4 walls

        state = None
        for _ in range(20):
            await asyncio.sleep(0.01)
            s = await live_client.get(f"/api/v1/tile-projects/{project_id}")
            if s.status_code == 200 and s.json()["step"] == "replace_material_specs":
                state = s.json()
                break
        assert state is not None
        assert len(state["surfaces"]) == 5

    @pytest.mark.asyncio
    async def test_scan_rejects_malformed_json(self, live_client) -> None:
        create_resp = await live_client.post(
            "/api/v1/tile-projects", json={"device_fingerprint": "d3"}
        )
        project_id = create_resp.json()["project_id"]
        resp = await live_client.post(
            f"/api/v1/tile-projects/{project_id}/scan",
            files={"file": ("scan.json", io.BytesIO(b"not-json"), "application/json")},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_scan"

    @pytest.mark.asyncio
    async def test_scan_rejects_file_over_size_cap(self, live_client) -> None:
        create_resp = await live_client.post(
            "/api/v1/tile-projects", json={"device_fingerprint": "d4"}
        )
        project_id = create_resp.json()["project_id"]

        # Build a payload over 1 MB. Even though file.size is set by httpx
        # here (not None), the belt-and-suspenders len(raw) check also
        # catches streamed uploads where file.size is None.
        oversized = b"x" * (1024 * 1024 + 100)
        resp = await live_client.post(
            f"/api/v1/tile-projects/{project_id}/scan",
            files={"file": ("big.json", io.BytesIO(oversized), "application/json")},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "file_too_large"

    @pytest.mark.asyncio
    async def test_policies_persist_across_query(self, live_client) -> None:
        create_resp = await live_client.post(
            "/api/v1/tile-projects", json={"device_fingerprint": "d5"}
        )
        project_id = create_resp.json()["project_id"]
        resp = await live_client.post(
            f"/api/v1/tile-projects/{project_id}/policies",
            json={
                "overage_policy": "risk_adjusted",
                "starting_point_rule": "largest_wall_corner",
                "contractor_tier": None,
            },
        )
        assert resp.status_code == 200

        # Policy is stored even before the estimate phase is reached.
        state = None
        for _ in range(20):
            await asyncio.sleep(0.01)
            s = await live_client.get(f"/api/v1/tile-projects/{project_id}")
            if s.status_code == 200 and s.json()["overage_policy"] == "risk_adjusted":
                state = s.json()
                break
        assert state is not None
        assert state["starting_point_rule"] == "largest_wall_corner"

    @pytest.mark.asyncio
    async def test_cancel_returns_ok_and_transitions_state(self, live_client) -> None:
        create_resp = await live_client.post(
            "/api/v1/tile-projects", json={"device_fingerprint": "d6"}
        )
        project_id = create_resp.json()["project_id"]

        resp = await live_client.delete(f"/api/v1/tile-projects/{project_id}")
        assert resp.status_code == 200

        final_step = None
        for _ in range(30):
            await asyncio.sleep(0.01)
            s = await live_client.get(f"/api/v1/tile-projects/{project_id}")
            if s.status_code == 200 and s.json()["step"] in {"cancelled", "abandoned"}:
                final_step = s.json()["step"]
                break
        assert final_step in {"cancelled", "abandoned"}
