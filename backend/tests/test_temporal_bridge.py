"""Tests for the Temporal code paths in projects.py (use_temporal=True).

All existing API endpoint tests exercise the mock path (use_temporal=False).
This file tests every Temporal branch: create -> query -> signal for all 17
endpoints, including error handling (RPCError NOT_FOUND, R2 upload/rollback).
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from temporalio.service import RPCError, RPCStatusCode

from app.main import app
from app.models.contracts import (
    DesignOption,
    PhotoData,
    WorkflowError,
    WorkflowState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rpc_error(status: RPCStatusCode) -> RPCError:
    """Create an RPCError with the required raw_grpc_status arg."""
    return RPCError(status.name.lower(), status=status, raw_grpc_status=b"")


def _make_temporal_client() -> MagicMock:
    """Build a mock Temporal client with a handle that queries/signals."""
    handle = MagicMock()
    handle.query = AsyncMock()
    handle.signal = AsyncMock()

    client = MagicMock()
    client.get_workflow_handle = MagicMock(return_value=handle)
    client.start_workflow = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def temporal_app():
    """Yield (mock_client, async_httpx_client) with Temporal mode enabled.

    Injects a mock Temporal client into app.state. Uses
    raise_app_exceptions=False so unhandled errors return 500 JSON
    (matching production behavior).
    """
    with patch("app.api.routes.projects.settings") as mock_settings:
        mock_settings.use_temporal = True
        mock_settings.temporal_task_queue = "test-queue"
        mock_settings.use_mock_activities = True

        mock_client = _make_temporal_client()
        app.state.temporal_client = mock_client

        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield mock_client, ac

    if hasattr(app.state, "temporal_client"):
        del app.state.temporal_client


# ---------------------------------------------------------------------------
# Reusable workflow states
# ---------------------------------------------------------------------------

_PHOTOS = WorkflowState(step="photos")
_SCAN = WorkflowState(
    step="scan",
    photos=[PhotoData(photo_id="p1", storage_key="k1", photo_type="room")],
)
_INTAKE = WorkflowState(step="intake")
_INTAKE_INSPO = WorkflowState(
    step="intake",
    photos=[
        PhotoData(photo_id="p1", storage_key="k1", photo_type="room"),
        PhotoData(photo_id="p2", storage_key="k2", photo_type="inspiration"),
    ],
)
_SELECTION = WorkflowState(
    step="selection",
    generated_options=[
        DesignOption(image_url="https://r2.example.com/opt0.png", caption="A"),
        DesignOption(image_url="https://r2.example.com/opt1.png", caption="B"),
    ],
)
_ITERATION = WorkflowState(
    step="iteration",
    selected_option=0,
    current_image="https://r2.example.com/opt0.png",
)
_ERROR = WorkflowState(
    step="iteration",
    error=WorkflowError(message="Gemini timeout", retryable=True),
)


def _photo_files():
    """Return fresh file payload for upload (BytesIO must be re-created)."""
    return {"file": ("room.jpg", io.BytesIO(b"\xff\xd8" + b"\x00" * 100), "image/jpeg")}


def _mock_validation(passed: bool = True):
    """Patch validate_photo to return a canned result."""
    from app.models.contracts import ValidatePhotoOutput

    return patch(
        "app.api.routes.projects.validate_photo",
        return_value=ValidatePhotoOutput(passed=passed, failures=[], messages=["OK"]),
    )


# ---------------------------------------------------------------------------
# Query + state resolution
# ---------------------------------------------------------------------------


class TestTemporalQuery:
    """GET /api/v1/projects/{id} — Temporal query path."""

    @pytest.mark.asyncio
    async def test_returns_workflow_state(self, temporal_app):
        """Queries Temporal and returns WorkflowState."""
        mock_client, client = temporal_app
        mock_client.get_workflow_handle.return_value.query.return_value = _ITERATION

        resp = await client.get("/api/v1/projects/proj-1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["step"] == "iteration"
        assert body["current_image"] == "https://r2.example.com/opt0.png"

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, temporal_app):
        """Temporal NOT_FOUND -> 404 with ErrorResponse."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.get("/api/v1/projects/nonexistent")
        assert resp.status_code == 404
        assert resp.json()["error"] == "workflow_not_found"

    @pytest.mark.asyncio
    async def test_internal_error_returns_500(self, temporal_app):
        """Non-NOT_FOUND RPCError is re-raised -> 500."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.side_effect = _rpc_error(RPCStatusCode.INTERNAL)

        resp = await client.get("/api/v1/projects/proj-1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Create project
# ---------------------------------------------------------------------------


class TestCreateProjectTemporal:
    """POST /api/v1/projects with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_starts_workflow(self, temporal_app):
        """Creates project and starts Temporal workflow."""
        mock_client, client = temporal_app
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-123"})
        assert resp.status_code == 201
        project_id = resp.json()["project_id"]
        assert len(project_id) == 36

        mock_client.start_workflow.assert_called_once()
        call_kwargs = mock_client.start_workflow.call_args
        assert call_kwargs.kwargs["id"] == project_id
        assert call_kwargs.kwargs["task_queue"] == "test-queue"

    @pytest.mark.asyncio
    async def test_workflow_start_failure_returns_500(self, temporal_app):
        """RPCError on workflow start -> 500."""
        mock_client, client = temporal_app
        mock_client.start_workflow.side_effect = _rpc_error(RPCStatusCode.UNAVAILABLE)

        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-123"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Delete project
