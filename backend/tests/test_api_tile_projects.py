"""Integration tests for tile-projects FastAPI endpoints (mock mode).

These run in the conftest-configured mock environment (USE_TEMPORAL=false),
so they exercise the route surface, request/response validation, and the
lidar-to-surface helper — but do NOT exercise the Temporal signal/query
round-trip (that's covered by test_tile_workflow.py).
"""

from __future__ import annotations

import io
import json

import pytest


def _valid_scan_json() -> bytes:
    """Minimal RoomPlan JSON that parse_room_dimensions accepts."""
    return json.dumps(
        {
            "room": {"width": 2.4, "length": 1.8, "height": 2.4, "unit": "meters"},
            "walls": [
                {"id": "wall_0", "width": 2.4, "height": 2.4},
                {"id": "wall_1", "width": 1.8, "height": 2.4},
                {"id": "wall_2", "width": 2.4, "height": 2.4},
                {"id": "wall_3", "width": 1.8, "height": 2.4},
            ],
            "openings": [
                {
                    "type": "door",
                    "wall_id": "wall_2",
                    "width": 0.9,
                    "height": 2.1,
                    "position": {"x": 0.3},
                }
            ],
            "furniture": [],
            "surfaces": [],
            "floor_area_sqm": 4.32,
        }
    ).encode()


@pytest.fixture
async def tile_project_id(client) -> str:
    resp = await client.post("/api/v1/tile-projects", json={"device_fingerprint": "test-device-42"})
    assert resp.status_code == 201, resp.text
    return resp.json()["project_id"]


class TestCreateTileProject:
    @pytest.mark.asyncio
    async def test_creates_project_and_returns_uuid(self, client) -> None:
        resp = await client.post("/api/v1/tile-projects", json={"device_fingerprint": "abc"})
        assert resp.status_code == 201
        payload = resp.json()
        assert "project_id" in payload
        # UUID length sanity
        assert len(payload["project_id"]) == 36

    @pytest.mark.asyncio
    async def test_missing_fingerprint_returns_422(self, client) -> None:
        resp = await client.post("/api/v1/tile-projects", json={})
        assert resp.status_code == 422


class TestGetTileProjectMockMode:
    @pytest.mark.asyncio
    async def test_mock_get_returns_501(self, client, tile_project_id) -> None:
        resp = await client.get(f"/api/v1/tile-projects/{tile_project_id}")
        # Mock mode can't answer state queries — Temporal mode required.
        assert resp.status_code == 501
        body = resp.json()
        assert body["error"] == "not_implemented"


class TestUploadScan:
    @pytest.mark.asyncio
    async def test_parses_valid_scan_and_returns_surface_count(
        self, client, tile_project_id
    ) -> None:
        scan_bytes = _valid_scan_json()
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/scan",
            files={"file": ("scan.json", io.BytesIO(scan_bytes), "application/json")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        # 1 floor + 4 walls
        assert body["surface_count"] == 5

    @pytest.mark.asyncio
    async def test_rejects_malformed_json(self, client, tile_project_id) -> None:
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/scan",
            files={"file": ("scan.json", io.BytesIO(b"not-valid-json"), "application/json")},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_scan"

    @pytest.mark.asyncio
    async def test_rejects_missing_room_field(self, client, tile_project_id) -> None:
        bad_scan = json.dumps({"walls": []}).encode()
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/scan",
            files={"file": ("scan.json", io.BytesIO(bad_scan), "application/json")},
        )
        assert resp.status_code == 422


class TestSetMaterials:
    @pytest.mark.asyncio
    async def test_accepts_wall_and_floor_specs(self, client, tile_project_id) -> None:
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/materials",
            json={
                "wall": {
                    "width_cm": 30,
                    "height_cm": 60,
                    "grout_mm": 3.0,
                    "per_box": 10,
                    "price_per_box": 42.0,
                },
                "floor": {
                    "width_cm": 60,
                    "height_cm": 60,
                    "grout_mm": 3.0,
                    "per_box": 5,
                    "price_per_box": 68.0,
                },
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_rejects_non_positive_dimensions(self, client, tile_project_id) -> None:
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/materials",
            json={
                "wall": {"width_cm": 0, "height_cm": 60, "per_box": 10},
                "floor": {"width_cm": 60, "height_cm": 60, "per_box": 5},
            },
        )
        assert resp.status_code == 422


class TestSetPolicies:
    @pytest.mark.asyncio
    async def test_accepts_all_policy_combinations(self, client, tile_project_id) -> None:
        for overage, tier in [
            ("flat_10", None),
            ("risk_adjusted", None),
            ("contractor_tier", "apprentice"),
            ("contractor_tier", "pro"),
            ("contractor_tier", "perfectionist"),
        ]:
            resp = await client.post(
                f"/api/v1/tile-projects/{tile_project_id}/policies",
                json={
                    "overage_policy": overage,
                    "contractor_tier": tier,
                    "starting_point_rule": "centered_focal_wall",
                },
            )
            assert resp.status_code == 200, (overage, tier, resp.text)

    @pytest.mark.asyncio
    async def test_rejects_invalid_policy(self, client, tile_project_id) -> None:
        resp = await client.post(
            f"/api/v1/tile-projects/{tile_project_id}/policies",
            json={
                "overage_policy": "bogus",
                "starting_point_rule": "centered_focal_wall",
            },
        )
        assert resp.status_code == 422


class TestRequestRender:
    @pytest.mark.asyncio
    async def test_render_returns_ok_in_mock_mode(self, client, tile_project_id) -> None:
        resp = await client.post(f"/api/v1/tile-projects/{tile_project_id}/render")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestCancelTileProject:
    @pytest.mark.asyncio
    async def test_delete_returns_ok_in_mock_mode(self, client, tile_project_id) -> None:
        resp = await client.delete(f"/api/v1/tile-projects/{tile_project_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
