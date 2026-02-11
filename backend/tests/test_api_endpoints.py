"""Integration tests for all FastAPI endpoints.

Tests the full mock API flow: create project -> upload photos -> scan ->
intake -> select -> iterate -> approve. Verifies status codes, response
shapes, and step transition logic.
"""

import io
from unittest.mock import patch

import pytest

from app.api.routes.projects import _mock_states
from app.models.contracts import DesignOption, PhotoData, ValidatePhotoOutput

# Reusable mock validation results
_VALID = ValidatePhotoOutput(passed=True, failures=[], messages=["Photo looks great!"])
_INVALID = ValidatePhotoOutput(
    passed=False,
    failures=["low_resolution"],
    messages=["Image is too small (100px)."],
)


@pytest.fixture(autouse=True)
def clear_mock_state():
    """Reset mock state between tests."""
    _mock_states.clear()


@pytest.fixture
async def project_id(client):
    """Create a project and return its ID."""
    resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-device-123"})
    return resp.json()["project_id"]


class TestCreateProject:
    """POST /api/v1/projects"""

    @pytest.mark.asyncio
    async def test_creates_project(self, client):
        """Creates a project and returns project_id with 201 status."""
        resp = await client.post(
            "/api/v1/projects",
            json={"device_fingerprint": "abc-123", "has_lidar": True},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert "project_id" in body
        assert len(body["project_id"]) == 36  # UUID format

    @pytest.mark.asyncio
    async def test_missing_fingerprint_returns_422(self, client):
        """Missing required field returns 422 validation error."""
        resp = await client.post("/api/v1/projects", json={})
        assert resp.status_code == 422


class TestGetProjectState:
    """GET /api/v1/projects/{id}"""

    @pytest.mark.asyncio
    async def test_returns_initial_state(self, client, project_id):
        """Initial state has step='photos' and empty collections."""
        resp = await client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["step"] == "photos"
        assert body["photos"] == []
        assert body["iteration_count"] == 0
        assert body["approved"] is False

    @pytest.mark.asyncio
    async def test_not_found_returns_404(self, client):
        """Nonexistent project returns 404 with ErrorResponse shape."""
        resp = await client.get("/api/v1/projects/nonexistent-id")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "workflow_not_found"
        assert body["retryable"] is False


class TestDeleteProject:
    """DELETE /api/v1/projects/{id}"""

    @pytest.mark.asyncio
    async def test_deletes_project(self, client, project_id):
        """Deleting a project returns 204 and removes it."""
        resp = await client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 204
        # Verify it's gone
        resp2 = await client.get(f"/api/v1/projects/{project_id}")
        assert resp2.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client):
        """Deleting a nonexistent project returns 404."""
        resp = await client.delete("/api/v1/projects/nonexistent-id")
        assert resp.status_code == 404
        assert resp.json()["error"] == "workflow_not_found"


class TestPhotoUpload:
    """POST /api/v1/projects/{id}/photos"""

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_upload_photo(self, _mock_val, client, project_id):
        """Uploading a photo returns photo_id and validation result."""
        fake_file = io.BytesIO(b"\x89PNG\r\nfake image data")
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", fake_file, "image/jpeg")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "photo_id" in body
        assert body["validation"]["passed"] is True

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_photo_added_to_state(self, _mock_val, client, project_id):
        """Uploaded photo appears in workflow state when validation passes."""
        fake_file = io.BytesIO(b"data")
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", fake_file, "image/jpeg")},
        )
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert len(state.json()["photos"]) == 1

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_INVALID)
    async def test_rejected_photo_not_added_to_state(self, _mock_val, client, project_id):
        """Rejected photo returns passed=False and is NOT added to state."""
        fake_file = io.BytesIO(b"tiny")
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", fake_file, "image/jpeg")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["passed"] is False
        assert "photo_id" in body
        # Photo should NOT be in state
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert len(state.json()["photos"]) == 0

    @pytest.mark.asyncio
    async def test_file_too_large_returns_413(self, client, project_id):
        """File exceeding 20 MB limit returns 413 error."""
        big_file = io.BytesIO(b"\x00" * (20 * 1024 * 1024 + 1))
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("big.jpg", big_file, "image/jpeg")},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "file_too_large"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_photo_upload_wrong_step_returns_409(self, _mock_val, client, project_id):
        """Photo upload at wrong step returns 409 conflict."""
        _mock_states[project_id].step = "scan"
        fake_file = io.BytesIO(b"data")
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", fake_file, "image/jpeg")},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_photo_type_parameter(self, mock_val, client, project_id):
        """photo_type query param is forwarded to validation."""
        fake_file = io.BytesIO(b"data")
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", fake_file, "image/jpeg")},
            params={"photo_type": "inspiration"},
        )
        # Verify validation was called with correct photo_type
        call_input = mock_val.call_args[0][0]
        assert call_input.photo_type == "inspiration"
        # Verify photo stored with correct type
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["photos"][0]["photo_type"] == "inspiration"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_auto_transitions_to_scan_after_two_photos(self, _mock_val, client, project_id):
        """Step auto-transitions from photos to scan after 2nd valid photo."""
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{project_id}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "scan"
        assert len(body["photos"]) == 2

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_stays_in_photos_with_one_photo(self, _mock_val, client, project_id):
        """Step stays at photos with only 1 valid photo."""
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["step"] == "photos"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_INVALID)
    async def test_rejected_photo_does_not_trigger_transition(self, _mock_val, client, project_id):
        """Rejected photos don't count toward the 2-photo minimum."""
        # Upload 1 valid + 1 rejected = 1 in state, no transition
        _mock_states[project_id].photos.append(
            PhotoData(photo_id="existing", storage_key="s3://test", photo_type="room"),
        )
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["step"] == "photos"  # still photos, rejected doesn't count