# ---------------------------------------------------------------------------


class TestDeleteProjectTemporal:
    """DELETE /api/v1/projects/{id} with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_signals_cancel(self, temporal_app):
        """Sends cancel_project signal to Temporal."""
        mock_client, client = temporal_app

        resp = await client.delete("/api/v1/projects/proj-1")
        assert resp.status_code == 204
        mock_client.get_workflow_handle.return_value.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, temporal_app):
        """Temporal NOT_FOUND on signal -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.delete("/api/v1/projects/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Photo upload (R2 integration)
# ---------------------------------------------------------------------------


class TestPhotoUploadTemporal:
    """POST /api/v1/projects/{id}/photos with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_uploads_to_r2_and_signals(self, temporal_app):
        """Valid photo: R2 upload + add_photo signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS

        with (
            _mock_validation(),
            patch("app.utils.r2.upload_object") as mock_upload,
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        assert resp.status_code == 200
        assert resp.json()["validation"]["passed"] is True
        mock_upload.assert_called_once()
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_r2_upload_failure_returns_500(self, temporal_app):
        """R2 upload failure is re-raised -> 500."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS

        with (
            _mock_validation(),
            patch(
                "app.utils.r2.upload_object",
                side_effect=ConnectionError("R2 down"),
            ),
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_signal_failure_triggers_r2_rollback(self, temporal_app):
        """Signal failure after R2 upload -> R2 object deleted."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        with (
            _mock_validation(),
            patch("app.utils.r2.upload_object") as mock_upload,
            patch("app.utils.r2.delete_object") as mock_delete,
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        assert resp.status_code == 404
        mock_upload.assert_called_once()
        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_r2_rollback_failure_logged_not_raised(self, temporal_app):
        """R2 rollback failure is logged but returns 404 from signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        with (
            _mock_validation(),
            patch("app.utils.r2.upload_object"),
            patch(
                "app.utils.r2.delete_object",
                side_effect=ConnectionError("R2 down"),
            ),
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        # 404 from signal NOT_FOUND, not 500 from rollback failure
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_validation_failure_skips_upload_and_signal(self, temporal_app):
        """Failed validation -> no R2 upload, no signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS

        with (
            _mock_validation(passed=False),
            patch("app.utils.r2.upload_object") as mock_upload,
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        assert resp.status_code == 200
        assert resp.json()["validation"]["passed"] is False
        mock_upload.assert_not_called()
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Delete photo
# ---------------------------------------------------------------------------


class TestDeletePhotoTemporal:
    """DELETE /api/v1/projects/{id}/photos/{photo_id}."""

    @pytest.mark.asyncio
    async def test_signals_remove_photo(self, temporal_app):
        """Sends remove_photo signal for existing photo."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = WorkflowState(
            step="photos",
            photos=[PhotoData(photo_id="ph-1", storage_key="k", photo_type="room")],
        )

        resp = await client.delete("/api/v1/projects/proj-1/photos/ph-1")
        assert resp.status_code == 204
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_nonexistent_photo_returns_404(self, temporal_app):
        """Photo not in state -> 404 before signaling."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS  # no photos

        resp = await client.delete("/api/v1/projects/proj-1/photos/missing")
        assert resp.status_code == 404
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Scan endpoints
# ---------------------------------------------------------------------------


class TestScanTemporal:
    """Scan endpoints with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_complete_scan_signals(self, temporal_app):
        """POST /scan -> complete_scan signal with parsed ScanData."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SCAN

        lidar = {"room": {"width": 5.0, "length": 4.0, "height": 2.7}}
        resp = await client.post("/api/v1/projects/proj-1/scan", json=lidar)
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_scan_signals(self, temporal_app):
        """POST /scan/skip -> skip_scan signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SCAN

        resp = await client.post("/api/v1/projects/proj-1/scan/skip")
        assert resp.status_code == 200
        handle.signal.assert_called_once()


# ---------------------------------------------------------------------------
# Intake endpoints
# ---------------------------------------------------------------------------


class TestIntakeTemporal:
    """Intake endpoints with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_confirm_intake_signals(self, temporal_app):
        """POST /intake/confirm -> complete_intake signal with brief."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _INTAKE

        brief = {
            "room_type": "living_room",
            "style": "modern",
            "budget_cents": 500000,
            "occupants": "2 adults",
            "priorities": "comfort",
        }
        resp = await client.post(
            "/api/v1/projects/proj-1/intake/confirm",
            json={"brief": brief},
        )
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_intake_signals(self, temporal_app):
        """POST /intake/skip -> skip_intake signal (with inspiration)."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _INTAKE_INSPO

        resp = await client.post("/api/v1/projects/proj-1/intake/skip")
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_without_inspiration_rejected(self, temporal_app):
        """Skip without inspiration -> 422 (doesn't signal)."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _INTAKE

        resp = await client.post("/api/v1/projects/proj-1/intake/skip")
        assert resp.status_code == 422
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class TestSelectOptionTemporal:
    """POST /api/v1/projects/{id}/select with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_select_option_signals(self, temporal_app):
        """Sends select_option signal with index."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SELECTION

        resp = await client.post("/api/v1/projects/proj-1/select", json={"index": 0})
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_invalid_index_returns_422(self, temporal_app):
        """Option index out of range -> 422 before signaling.

        Uses index=1 with a state containing only 1 option so the request
        passes Pydantic validation (le=1) but fails the endpoint range check.
        """
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = WorkflowState(
            step="selection",
            generated_options=[
                DesignOption(image_url="https://r2.example.com/opt0.png", caption="A"),
            ],
        )

        resp = await client.post("/api/v1/projects/proj-1/select", json={"index": 1})
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_selection"
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Start over
# ---------------------------------------------------------------------------


class TestStartOverTemporal:
    """POST /api/v1/projects/{id}/start-over with use_temporal=True."""

    @pytest.mark.asyncio
    async def test_start_over_signals(self, temporal_app):
        """Sends start_over signal from iteration step."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION

        resp = await client.post("/api/v1/projects/proj-1/start-over")
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_over_from_completed_blocked(self, temporal_app):
        """Start over from completed step -> 409."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = WorkflowState(step="completed", approved=True)

        resp = await client.post("/api/v1/projects/proj-1/start-over")
        assert resp.status_code == 409
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Iteration: annotation + text feedback
# ---------------------------------------------------------------------------


class TestAnnotationEditTemporal:
    """POST /api/v1/projects/{id}/iterate/annotate."""

    @pytest.mark.asyncio
    async def test_annotation_edit_signals(self, temporal_app):
        """Sends submit_annotation_edit signal with serialized annotations."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION

        resp = await client.post(
            "/api/v1/projects/proj-1/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.3,
                        "radius": 0.1,
                        "instruction": "Add a large fiddle leaf fig plant here",
                    }
                ]
            },
        )
        assert resp.status_code == 200
        handle.signal.assert_called_once()
        # Verify annotations were serialized to dicts
        annotations_arg = handle.signal.call_args.args[-1]
        assert isinstance(annotations_arg, list)
        assert isinstance(annotations_arg[0], dict)
        assert "instruction" in annotations_arg[0]