class TestScanEndpoints:
    """POST /api/v1/projects/{id}/scan and scan/skip"""

    @pytest.mark.asyncio
    async def test_skip_scan_transitions_to_intake(self, client, project_id):
        """Skipping scan moves to intake step."""
        # First, manually move to scan step
        _mock_states[project_id].step = "scan"
        resp = await client.post(f"/api/v1/projects/{project_id}/scan/skip")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["step"] == "intake"

    @pytest.mark.asyncio
    async def test_skip_scan_leaves_scan_data_null(self, client, project_id):
        """Skipping scan preserves scan_data=null so T1 iOS knows scan was skipped.

        T1 iOS checks scan_data to determine whether to show room dimensions
        in the design brief. After skip_scan, scan_data must be null (not an
        empty ScanData or default object).
        """
        _mock_states[project_id].step = "scan"
        await client.post(f"/api/v1/projects/{project_id}/scan/skip")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"
        assert body["scan_data"] is None

    @pytest.mark.asyncio
    async def test_upload_scan_transitions_to_intake(self, client, project_id):
        """Uploading valid scan data parses dimensions and moves to intake."""
        _mock_states[project_id].step = "scan"
        scan_body = {
            "room": {"width": 4.5, "length": 6.0, "height": 2.7},
            "walls": [{"id": "wall_0", "width": 4.5, "height": 2.7}],
            "openings": [],
        }
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json=scan_body,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "intake"
        assert body["scan_data"] is not None
        assert body["scan_data"]["room_dimensions"]["width_m"] == 4.5
        assert body["scan_data"]["room_dimensions"]["length_m"] == 6.0
        assert body["scan_data"]["room_dimensions"]["height_m"] == 2.7

    @pytest.mark.asyncio
    async def test_upload_scan_invalid_data_returns_422(self, client, project_id):
        """Invalid scan data (missing room dimensions) returns 422."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"no_room_key": True},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_scan_data"

    @pytest.mark.asyncio
    async def test_upload_scan_wrong_step_returns_409(self, client, project_id):
        """Upload scan at wrong step returns 409 conflict."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_scan_wrong_step_returns_409(self, client, project_id):
        """Skip scan at wrong step returns 409 conflict."""
        # Project starts in 'photos' step, not 'scan'
        resp = await client.post(f"/api/v1/projects/{project_id}/scan/skip")
        assert resp.status_code == 409
        body = resp.json()
        assert body["error"] == "wrong_step"


class TestIntakeEndpoints:
    """POST /api/v1/projects/{id}/intake/*"""

    @pytest.mark.asyncio
    async def test_start_intake(self, client, project_id):
        """Starting intake returns agent message with options."""
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "agent_message" in body
        assert len(body["options"]) == 3
        assert body["progress"] == "Question 1 of 3"

    @pytest.mark.asyncio
    async def test_send_message(self, client, project_id):
        """First intake message returns style question (step 2 of conversation)."""
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "living room"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "living room" in body["agent_message"]
        assert body["progress"] == "Question 2 of 3"
        assert len(body["options"]) == 4

    @pytest.mark.asyncio
    async def test_send_message_validates_through_model(self, client, project_id):
        """send_intake_message response conforms to full IntakeChatOutput model.

        T1 iOS builds Swift models mirroring IntakeChatOutput. Validating
        just is_summary and partial_brief doesn't catch extra/missing fields.
        """
        from app.models.contracts import IntakeChatOutput

        _mock_states[project_id].step = "intake"
        # Send 3 messages to reach summary
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "living room"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "modern"},
        )
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "Replace the old couch"},
        )
        assert resp.status_code == 200
        output = IntakeChatOutput.model_validate(resp.json())
        assert output.is_summary is True
        assert output.partial_brief is not None
        assert output.agent_message != ""

    @pytest.mark.asyncio
    async def test_intake_conversation_flow(self, client, project_id):
        """Full 3-step intake conversation exercises all IntakeChatOutput fields.

        Step 1: room type → quick reply options
        Step 2: style → quick reply options
        Step 3: preferences → open-ended text
        Step 4: summary with partial brief
        """
        _mock_states[project_id].step = "intake"

        # Start intake initializes conversation
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        body = resp.json()
        assert body["progress"] == "Question 1 of 3"
        assert len(body["options"]) == 3

        # Message 1: room type → style question with options
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "bedroom"},
        )
        body = resp.json()
        assert "bedroom" in body["agent_message"]
        assert body["progress"] == "Question 2 of 3"
        assert len(body["options"]) == 4
        assert body["is_summary"] is False

        # Message 2: style → open-ended question
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "scandinavian"},
        )
        body = resp.json()
        assert body["progress"] == "Question 3 of 3"
        assert body["is_open_ended"] is True
        assert body["options"] is None

        # Message 3: preferences → summary with partial brief
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "Add more natural light and plants"},
        )
        body = resp.json()
        assert body["is_summary"] is True
        assert body["partial_brief"]["room_type"] == "bedroom"
        assert body["progress"] == "Summary"

    @pytest.mark.asyncio
    async def test_confirm_intake_transitions(self, client, project_id):
        """Confirming intake stores the brief, generates options, moves to selection."""
        _mock_states[project_id].step = "intake"
        brief_data = {"room_type": "bedroom", "pain_points": ["too dark"]}
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": brief_data},
        )
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "selection"
        assert len(body["generated_options"]) == 2
        assert body["design_brief"] is not None
        assert body["design_brief"]["room_type"] == "bedroom"
        assert body["design_brief"]["pain_points"] == ["too dark"]

    @pytest.mark.asyncio
    async def test_skip_intake_generates_options(self, client, project_id):
        """Skipping intake still generates options."""
        _mock_states[project_id].step = "intake"
        await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "selection"
        assert len(body["generated_options"]) == 2

    @pytest.mark.asyncio
    async def test_start_intake_wrong_step_returns_409(self, client, project_id):
        """Starting intake at wrong step (photos) returns 409."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_send_message_wrong_step_returns_409(self, client, project_id):
        """Sending intake message at wrong step returns 409."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "I want a cozy living room"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_confirm_intake_wrong_step_returns_409(self, client, project_id):
        """Confirming intake at wrong step returns 409."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "bedroom"}},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_skip_intake_wrong_step_returns_409(self, client, project_id):
        """Skipping intake at wrong step returns 409."""
        resp = await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_invalid_intake_mode_returns_422(self, client, project_id):
        """Invalid intake mode returns 422 validation error."""
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "invalid"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    @pytest.mark.asyncio
    async def test_missing_intake_message_returns_422(self, client, project_id):
        """Missing message field returns 422 validation error."""
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"


class TestSelectionEndpoints:
    """POST /api/v1/projects/{id}/select and start-over"""

    @pytest.mark.asyncio
    async def test_select_option(self, client, project_id):
        """Selecting an option sets current_image and moves to iteration."""
        # Set up state for selection
        _mock_states[project_id].step = "intake"
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )

        resp = await client.post(f"/api/v1/projects/{project_id}/select", json={"index": 0})
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "iteration"
        assert body["selected_option"] == 0
        assert body["current_image"] is not None

    @pytest.mark.asyncio
    async def test_select_option_out_of_bounds_returns_422(self, client, project_id):
        """Selecting index beyond generated_options returns 422."""
        _mock_states[project_id].step = "selection"
        _mock_states[project_id].generated_options = []  # empty list
        resp = await client.post(f"/api/v1/projects/{project_id}/select", json={"index": 0})
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_selection"

    @pytest.mark.asyncio
    async def test_select_wrong_step_returns_409(self, client, project_id):
        """Selecting from wrong step (photos) returns 409."""
        resp = await client.post(f"/api/v1/projects/{project_id}/select", json={"index": 0})
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_start_over_resets_to_intake(self, client, project_id):
        """Start over clears design state and goes back to intake."""
        _mock_states[project_id].step = "selection"
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["step"] == "intake"

    @pytest.mark.asyncio
    async def test_start_over_from_photos_step(self, client, project_id):
        """Start over from early step (no options generated) doesn't corrupt state."""
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "intake"
        assert body["generated_options"] == []
        assert body["selected_option"] is None
        assert body["design_brief"] is None

    @pytest.mark.asyncio
    async def test_start_over_from_iteration_resets_all_state(self, client, project_id):
        """Start over from iteration clears revision history, iteration count, and approval."""
        from app.models.contracts import DesignOption, GenerateShoppingListOutput, RevisionRecord

        state = _mock_states[project_id]
        state.step = "iteration"
        state.iteration_count = 3
        state.current_image = "https://r2.example.com/old.png"
        state.approved = True
        state.revision_history = [
            RevisionRecord(
                revision_number=1,
                type="annotation",
                base_image_url="https://r2.example.com/base.png",
                revised_image_url="https://r2.example.com/rev1.png",
            ),
        ]
        state.generated_options = [
            DesignOption(image_url="https://r2.example.com/opt.png", caption="Test"),
        ]
        state.shopping_list = GenerateShoppingListOutput(
            items=[],
            total_estimated_cost_cents=0,
        )

        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"
        assert body["iteration_count"] == 0
        assert body["revision_history"] == []
        assert body["approved"] is False
        assert body["generated_options"] == []
        assert body["selected_option"] is None
        assert body["current_image"] is None
        assert body["design_brief"] is None
        assert body["shopping_list"] is None

    @pytest.mark.asyncio
    async def test_start_over_preserves_photos_and_scan_data(
        self,
        client,
        project_id,
    ):
        """Start over resets design state but preserves photos and scan data.

        Users should never need to re-upload photos or rescan after starting
        over. This test verifies the mock API matches the workflow's behavior.
        """
        from app.models.contracts import PhotoData, RoomDimensions, ScanData

        state = _mock_states[project_id]
        state.step = "selection"
        state.photos = [
            PhotoData(
                photo_id="room-001",
                storage_key="projects/test/room.jpg",
                photo_type="room",
            ),
            PhotoData(
                photo_id="inspo-001",
                storage_key="projects/test/inspo.jpg",
                photo_type="inspiration",
            ),
        ]
        state.scan_data = ScanData(
            storage_key="scans/test.json",
            room_dimensions=RoomDimensions(
                width_m=5.0,
                length_m=7.0,
                height_m=2.8,
            ),
        )

        resp = await client.post(
            f"/api/v1/projects/{project_id}/start-over",
        )
        assert resp.status_code == 200

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()

        # Photos preserved
        assert len(body["photos"]) == 2
        assert body["photos"][0]["photo_id"] == "room-001"
        assert body["photos"][0]["photo_type"] == "room"
        assert body["photos"][1]["photo_id"] == "inspo-001"
        assert body["photos"][1]["photo_type"] == "inspiration"

        # Scan data preserved
        assert body["scan_data"] is not None
        assert body["scan_data"]["room_dimensions"]["width_m"] == 5.0

        # Design state reset
        assert body["step"] == "intake"
        assert body["generated_options"] == []
        assert body["design_brief"] is None

    @pytest.mark.asyncio
    async def test_start_over_clears_error(self, client, project_id):
        """Start over clears stale error so T1 iOS doesn't show confusing messages."""
        from app.models.contracts import WorkflowError

        _mock_states[project_id].step = "selection"
        _mock_states[project_id].error = WorkflowError(
            message="Invalid selection: option 99 does not exist",
            retryable=True,
        )
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"
        assert body["error"] is None

    @pytest.mark.asyncio
    async def test_start_over_resets_intake_conversation(self, client, project_id):
        """Start over resets intake conversation so user starts fresh.

        Without this, a user who completed 2 intake messages, then started over,
        would see question 3 instead of question 1 when re-entering intake.
        """
        _mock_states[project_id].step = "intake"

        # Complete partial conversation (2 messages)
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "living room"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "modern"},
        )

        # Move to selection (to enable start_over)
        _mock_states[project_id].step = "selection"
        _mock_states[project_id].generated_options = [
            DesignOption(image_url="https://r2.example.com/opt.png", caption="A"),
            DesignOption(image_url="https://r2.example.com/opt2.png", caption="B"),
        ]

        # Start over
        await client.post(f"/api/v1/projects/{project_id}/start-over")

        # Re-enter intake — should see question 1, not question 3
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        assert resp.json()["progress"] == "Question 1 of 3"

        # First message should be question 2 (fresh conversation)
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "bedroom"},
        )
        assert resp.json()["progress"] == "Question 2 of 3"