class TestTextFeedbackTemporal:
    """POST /api/v1/projects/{id}/iterate/feedback."""

    @pytest.mark.asyncio
    async def test_text_feedback_signals(self, temporal_app):
        """Sends submit_text_feedback signal with feedback string."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION

        resp = await client.post(
            "/api/v1/projects/proj-1/iterate/feedback",
            json={"feedback": "Make the lighting much warmer"},
        )
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_short_feedback_rejected(self, temporal_app):
        """Feedback under 10 chars -> 422 before signaling."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION

        resp = await client.post(
            "/api/v1/projects/proj-1/iterate/feedback",
            json={"feedback": "brighter"},
        )
        assert resp.status_code == 422
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


class TestApproveDesignTemporal:
    """POST /api/v1/projects/{id}/approve."""

    @pytest.mark.asyncio
    async def test_approve_signals(self, temporal_app):
        """Sends approve_design signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION

        resp = await client.post("/api/v1/projects/proj-1/approve")
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_with_active_error_blocked(self, temporal_app):
        """Active error -> 409, must retry first."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ERROR

        resp = await client.post("/api/v1/projects/proj-1/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "active_error"
        handle.signal.assert_not_called()


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


class TestRetryFailedStepTemporal:
    """POST /api/v1/projects/{id}/retry."""

    @pytest.mark.asyncio
    async def test_retry_signals(self, temporal_app):
        """Sends retry_failed_step signal."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ERROR

        resp = await client.post("/api/v1/projects/proj-1/retry")
        assert resp.status_code == 200
        handle.signal.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_not_found_returns_404(self, temporal_app):
        """Temporal NOT_FOUND on signal -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ERROR
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/retry")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Signal error propagation
# ---------------------------------------------------------------------------


class TestSignalErrorPropagation:
    """Non-NOT_FOUND RPCError on signals should re-raise -> 500."""

    @pytest.mark.asyncio
    async def test_signal_internal_error_returns_500(self, temporal_app):
        """INTERNAL RPCError on signal -> 500."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.INTERNAL)

        resp = await client.post("/api/v1/projects/proj-1/approve")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Photo upload without R2 configured (line 404)
# ---------------------------------------------------------------------------


class TestPhotoUploadNoR2:
    """Upload photo in Temporal mode without R2 credentials."""

    @pytest.mark.asyncio
    async def test_no_r2_warns_and_signals(self, temporal_app):
        """Without R2 credentials, skips upload and still signals workflow."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _PHOTOS

        with (
            _mock_validation(),
            patch("app.api.routes.projects._r2_configured", return_value=False),
        ):
            resp = await client.post(
                "/api/v1/projects/proj-1/photos",
                files=_photo_files(),
                data={"photo_type": "room"},
            )

        assert resp.status_code == 200
        assert resp.json()["validation"]["passed"] is True
        handle.signal.assert_called_once()


# ---------------------------------------------------------------------------
# Signal NOT_FOUND for all remaining endpoints (lines 473, 523, 554,
# 740, 783, 821, 848, 893, 927, 957)
# ---------------------------------------------------------------------------


class TestSignalNotFoundAllEndpoints:
    """Verify every endpoint returns 404 when signal hits NOT_FOUND.

    The delete and retry endpoints already have NOT_FOUND tests above;
    these cover the remaining endpoints.
    """

    @pytest.mark.asyncio
    async def test_delete_photo_signal_not_found(self, temporal_app):
        """DELETE photo signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = WorkflowState(
            step="photos",
            photos=[PhotoData(photo_id="ph-1", storage_key="k", photo_type="room")],
        )
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.delete("/api/v1/projects/proj-1/photos/ph-1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_complete_scan_signal_not_found(self, temporal_app):
        """POST scan signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SCAN
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        lidar = {"room": {"width": 5.0, "length": 4.0, "height": 2.7}}
        resp = await client.post("/api/v1/projects/proj-1/scan", json=lidar)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_skip_scan_signal_not_found(self, temporal_app):
        """POST scan/skip signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SCAN
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/scan/skip")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_confirm_intake_signal_not_found(self, temporal_app):
        """POST intake/confirm signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _INTAKE
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        brief = {
            "room_type": "living_room",
            "style": "modern",
            "budget_cents": 500000,
            "occupants": "2 adults",
            "priorities": "comfort",
        }
        resp = await client.post(
            "/api/v1/projects/proj-1/intake/confirm",
            json={"brief": brief},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_skip_intake_signal_not_found(self, temporal_app):
        """POST intake/skip signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _INTAKE_INSPO
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/intake/skip")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_select_option_signal_not_found(self, temporal_app):
        """POST select signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _SELECTION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/select", json={"index": 0})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_start_over_signal_not_found(self, temporal_app):
        """POST start-over signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/start-over")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_annotation_edit_signal_not_found(self, temporal_app):
        """POST iterate/annotate signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post(
            "/api/v1/projects/proj-1/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.3,
                        "radius": 0.1,
                        "instruction": "Add a large fiddle leaf fig plant here",
                    }
                ]
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_text_feedback_signal_not_found(self, temporal_app):
        """POST iterate/feedback signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post(
            "/api/v1/projects/proj-1/iterate/feedback",
            json={"feedback": "Make the lighting much warmer"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_design_signal_not_found(self, temporal_app):
        """POST approve signal NOT_FOUND -> 404."""
        mock_client, client = temporal_app
        handle = mock_client.get_workflow_handle.return_value
        handle.query.return_value = _ITERATION
        handle.signal.side_effect = _rpc_error(RPCStatusCode.NOT_FOUND)

        resp = await client.post("/api/v1/projects/proj-1/approve")
        assert resp.status_code == 404