class TestIterationEndpoints:
    """POST /api/v1/projects/{id}/iterate/*"""

    @pytest.mark.asyncio
    async def test_annotation_edit(self, client, project_id):
        """Annotation edit produces a revision and increments iteration count."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace the sofa with a modern sectional",
                    }
                ]
            },
        )
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["iteration_count"] == 1
        assert len(body["revision_history"]) == 1
        assert body["revision_history"][0]["type"] == "annotation"

    @pytest.mark.asyncio
    async def test_text_feedback(self, client, project_id):
        """Text feedback produces a revision with type 'feedback'."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "Make it warmer and more cozy"},
        )
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["iteration_count"] == 1
        assert body["revision_history"][0]["type"] == "feedback"

    @pytest.mark.asyncio
    async def test_annotate_wrong_step_returns_409(self, client, project_id):
        """Annotation edit at wrong step (photos) returns 409."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace the old furniture",
                    }
                ]
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_feedback_wrong_step_returns_409(self, client, project_id):
        """Text feedback at wrong step (photos) returns 409."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "Make it warmer"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_missing_feedback_returns_422(self, client, project_id):
        """Missing feedback field returns 422 validation error."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    @pytest.mark.asyncio
    async def test_annotate_empty_annotations_returns_422(self, client, project_id):
        """Empty annotations array violates min_length=1 constraint."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={"annotations": []},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_annotate_short_instruction_returns_422(self, client, project_id):
        """Instruction below min_length=10 returns 422."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "short",
                    }
                ]
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_annotate_too_many_annotations_returns_422(self, client, project_id):
        """More than 3 annotations violates max_length=3 constraint."""
        _mock_states[project_id].step = "iteration"
        annotation = {
            "region_id": 1,
            "center_x": 0.5,
            "center_y": 0.5,
            "radius": 0.1,
            "instruction": "Replace this furniture piece",
        }
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [annotation, annotation, annotation, annotation],  # 4 > max 3
            },
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_current_image_tracks_through_revisions(
        self,
        client,
        project_id,
    ):
        """Verifies current_image chains correctly through selection and iterations.

        T1 iOS displays current_image as the active design preview. After
        selection, it should be the selected option's URL. After each
        iteration, it should update to the revision URL. This test verifies
        the mock API tracks current_image identically to the workflow.
        """
        # Set up for selection
        _mock_states[project_id].step = "intake"
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )

        # Select option 0 → current_image = option 0's image
        await client.post(
            f"/api/v1/projects/{project_id}/select",
            json={"index": 0},
        )
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "iteration"
        option_image = body["generated_options"][0]["image_url"]
        assert body["current_image"] == option_image

        # Annotation edit → current_image updates to revision 1
        await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace the old furniture",
                    }
                ],
            },
        )
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        rev1_image = body["revision_history"][0]["revised_image_url"]
        assert body["current_image"] == rev1_image
        assert body["current_image"] != option_image

        # Text feedback → current_image updates to revision 2
        await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "Make it brighter"},
        )
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        rev2_image = body["revision_history"][1]["revised_image_url"]
        assert body["current_image"] == rev2_image
        assert body["current_image"] != rev1_image

        # Chain integrity: rev2.base = rev1.revised
        assert body["revision_history"][1]["base_image_url"] == rev1_image

    @pytest.mark.asyncio
    async def test_fifth_iteration_auto_transitions_to_approval(self, client, project_id):
        """After 5 iterations, step auto-transitions from iteration to approval."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"

        for i in range(5):
            resp = await client.post(
                f"/api/v1/projects/{project_id}/iterate/feedback",
                json={"feedback": f"Iteration {i + 1} feedback"},
            )
            assert resp.status_code == 200

        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "approval"
        assert body["iteration_count"] == 5
        assert len(body["revision_history"]) == 5

    @pytest.mark.asyncio
    async def test_sixth_iteration_blocked_after_cap(self, client, project_id):
        """Cannot submit more iterations after reaching the 5-iteration cap."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"

        # Do 5 iterations (transitions to approval)
        for i in range(5):
            await client.post(
                f"/api/v1/projects/{project_id}/iterate/annotate",
                json={
                    "annotations": [
                        {
                            "region_id": 1,
                            "center_x": 0.5,
                            "center_y": 0.5,
                            "radius": 0.1,
                            "instruction": f"Iteration {i + 1} replace the furniture",
                        }
                    ]
                },
            )

        # 6th iteration should fail (step is now "approval", not "iteration")
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "This should be blocked by iteration cap",
                    }
                ]
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"


class TestApprovalEndpoints:
    """POST /api/v1/projects/{id}/approve"""

    @pytest.mark.asyncio
    async def test_approve_from_iteration(self, client, project_id):
        """Approving from iteration step sets approved=True and step=completed."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["approved"] is True
        assert body["step"] == "completed"
        assert body["shopping_list"] is not None

    @pytest.mark.asyncio
    async def test_approve_from_approval(self, client, project_id):
        """Approving from approval step (after 5 iterations) also works."""
        _mock_states[project_id].step = "approval"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["approved"] is True
        assert body["step"] == "completed"
        assert body["shopping_list"] is not None

    @pytest.mark.asyncio
    async def test_approve_populates_shopping_list_shape(self, client, project_id):
        """Approval populates shopping_list with correct shape for T1 iOS."""
        _mock_states[project_id].step = "iteration"
        await client.post(f"/api/v1/projects/{project_id}/approve")
        state = await client.get(f"/api/v1/projects/{project_id}")
        shopping = state.json()["shopping_list"]
        assert len(shopping["items"]) == 2
        assert shopping["total_estimated_cost_cents"] > 0
        # First item has all optional fields populated
        item0 = shopping["items"][0]
        assert item0["category_group"] == "Furniture"
        assert item0["image_url"] is not None
        assert item0["fit_status"] == "may_not_fit"
        assert item0["fit_detail"] is not None
        assert item0["dimensions"] is not None
        # Second item has optional fields as None (tests nil handling)
        item1 = shopping["items"][1]
        assert item1["image_url"] is None
        assert item1["fit_status"] is None
        # Unmatched items present for fallback UI
        assert len(shopping["unmatched"]) == 1
        assert shopping["unmatched"][0]["category"] == "Rug"
        assert "google_shopping_url" in shopping["unmatched"][0]

    @pytest.mark.asyncio
    async def test_approve_wrong_step_returns_409(self, client, project_id):
        """Approving from wrong step returns 409."""
        _mock_states[project_id].step = "photos"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"


class TestRetryEndpoint:
    """POST /api/v1/projects/{id}/retry"""

    @pytest.mark.asyncio
    async def test_retry_nonexistent_returns_404(self, client):
        """Retrying a nonexistent project returns 404."""
        resp = await client.post("/api/v1/projects/nonexistent-id/retry")
        assert resp.status_code == 404
        assert resp.json()["error"] == "workflow_not_found"

    @pytest.mark.asyncio
    async def test_retry_clears_error(self, client, project_id):
        """Retry clears the error field and returns ActionResponse."""
        from app.models.contracts import ActionResponse, WorkflowError

        _mock_states[project_id].error = WorkflowError(message="Generation failed", retryable=True)
        resp = await client.post(f"/api/v1/projects/{project_id}/retry")
        assert resp.status_code == 200
        ActionResponse.model_validate(resp.json())
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["error"] is None


class TestErrorResponseSchema:
    """Verify all error responses conform to the full ErrorResponse model.

    T1 iOS uses `retryable` and `message` from error responses to decide
    whether to show a retry button and what message to display. Validating
    just the `error` code (as most tests do) doesn't catch missing fields.
    """

    @pytest.mark.asyncio
    async def test_not_found_response_conforms(self, client):
        """404 not-found matches full ErrorResponse schema."""
        from app.models.contracts import ErrorResponse

        resp = await client.get("/api/v1/projects/nonexistent-id")
        assert resp.status_code == 404
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "workflow_not_found"
        assert er.retryable is False
        assert er.message != ""

    @pytest.mark.asyncio
    async def test_wrong_step_response_conforms(self, client, project_id):
        """409 wrong-step matches full ErrorResponse schema."""
        from app.models.contracts import ErrorResponse

        # project_id starts at "photos" step — skip_scan requires "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan/skip",
        )
        assert resp.status_code == 409
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "wrong_step"
        assert er.retryable is False
        assert "photos" in er.message  # mentions current step

    @pytest.mark.asyncio
    async def test_invalid_selection_response_conforms(
        self,
        client,
        project_id,
    ):
        """422 invalid-selection matches full ErrorResponse schema."""
        from app.models.contracts import ErrorResponse

        _mock_states[project_id].step = "selection"
        _mock_states[project_id].generated_options = []
        resp = await client.post(
            f"/api/v1/projects/{project_id}/select",
            json={"index": 0},
        )
        assert resp.status_code == 422
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "invalid_selection"
        assert er.retryable is False

    @pytest.mark.asyncio
    async def test_file_too_large_response_conforms(self, client, project_id):
        """413 file-too-large matches full ErrorResponse schema."""
        from app.models.contracts import ErrorResponse

        big_file = io.BytesIO(b"\x00" * (20 * 1024 * 1024 + 1))
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("big.jpg", big_file, "image/jpeg")},
        )
        assert resp.status_code == 413
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "file_too_large"
        assert er.retryable is False

    @pytest.mark.asyncio
    async def test_invalid_scan_data_response_conforms(self, client, project_id):
        """422 invalid-scan-data matches full ErrorResponse schema."""
        from app.models.contracts import ErrorResponse

        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"missing": "room key"},
        )
        assert resp.status_code == 422
        er = ErrorResponse.model_validate(resp.json())
        assert er.error == "invalid_scan_data"
        assert er.retryable is False


class TestNotFoundOnAllEndpoints:
    """Verify all project-scoped endpoints return 404 for nonexistent projects.

    Every endpoint uses _check_step which returns 404 when state is None.
    This test ensures the 404 path works for each endpoint individually,
    not just GET/DELETE/retry which had explicit tests.
    """

    _FAKE_ID = "nonexistent-project-id"

    @pytest.mark.asyncio
    async def test_photo_upload_returns_404(self, client):
        """Photo upload on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_scan_upload_returns_404(self, client):
        """Scan upload on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/scan",
            json={"rooms": [{"vertices": [[0, 0], [4, 0], [4, 3], [0, 3]], "height": 2.7}]},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_scan_skip_returns_404(self, client):
        """Skip scan on nonexistent project returns 404."""
        resp = await client.post(f"/api/v1/projects/{self._FAKE_ID}/scan/skip")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_intake_start_returns_404(self, client):
        """Start intake on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/intake/start",
            json={"mode": "quick"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_intake_message_returns_404(self, client):
        """Send intake message on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/intake/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_intake_confirm_returns_404(self, client):
        """Confirm intake on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_intake_skip_returns_404(self, client):
        """Skip intake on nonexistent project returns 404."""
        resp = await client.post(f"/api/v1/projects/{self._FAKE_ID}/intake/skip")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_select_returns_404(self, client):
        """Select option on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/select",
            json={"index": 0},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_start_over_returns_404(self, client):
        """Start over on nonexistent project returns 404."""
        resp = await client.post(f"/api/v1/projects/{self._FAKE_ID}/start-over")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_annotate_returns_404(self, client):
        """Annotation edit on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace the old couch with something modern",
                    }
                ]
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_feedback_returns_404(self, client):
        """Text feedback on nonexistent project returns 404."""
        resp = await client.post(
            f"/api/v1/projects/{self._FAKE_ID}/iterate/feedback",
            json={"feedback": "Make it brighter"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_returns_404(self, client):
        """Approve on nonexistent project returns 404."""
        resp = await client.post(f"/api/v1/projects/{self._FAKE_ID}/approve")
        assert resp.status_code == 404


class TestProjectIsolation:
    """Verify that concurrent projects don't share state."""

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_two_projects_independent_state(self, _mock_val, client):
        """Two projects at different steps have fully independent state."""
        # Create two projects
        resp1 = await client.post("/api/v1/projects", json={"device_fingerprint": "dev-1"})
        pid1 = resp1.json()["project_id"]

        resp2 = await client.post("/api/v1/projects", json={"device_fingerprint": "dev-2"})
        pid2 = resp2.json()["project_id"]

        # Advance project 1 through photos to scan
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid1}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )

        # Project 1 is at "scan", project 2 still at "photos"
        state1 = (await client.get(f"/api/v1/projects/{pid1}")).json()
        state2 = (await client.get(f"/api/v1/projects/{pid2}")).json()
        assert state1["step"] == "scan"
        assert state2["step"] == "photos"

        # Project 1's photos don't bleed into project 2
        assert len(state1["photos"]) == 2
        assert len(state2["photos"]) == 0

        # Advance project 2 independently — upload 1 photo
        await client.post(
            f"/api/v1/projects/{pid2}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        state2 = (await client.get(f"/api/v1/projects/{pid2}")).json()
        assert state2["step"] == "photos"
        assert len(state2["photos"]) == 1

        # Project 1 still at scan with its own photos
        state1 = (await client.get(f"/api/v1/projects/{pid1}")).json()
        assert state1["step"] == "scan"
        assert len(state1["photos"]) == 2

    @pytest.mark.asyncio
    async def test_delete_one_project_preserves_other(self, client):
        """Deleting project A doesn't affect project B."""
        resp1 = await client.post("/api/v1/projects", json={"device_fingerprint": "dev-1"})
        pid1 = resp1.json()["project_id"]

        resp2 = await client.post("/api/v1/projects", json={"device_fingerprint": "dev-2"})
        pid2 = resp2.json()["project_id"]

        # Delete project 1
        resp = await client.delete(f"/api/v1/projects/{pid1}")
        assert resp.status_code == 204

        # Project 2 still accessible
        resp = await client.get(f"/api/v1/projects/{pid2}")
        assert resp.status_code == 200
        assert resp.json()["step"] == "photos"

        # Project 1 gone
        resp = await client.get(f"/api/v1/projects/{pid1}")
        assert resp.status_code == 404


class TestFullFlow:
    """End-to-end test: create -> photos -> scan skip -> intake -> select -> iterate -> approve."""

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_happy_path(self, _mock_val, client):
        """Complete flow through all mock endpoints."""
        # 1. Create project
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-e2e"})
        pid = resp.json()["project_id"]

        # 2. Upload 2 photos (auto-transitions to scan after 2nd)
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )

        # 3. Verify auto-transition to scan, then skip
        assert _mock_states[pid].step == "scan"
        await client.post(f"/api/v1/projects/{pid}/scan/skip")

        # 5. Start intake → conversation flow
        resp = await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        assert resp.json()["progress"] == "Question 1 of 3"

        # 5a. Answer room type
        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        assert resp.json()["progress"] == "Question 2 of 3"

        # 5b. Answer style
        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "modern"},
        )
        assert resp.json()["is_open_ended"] is True

        # 5c. Answer preferences → summary
        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "Replace the couch and add plants"},
        )
        assert resp.json()["is_summary"] is True
        assert resp.json()["partial_brief"]["room_type"] == "living room"

        # 6. Confirm intake (generates options)
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )

        # 7. Select option 0
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # 8. Annotation edit
        await client.post(
            f"/api/v1/projects/{pid}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace the old coffee table",
                    }
                ]
            },
        )

        # 9. Approve design
        await client.post(f"/api/v1/projects/{pid}/approve")

        # Verify final state
        state = await client.get(f"/api/v1/projects/{pid}")
        body = state.json()
        assert body["step"] == "completed"
        assert body["approved"] is True
        assert len(body["photos"]) == 2
        assert body["iteration_count"] == 1
        assert len(body["revision_history"]) == 1
        assert body["shopping_list"] is not None
        assert len(body["shopping_list"]["items"]) == 2

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_response_schema_fidelity(self, _mock_val, client):
        """All responses parse through their respective Pydantic models.

        T1 iOS builds Swift models mirroring the Pydantic contracts. If the
        mock API returns fields or shapes that don't match the models,
        T1 would build against incorrect types. This test validates both
        WorkflowState (GET) and ActionResponse (POST) at every step.
        """
        from app.models.contracts import (
            ActionResponse,
            CreateProjectResponse,
            IntakeChatOutput,
            PhotoUploadResponse,
            WorkflowState,
        )

        # Create project (validate CreateProjectResponse)
        resp = await client.post(
            "/api/v1/projects",
            json={"device_fingerprint": "test-schema"},
        )
        cr = CreateProjectResponse.model_validate(resp.json())
        pid = cr.project_id

        # Step: photos — validate initial state
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "photos"

        # Upload photos → scan (validate PhotoUploadResponse)
        for i in range(2):
            resp = await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={
                    "file": (
                        f"room_{i}.jpg",
                        io.BytesIO(b"img"),
                        "image/jpeg",
                    ),
                },
            )
            PhotoUploadResponse.model_validate(resp.json())
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "scan"

        # Skip scan → intake (validate ActionResponse)
        resp = await client.post(f"/api/v1/projects/{pid}/scan/skip")
        ActionResponse.model_validate(resp.json())
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "intake"

        # Start intake — validate IntakeChatOutput
        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/start",
            json={"mode": "quick"},
        )
        IntakeChatOutput.model_validate(resp.json())

        # Confirm intake → selection (validate ActionResponse)
        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        ActionResponse.model_validate(resp.json())
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "selection"
        assert len(ws.generated_options) == 2

        # Select → iteration (validate ActionResponse)
        resp = await client.post(
            f"/api/v1/projects/{pid}/select",
            json={"index": 0},
        )
        ActionResponse.model_validate(resp.json())
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "iteration"

        # Approve → completed (validate ActionResponse)
        resp = await client.post(f"/api/v1/projects/{pid}/approve")
        ActionResponse.model_validate(resp.json())
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        ws = WorkflowState.model_validate(body)
        assert ws.step == "completed"
        assert ws.shopping_list is not None
