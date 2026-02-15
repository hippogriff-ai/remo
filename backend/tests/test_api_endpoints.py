"""Integration tests for all FastAPI endpoints.

Tests the full mock API flow: create project -> upload photos -> scan ->
intake -> select -> iterate -> approve. Verifies status codes, response
shapes, and step transition logic.
"""

import io
import time
from unittest.mock import AsyncMock, patch

import pytest

import app.api.routes.projects as _projects_mod
from app.api.routes.projects import (
    _intake_sessions,
    _mock_pending_generation,
    _mock_pending_shopping,
    _mock_states,
)
from app.models.contracts import (
    ChatMessage,
    DesignBrief,
    DesignOption,
    IntakeChatOutput,
    PhotoData,
    QuickReplyOption,
    RoomAnalysis,
    RoomContext,
    ValidatePhotoOutput,
)

# Reusable mock validation results
_VALID = ValidatePhotoOutput(passed=True, failures=[], messages=["Photo looks great!"])
_INVALID = ValidatePhotoOutput(
    passed=False,
    failures=["low_resolution"],
    messages=["This photo is too low resolution. Please use a higher quality image."],
)


@pytest.fixture(autouse=True)
def clear_mock_state():
    """Reset mock state and set delays to 0 for instant transitions."""
    _mock_states.clear()
    _mock_pending_generation.clear()
    _mock_pending_shopping.clear()
    _intake_sessions.clear()
    _projects_mod.MOCK_GENERATION_DELAY = 0.0
    _projects_mod.MOCK_SHOPPING_DELAY = 0.0
    yield
    _mock_states.clear()
    _mock_pending_generation.clear()
    _mock_pending_shopping.clear()
    _intake_sessions.clear()
    _projects_mod.MOCK_GENERATION_DELAY = 2.0
    _projects_mod.MOCK_SHOPPING_DELAY = 2.0


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

    @pytest.mark.asyncio
    async def test_delete_during_generation_cleans_up(self, client, project_id):
        """Deleting project during generation step cleans up pending generation state.

        Ensures _mock_pending_generation dict is cleaned up so memory
        doesn't leak for long-running test suites or API instances.
        """
        _mock_states[project_id].step = "generation"
        _mock_pending_generation[project_id] = (
            time.monotonic() + 9999,
            [],
        )
        resp = await client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 204
        assert project_id not in _mock_pending_generation

    @pytest.mark.asyncio
    async def test_delete_cleans_up_intake_session(self, client, project_id):
        """Deleting project during intake cleans up intake session state."""
        _mock_states[project_id].step = "intake"
        # Start intake to create a session
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        assert project_id in _intake_sessions
        resp = await client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 204
        assert project_id not in _intake_sessions


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
        """Photo upload at wrong step (past scan) returns 409 conflict."""
        _mock_states[project_id].step = "intake"
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
        """photo_type form field is forwarded to validation."""
        fake_file = io.BytesIO(b"data")
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", fake_file, "image/jpeg")},
            data={"photo_type": "inspiration"},
        )
        # Verify validation was called with correct photo_type
        call_input = mock_val.call_args[0][0]
        assert call_input.photo_type == "inspiration"
        # Verify photo stored with correct type
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["photos"][0]["photo_type"] == "inspiration"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_two_photos_stays_in_photos_until_confirmed(self, _mock_val, client, project_id):
        """Step stays at photos after 2 room photos until explicitly confirmed."""
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{project_id}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        state = await client.get(f"/api/v1/projects/{project_id}")
        body = state.json()
        assert body["step"] == "photos"
        assert len(body["photos"]) == 2

        # Now confirm -> transitions to scan
        resp = await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
        assert resp.status_code == 200
        state = await client.get(f"/api/v1/projects/{project_id}")
        assert state.json()["step"] == "scan"

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

    @pytest.mark.asyncio
    async def test_fourth_inspiration_photo_rejected(self, client, project_id):
        """PHOTO-10: 4th inspiration photo is blocked with 422.

        Covers product spec PHOTO-10: "Maximum 3 inspiration photos".
        The limit is checked before validation, so no mock needed.
        """
        # Pre-populate 3 inspiration photos
        for i in range(3):
            _mock_states[project_id].photos.append(
                PhotoData(
                    photo_id=f"inspo-{i}",
                    storage_key=f"s3://test/inspo-{i}.jpg",
                    photo_type="inspiration",
                ),
            )
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo4.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "too_many_inspiration_photos"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_room_photo_not_limited_by_inspiration_cap(self, _mock_val, client, project_id):
        """Room photos are unaffected by the inspiration limit.

        Verifies that max inspiration limit doesn't block room uploads.
        """
        for i in range(3):
            _mock_states[project_id].photos.append(
                PhotoData(
                    photo_id=f"inspo-{i}",
                    storage_key=f"s3://test/inspo-{i}.jpg",
                    photo_type="inspiration",
                ),
            )
        # Room photo should still work
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "room"},
        )
        assert resp.status_code == 200

    # --- PHOTO-7: inspiration photo notes ---

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_inspiration_photo_with_note(self, _mock_val, client, project_id):
        """PHOTO-7: Inspiration photo note is stored on PhotoData."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration", "note": "Love the warm lighting"},
        )
        assert resp.status_code == 200
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        inspo = [p for p in state["photos"] if p["photo_type"] == "inspiration"]
        assert len(inspo) == 1
        assert inspo[0]["note"] == "Love the warm lighting"

    @pytest.mark.asyncio
    async def test_note_on_room_photo_returns_422(self, client, project_id):
        """Notes are only allowed on inspiration photos."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "room", "note": "some note"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "note_not_allowed"

    @pytest.mark.asyncio
    async def test_note_exceeding_200_chars_returns_422(self, client, project_id):
        """Inspiration photo note must be 200 characters or fewer."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration", "note": "x" * 201},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "note_too_long"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_inspiration_photo_without_note(self, _mock_val, client, project_id):
        """Inspiration photo without note has note=null."""
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration"},
        )
        assert resp.status_code == 200
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        inspo = [p for p in state["photos"] if p["photo_type"] == "inspiration"]
        assert inspo[0]["note"] is None

    # --- Photo uploads during scan step ---

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_inspiration_upload_during_scan_step(self, _mock_val, client, project_id):
        """Users can add inspiration photos after auto-transition to scan."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration"},
        )
        assert resp.status_code == 200
        # Step stays scan (auto-transition only fires from "photos")
        assert _mock_states[project_id].step == "scan"

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_room_upload_during_scan_step(self, _mock_val, client, project_id):
        """Additional room photos can be uploaded during scan step."""
        _mock_states[project_id].step = "scan"
        _mock_states[project_id].photos = [
            PhotoData(photo_id="r0", storage_key="s3://r0.jpg", photo_type="room"),
            PhotoData(photo_id="r1", storage_key="s3://r1.jpg", photo_type="room"),
        ]
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("room3.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        assert resp.status_code == 200
        assert len(_mock_states[project_id].photos) == 3

    @pytest.mark.asyncio
    async def test_upload_blocked_during_intake_step(self, client, project_id):
        """Photo uploads are blocked once past the scan step."""
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("late.jpg", io.BytesIO(b"img"), "image/jpeg")},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"


class TestPhotoDelete:
    """DELETE /api/v1/projects/{id}/photos/{photoId} — INT-3."""

    @pytest.mark.asyncio
    async def test_delete_existing_photo(self, client, project_id):
        """Deleting an existing photo returns 204 and removes it from state.

        Covers INT-3 TDD criterion: Delete existing photo -> 204, photo removed.
        """
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="photo-to-delete",
                storage_key="s3://test/room.jpg",
                photo_type="room",
            ),
        )
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/photo-to-delete")
        assert resp.status_code == 204
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert len(state["photos"]) == 0

    @pytest.mark.asyncio
    async def test_delete_nonexistent_photo(self, client, project_id):
        """Deleting a nonexistent photo returns 404 with photo_not_found.

        Covers INT-3 TDD criterion: Delete nonexistent photo -> 404.
        """
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/no-such-photo")
        assert resp.status_code == 404
        assert resp.json()["error"] == "photo_not_found"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_project(self, client):
        """Deleting a photo from a nonexistent project returns 404.

        Covers INT-3 TDD criterion: Delete nonexistent project -> 404.
        """
        resp = await client.delete("/api/v1/projects/nonexistent-id/photos/any-photo")
        assert resp.status_code == 404
        assert resp.json()["error"] == "workflow_not_found"

    @pytest.mark.asyncio
    async def test_delete_photo_wrong_step(self, client, project_id):
        """Deleting a photo after the scan step returns 409.

        Covers INT-3 TDD criterion: Delete after step past photos/scan -> 409.
        """
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(photo_id="photo-1", storage_key="s3://test/room.jpg", photo_type="room"),
        )
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/photo-1")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_delete_photo_during_scan_step(self, client, project_id):
        """Deleting a room photo during scan step succeeds; step stays at scan (forward-only)."""
        _mock_states[project_id].step = "scan"
        _mock_states[project_id].photos = [
            PhotoData(photo_id="room-1", storage_key="s3://test/room1.jpg", photo_type="room"),
            PhotoData(photo_id="room-2", storage_key="s3://test/room2.jpg", photo_type="room"),
        ]
        # Delete one room photo — step stays at scan (matches Temporal forward-only behavior)
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/room-2")
        assert resp.status_code == 204
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "scan"
        assert len(state["photos"]) == 1

    @pytest.mark.asyncio
    async def test_delete_inspiration_during_scan_keeps_step(self, client, project_id):
        """Deleting an inspiration photo during scan keeps step at scan."""
        _mock_states[project_id].step = "scan"
        _mock_states[project_id].photos = [
            PhotoData(photo_id="room-1", storage_key="s3://test/room1.jpg", photo_type="room"),
            PhotoData(photo_id="room-2", storage_key="s3://test/room2.jpg", photo_type="room"),
            PhotoData(
                photo_id="inspo-1", storage_key="s3://test/inspo.jpg", photo_type="inspiration"
            ),
        ]
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/inspo-1")
        assert resp.status_code == 204
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "scan"
        assert len(state["photos"]) == 2

    @pytest.mark.asyncio
    async def test_delete_preserves_other_photos(self, client, project_id):
        """Deleting one photo leaves other photos intact."""
        _mock_states[project_id].photos = [
            PhotoData(
                photo_id="keep-me",
                storage_key="s3://test/room1.jpg",
                photo_type="room",
            ),
            PhotoData(
                photo_id="delete-me",
                storage_key="s3://test/room2.jpg",
                photo_type="room",
            ),
            PhotoData(
                photo_id="keep-me-too",
                storage_key="s3://test/inspo.jpg",
                photo_type="inspiration",
            ),
        ]
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/delete-me")
        assert resp.status_code == 204
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert len(state["photos"]) == 2
        photo_ids = [p["photo_id"] for p in state["photos"]]
        assert "keep-me" in photo_ids
        assert "keep-me-too" in photo_ids
        assert "delete-me" not in photo_ids

    @pytest.mark.asyncio
    async def test_delete_same_photo_twice(self, client, project_id):
        """Deleting the same photo twice returns 404 on the second attempt."""
        _mock_states[project_id].photos.append(
            PhotoData(photo_id="once-only", storage_key="s3://test/room.jpg", photo_type="room"),
        )
        resp1 = await client.delete(f"/api/v1/projects/{project_id}/photos/once-only")
        assert resp1.status_code == 204
        resp2 = await client.delete(f"/api/v1/projects/{project_id}/photos/once-only")
        assert resp2.status_code == 404
        assert resp2.json()["error"] == "photo_not_found"


class TestPhotoNote:
    """PATCH /api/v1/projects/{id}/photos/{photoId}/note"""

    @pytest.mark.asyncio
    async def test_update_note_on_inspiration_photo(self, client, project_id):
        """Setting a note on an inspiration photo returns 200 and persists."""
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-1",
                storage_key="s3://test/inspo.jpg",
                photo_type="inspiration",
            ),
        )
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/inspo-1/note",
            json={"note": "Love the color palette"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        inspo = next(p for p in state["photos"] if p["photo_id"] == "inspo-1")
        assert inspo["note"] == "Love the color palette"

    @pytest.mark.asyncio
    async def test_clear_note(self, client, project_id):
        """Setting note to null clears it."""
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-2",
                storage_key="s3://test/inspo2.jpg",
                photo_type="inspiration",
                note="old note",
            ),
        )
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/inspo-2/note",
            json={"note": None},
        )
        assert resp.status_code == 200
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        inspo = next(p for p in state["photos"] if p["photo_id"] == "inspo-2")
        assert inspo["note"] is None

    @pytest.mark.asyncio
    async def test_note_on_room_photo_rejected(self, client, project_id):
        """Setting a note on a room photo returns 422."""
        _mock_states[project_id].photos.append(
            PhotoData(photo_id="room-1", storage_key="s3://test/room.jpg", photo_type="room"),
        )
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/room-1/note",
            json={"note": "some note"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "note_not_allowed"

    @pytest.mark.asyncio
    async def test_note_too_long_rejected(self, client, project_id):
        """A note longer than 200 chars is rejected."""
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-3",
                storage_key="s3://test/inspo3.jpg",
                photo_type="inspiration",
            ),
        )
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/inspo-3/note",
            json={"note": "x" * 201},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "note_too_long"

    @pytest.mark.asyncio
    async def test_note_on_nonexistent_photo(self, client, project_id):
        """Updating note on nonexistent photo returns 404."""
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/no-such-photo/note",
            json={"note": "test"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_note_wrong_step(self, client, project_id):
        """Updating note after generation step returns 409."""
        _mock_states[project_id].step = "generation"
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-4",
                storage_key="s3://test/inspo4.jpg",
                photo_type="inspiration",
            ),
        )
        resp = await client.patch(
            f"/api/v1/projects/{project_id}/photos/inspo-4/note",
            json={"note": "test"},
        )
        assert resp.status_code == 409


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

    @pytest.mark.asyncio
    async def test_upload_scan_oversized_payload_returns_413(self, client, project_id):
        """G8: Scan payload exceeding 1 MB limit is rejected."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
            headers={"content-length": str(2 * 1024 * 1024)},  # 2 MB
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "scan_too_large"

    @pytest.mark.asyncio
    async def test_upload_scan_malformed_content_length_returns_400(self, client, project_id):
        """G8: Malformed Content-Length header returns 400 (not 500)."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
            headers={"content-length": "not-a-number"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    @pytest.mark.asyncio
    async def test_upload_scan_negative_content_length_returns_400(self, client, project_id):
        """G24: Negative Content-Length header returns 400."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
            headers={"content-length": "-1024"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "bad_request"

    @pytest.mark.asyncio
    async def test_upload_scan_after_skip_returns_409(self, client, project_id):
        """G18: Uploading scan after skip returns 409 (already past scan step)."""
        _mock_states[project_id].step = "scan"
        # Skip scan first
        resp = await client.post(f"/api/v1/projects/{project_id}/scan/skip")
        assert resp.status_code == 200

        # Now try to upload scan — should be rejected (step is 'intake')
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_double_submit_scan_returns_409(self, client, project_id):
        """G18: Submitting scan a second time returns 409 (already past scan step)."""
        _mock_states[project_id].step = "scan"
        scan_body = {"room": {"width": 4.0, "length": 5.0, "height": 2.5}}

        # First scan succeeds
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json=scan_body,
        )
        assert resp.status_code == 200

        # Second scan fails — step is now 'intake'
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json=scan_body,
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_upload_scan_exactly_1mb_accepted(self, client, project_id):
        """G8: Scan payload at exactly 1 MB boundary should be accepted (not 413)."""
        _mock_states[project_id].step = "scan"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/scan",
            json={"room": {"width": 4.0, "length": 5.0, "height": 2.5}},
            headers={"content-length": str(1 * 1024 * 1024)},  # exactly 1 MB
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_upload_scan_without_analysis_leaves_room_context_null(self, client, project_id):
        """G25: Scan alone (no photo analysis) does not build room_context.

        Mirrors the real workflow's _build_room_context() which early-returns
        when room_analysis is None. The mock doesn't run photo analysis, so
        room_context stays None until analysis completes.
        """
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
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        # scan_data is populated
        assert body["scan_data"] is not None
        assert body["scan_data"]["room_dimensions"]["width_m"] == 4.5
        # room_context stays None (no analysis yet, matches real workflow)
        assert body["room_context"] is None

    @pytest.mark.asyncio
    async def test_upload_scan_with_analysis_builds_room_context(self, client, project_id):
        """G25: Scan + pre-existing analysis → full room_context with ["photos", "lidar"].

        Mirrors the real workflow's _build_room_context() when analysis has
        completed before scan arrives. Also verifies estimated_dimensions is
        overwritten with LiDAR-precise measurements.
        """
        state = _mock_states[project_id]
        state.step = "scan"
        state.room_analysis = RoomAnalysis(
            room_type="living room",
            estimated_dimensions="approximately 15x20 feet",
        )
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
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        ctx = body["room_context"]
        assert ctx is not None
        assert ctx["enrichment_sources"] == ["photos", "lidar"]
        assert ctx["room_dimensions"]["width_m"] == 4.5
        assert ctx["room_dimensions"]["length_m"] == 6.0
        assert ctx["room_dimensions"]["height_m"] == 2.7
        assert ctx["photo_analysis"] is not None
        assert ctx["photo_analysis"]["room_type"] == "living room"
        # estimated_dimensions overwritten with LiDAR-precise values
        assert ctx["photo_analysis"]["estimated_dimensions"] == ("4.5m x 6.0m (ceiling 2.7m)")

    @pytest.mark.asyncio
    async def test_skip_scan_leaves_room_context_null(self, client, project_id):
        """G25: Skipping scan does not populate room_context."""
        _mock_states[project_id].step = "scan"
        await client.post(f"/api/v1/projects/{project_id}/scan/skip")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["room_context"] is None


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
        assert len(body["options"]) == 6
        assert body["progress"] == "Question 1 of 3"

    @pytest.mark.asyncio
    async def test_send_message(self, client, project_id):
        """First intake message returns style question (step 2 of conversation)."""
        _mock_states[project_id].step = "intake"
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
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
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
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
        assert len(body["options"]) == 6

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
        """Skipping intake still generates options (requires inspiration photos)."""
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-0", storage_key="s3://test/inspo.jpg", photo_type="inspiration"
            ),
        )
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

    # --- INTAKE-3a: skip-intake guard ---

    @pytest.mark.asyncio
    async def test_skip_intake_without_inspiration_returns_422(self, client, project_id):
        """INTAKE-3a: Skipping intake without inspiration photos is blocked."""
        _mock_states[project_id].step = "intake"
        # No inspiration photos in state
        resp = await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        assert resp.status_code == 422
        assert resp.json()["error"] == "intake_required"

    @pytest.mark.asyncio
    async def test_skip_intake_with_room_photos_only_returns_422(self, client, project_id):
        """Room photos alone don't satisfy the inspiration requirement for skip."""
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(photo_id="room-0", storage_key="s3://test/room.jpg", photo_type="room"),
        )
        resp = await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        assert resp.status_code == 422
        assert resp.json()["error"] == "intake_required"

    @pytest.mark.asyncio
    async def test_skip_intake_with_inspiration_photo_succeeds(self, client, project_id):
        """INTAKE-3a: Skipping intake with inspiration photos is allowed."""
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-0", storage_key="s3://test/inspo.jpg", photo_type="inspiration"
            ),
        )
        resp = await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        assert resp.status_code == 200

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

    @pytest.mark.asyncio
    async def test_send_message_without_start_returns_409(self, client, project_id):
        """Sending intake message at intake step without calling start_intake first returns 409.

        Distinct from test_send_message_wrong_step_returns_409 which tests the wrong-step
        guard. This tests the mock-mode "no session" guard — step is correct (intake) but
        the conversation session hasn't been initialized via start_intake.
        """
        _mock_states[project_id].step = "intake"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "I want a cozy living room"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"
        assert "start_intake" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_session_recovery_from_client_history(self, client, project_id):
        """Intake session recovers from client-provided history after API restart."""
        _mock_states[project_id].step = "intake"
        # Start and send first message normally
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        resp1 = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "bedroom"},
        )
        assert resp1.status_code == 200

        # Simulate API restart: clear in-memory sessions
        _intake_sessions.pop(project_id, None)

        # Send next message with client-provided history (iOS resends conversation)
        resp2 = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={
                "message": "modern",
                "conversation_history": [
                    {"role": "user", "content": "bedroom"},
                    {"role": "assistant", "content": resp1.json()["agent_message"]},
                ],
                "mode": "quick",
            },
        )
        assert resp2.status_code == 200
        body = resp2.json()
        # Should be step 3 (open-ended), not step 1 (room type)
        assert body["progress"] == "Question 3 of 3"

    @pytest.mark.asyncio
    async def test_session_lost_without_history_returns_409(self, client, project_id):
        """When session is lost and client sends no history, 409 is returned."""
        _mock_states[project_id].step = "intake"
        await client.post(
            f"/api/v1/projects/{project_id}/intake/start",
            json={"mode": "quick"},
        )
        # Simulate API restart
        _intake_sessions.pop(project_id, None)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "bedroom"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"


class TestMockGenerationStep:
    """GAP-5: Verify generation step is visible between intake and selection."""

    @pytest.mark.asyncio
    async def test_confirm_intake_shows_generation_step(self, client, project_id):
        """After confirm_intake with delay, step is 'generation' and options are empty.

        Covers GAP-5 TDD criterion: state.step == 'generation', options empty.
        """
        _mock_states[project_id].step = "intake"
        # Use a long delay so generation doesn't auto-complete
        _projects_mod.MOCK_GENERATION_DELAY = 9999.0
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "generation"
        assert state["generated_options"] == []

    @pytest.mark.asyncio
    async def test_generation_completes_after_delay(self, client, project_id):
        """After delay elapsed, polling transitions to 'selection' with options.

        Covers GAP-5 TDD criterion: after polling delay, step == 'selection'
        with 2 generated_options.
        """
        _mock_states[project_id].step = "intake"
        # Set delay to 0 so generation completes on next poll
        _projects_mod.MOCK_GENERATION_DELAY = 0.0
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "selection"
        assert len(state["generated_options"]) == 2

    @pytest.mark.asyncio
    async def test_skip_intake_shows_generation_step(self, client, project_id):
        """skip_intake follows the same two-step transition.

        Covers GAP-5 TDD criterion: skip_intake shows generation then selection.
        """
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-0", storage_key="s3://test/inspo.jpg", photo_type="inspiration"
            ),
        )
        _projects_mod.MOCK_GENERATION_DELAY = 9999.0
        await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "generation"
        assert state["generated_options"] == []

    @pytest.mark.asyncio
    async def test_skip_intake_generation_completes(self, client, project_id):
        """skip_intake generation completes on next poll with delay=0."""
        _mock_states[project_id].step = "intake"
        _mock_states[project_id].photos.append(
            PhotoData(
                photo_id="inspo-0", storage_key="s3://test/inspo.jpg", photo_type="inspiration"
            ),
        )
        _projects_mod.MOCK_GENERATION_DELAY = 0.0
        await client.post(f"/api/v1/projects/{project_id}/intake/skip")
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "selection"
        assert len(state["generated_options"]) == 2

    @pytest.mark.asyncio
    async def test_design_brief_preserved_through_generation(
        self,
        client,
        project_id,
    ):
        """Design brief is stored even while step is 'generation'."""
        _mock_states[project_id].step = "intake"
        _projects_mod.MOCK_GENERATION_DELAY = 9999.0
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "bedroom", "pain_points": ["too dark"]}},
        )
        state = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert state["step"] == "generation"
        assert state["design_brief"]["room_type"] == "bedroom"


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
    async def test_select_negative_index_returns_422(self, client, project_id):
        """Pydantic ge=0 constraint rejects negative index before endpoint code runs."""
        _mock_states[project_id].step = "selection"
        resp = await client.post(f"/api/v1/projects/{project_id}/select", json={"index": -1})
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    @pytest.mark.asyncio
    async def test_select_index_too_high_returns_422(self, client, project_id):
        """Pydantic le=1 constraint rejects index > 1 before endpoint code runs."""
        _mock_states[project_id].step = "selection"
        resp = await client.post(f"/api/v1/projects/{project_id}/select", json={"index": 2})
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

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
        """Start over from iteration clears revision history, iteration count, etc."""
        from app.models.contracts import DesignOption, RevisionRecord

        state = _mock_states[project_id]
        state.step = "iteration"
        state.iteration_count = 3
        state.current_image = "https://r2.example.com/old.png"
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
    async def test_start_over_blocked_after_approval(self, client, project_id):
        """Start over returns 409 once design is approved."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].approved = True
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_start_over_blocked_from_completed(self, client, project_id):
        """Start over returns 409 from completed step."""
        _mock_states[project_id].step = "completed"
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_start_over_blocked_from_shopping(self, client, project_id):
        """Start over returns 409 from shopping step (waiting for shopping list).

        Users cannot restart while the shopping list is being generated.
        The only way forward from shopping is to wait for completion.
        """
        _mock_states[project_id].step = "shopping"
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_start_over_from_scan_step(self, client, project_id):
        """Start over from scan step resets to intake, preserving photos."""
        from app.models.contracts import PhotoData

        state = _mock_states[project_id]
        state.step = "scan"
        state.photos = [
            PhotoData(photo_id="p0", storage_key="photos/room_0.jpg", photo_type="room"),
            PhotoData(photo_id="p1", storage_key="photos/room_1.jpg", photo_type="room"),
        ]
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"
        # Photos preserved across start-over
        assert len(body["photos"]) == 2

    @pytest.mark.asyncio
    async def test_start_over_from_approval_not_approved(self, client, project_id):
        """Start over from approval step (before approve signal) succeeds.

        Distinct from test_start_over_blocked_after_approval which tests
        approved=True. Here approved=False — user is reviewing but hasn't
        committed yet, so start-over should be allowed.
        """
        _mock_states[project_id].step = "approval"
        _mock_states[project_id].approved = False
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"

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
    async def test_start_over_clears_room_analysis_and_room_context(self, client, project_id):
        """G25: start_over clears room_analysis and room_context.

        Mirrors the real workflow behavior (design_project.py lines 427-428):
        analysis and context are cleared so intake re-fires analysis. Scan data
        (photos + LiDAR) is preserved since re-scanning is expensive.
        """
        from app.models.contracts import RoomDimensions, ScanData

        state = _mock_states[project_id]
        state.step = "iteration"
        state.room_analysis = RoomAnalysis(room_type="living room")
        state.room_context = RoomContext(
            photo_analysis=state.room_analysis,
            enrichment_sources=["photos", "lidar"],
        )
        state.scan_data = ScanData(
            storage_key="projects/test/lidar/scan.json",
            room_dimensions=RoomDimensions(width_m=5.0, length_m=6.0, height_m=2.7),
        )

        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        # Analysis and context cleared (workflow re-fires analysis on restart)
        assert body["room_analysis"] is None
        assert body["room_context"] is None
        # Scan data preserved (re-scanning is expensive)
        assert body["scan_data"] is not None
        assert body["scan_data"]["room_dimensions"]["width_m"] == 5.0

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
    async def test_empty_feedback_string_returns_422(self, client, project_id):
        """Empty string feedback hits Pydantic min_length=1 before endpoint code.

        Distinct from test_missing_feedback_returns_422 (missing field entirely)
        and test_short_text_feedback_returns_422 (1-9 chars, hits endpoint check).
        """
        _mock_states[project_id].step = "iteration"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": ""},
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
    async def test_annotate_region_id_out_of_range_returns_422(self, client, project_id):
        """Pydantic ge=1 constraint on region_id rejects 0."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 0,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Make this area brighter",
                    }
                ]
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    @pytest.mark.asyncio
    async def test_short_text_feedback_returns_422(self, client, project_id):
        """REGEN-2: Text feedback < 10 chars returns 422.

        Covers product spec REGEN-2: "darker" (7 chars) should be rejected.
        """
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "darker"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"
        assert "10" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_exact_ten_char_feedback_accepted(self, client, project_id):
        """Text feedback of exactly 10 chars is accepted."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "1234567890"},  # exactly 10
        )
        assert resp.status_code == 200

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
        """REGEN-5: Both annotate and feedback are blocked after 5-iteration cap."""
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

        # 6th annotation blocked (step is now "approval", not "iteration")
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

        # Feedback also blocked after cap (REGEN-5: both buttons disabled)
        resp = await client.post(
            f"/api/v1/projects/{project_id}/iterate/feedback",
            json={"feedback": "This feedback should also be blocked after cap"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_mixed_annotations_and_feedback_share_pool(self, client, project_id):
        """REGEN-4: Annotation and text feedback iterations share the 5-count pool."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/base.png"

        # 3 annotation iterations
        for i in range(3):
            await client.post(
                f"/api/v1/projects/{project_id}/iterate/annotate",
                json={
                    "annotations": [
                        {
                            "region_id": 1,
                            "center_x": 0.5,
                            "center_y": 0.5,
                            "radius": 0.1,
                            "instruction": f"Annotation iteration {i + 1}",
                        }
                    ]
                },
            )

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["iteration_count"] == 3
        assert body["step"] == "iteration"

        # 2 text feedback iterations (should reach cap)
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{project_id}/iterate/feedback",
                json={"feedback": f"Feedback iteration {i + 4} adjustments"},
            )

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["iteration_count"] == 5
        assert body["step"] == "approval"
        # Revision history should contain mixed types
        types = [r["type"] for r in body["revision_history"]]
        assert types == ["annotation", "annotation", "annotation", "feedback", "feedback"]

    @pytest.mark.asyncio
    async def test_start_over_during_generation_cleans_up(self, client, project_id):
        """Start over during pending generation clears the generation queue."""
        _mock_states[project_id].step = "intake"
        _projects_mod.MOCK_GENERATION_DELAY = 9999.0
        await client.post(
            f"/api/v1/projects/{project_id}/intake/confirm",
            json={"brief": {"room_type": "bedroom"}},
        )
        # Now in generation step with pending generation
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "generation"

        # Start over should work and clean up
        resp = await client.post(f"/api/v1/projects/{project_id}/start-over")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "intake"
        assert body["generated_options"] == []
        assert project_id not in _mock_pending_generation


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

    @pytest.mark.asyncio
    async def test_double_approve_returns_409(self, client, project_id):
        """Approving an already-completed project returns 409."""
        _mock_states[project_id].step = "iteration"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 200
        # Second approve should fail (step is now "completed")
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_approve_blocked_by_active_error(self, client, project_id):
        """Approve is rejected when workflow has an active error (parity with workflow signal).

        In the Temporal workflow, approve_design is silently ignored when
        self.error is not None.  The mock API mirrors this by returning 409
        so T1 iOS gets explicit feedback.
        """
        from app.models.contracts import WorkflowError

        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].error = WorkflowError(
            message="Revision failed — please retry", retryable=True
        )
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "active_error"
        # State unchanged — not approved
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["approved"] is False
        assert body["step"] == "iteration"

    @pytest.mark.asyncio
    async def test_approve_succeeds_after_clearing_error(self, client, project_id):
        """Approve works once the error is cleared via retry.

        Verifies the full recovery path: error → retry → approve.
        """
        from app.models.contracts import WorkflowError

        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].error = WorkflowError(
            message="Revision failed — please retry", retryable=True
        )
        # Retry clears the error
        await client.post(f"/api/v1/projects/{project_id}/retry")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["error"] is None

        # Now approve succeeds
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["approved"] is True
        assert body["step"] == "completed"

    @pytest.mark.asyncio
    async def test_approve_from_generation_returns_409(self, client, project_id):
        """IMP-30: Approve during generation step returns 409.

        iOS could race and send approve while generation is in progress.
        The API must reject this clearly.
        """
        _mock_states[project_id].step = "generation"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_approve_from_selection_returns_409(self, client, project_id):
        """IMP-30: Approve during selection step returns 409.

        User must select a design option before approving.
        """
        _mock_states[project_id].step = "selection"
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"

    @pytest.mark.asyncio
    async def test_approve_blocked_by_error_at_approval_step(self, client, project_id):
        """IMP-30: Approve blocked when error is set at approval step.

        Realistic scenario: 5th iteration fails, user is forced to approval
        step with an active error. Approve must still be blocked.
        """
        from app.models.contracts import WorkflowError

        _mock_states[project_id].step = "approval"
        _mock_states[project_id].error = WorkflowError(
            message="Revision failed — please retry", retryable=True
        )
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "active_error"
        # State unchanged — not approved
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["approved"] is False
        assert body["step"] == "approval"


class TestMockShoppingStep:
    """IMP-14: Verify shopping step is visible between approval and completion.

    Mirrors GAP-5 pattern: approve transitions to step='shopping' first,
    then polling auto-completes to step='completed' after delay.
    This lets iOS test the 'Generating shopping list...' spinner UI.
    """

    @pytest.mark.asyncio
    async def test_approve_shows_shopping_step(self, client, project_id):
        """After approve with delay, step is 'shopping' and shopping_list is null."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/img.png"
        _projects_mod.MOCK_SHOPPING_DELAY = 9999.0
        resp = await client.post(f"/api/v1/projects/{project_id}/approve")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "shopping"
        assert body["shopping_list"] is None
        assert body["approved"] is True

    @pytest.mark.asyncio
    async def test_shopping_completes_after_delay(self, client, project_id):
        """After delay elapsed, polling transitions to 'completed' with shopping_list."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/img.png"
        _projects_mod.MOCK_SHOPPING_DELAY = 0.0
        await client.post(f"/api/v1/projects/{project_id}/approve")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "completed"
        assert body["shopping_list"] is not None
        assert len(body["shopping_list"]["items"]) == 2
        assert body["shopping_list"]["total_estimated_cost_cents"] == 33998

    @pytest.mark.asyncio
    async def test_shopping_list_has_unmatched_items(self, client, project_id):
        """Shopping list includes unmatched items with Google Shopping fallback URLs."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/img.png"
        _projects_mod.MOCK_SHOPPING_DELAY = 0.0
        await client.post(f"/api/v1/projects/{project_id}/approve")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        unmatched = body["shopping_list"]["unmatched"]
        assert len(unmatched) == 1
        assert unmatched[0]["category"] == "Rug"
        assert "google.com" in unmatched[0]["google_shopping_url"]

    @pytest.mark.asyncio
    async def test_delete_during_shopping_cleans_up(self, client, project_id):
        """Deleting a project during shopping step cleans up pending state."""
        _mock_states[project_id].step = "iteration"
        _mock_states[project_id].current_image = "https://r2.example.com/img.png"
        _projects_mod.MOCK_SHOPPING_DELAY = 9999.0
        await client.post(f"/api/v1/projects/{project_id}/approve")
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "shopping"
        # Delete project while shopping is pending
        resp = await client.delete(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 204
        # Project is gone
        resp = await client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 404
        # Pending shopping state cleaned up
        assert project_id not in _mock_pending_shopping


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

    @pytest.mark.asyncio
    async def test_retry_noop_when_no_error(self, client, project_id):
        """Retry is a no-op when no error exists — returns 200, state unchanged.

        Matches workflow behavior: retry_failed_step sets error=None unconditionally,
        so calling it when error is already None is a harmless no-op. iOS may call this
        optimistically without checking for an active error first.
        """
        from app.models.contracts import ActionResponse

        assert _mock_states[project_id].error is None
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
    async def test_photo_delete_returns_404(self, client):
        """Photo delete on nonexistent project returns 404."""
        resp = await client.delete(f"/api/v1/projects/{self._FAKE_ID}/photos/any-photo")
        assert resp.status_code == 404

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
        await client.post(f"/api/v1/projects/{pid1}/photos/confirm")

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

        # 2. Upload 2 photos
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )

        # 3. Confirm photos -> scan, then skip scan
        assert _mock_states[pid].step == "photos"
        await client.post(f"/api/v1/projects/{pid}/photos/confirm")
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
        assert ws.step == "photos"

        # Confirm photos → scan (validate ActionResponse)
        resp = await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        ActionResponse.model_validate(resp.json())
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

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_premium_happy_path(self, _mock_val, client):
        """Full-featured flow: LiDAR scan + inspiration photos with notes + mixed iterations.

        Exercises the "premium experience" path that the basic happy_path skips:
        - Inspiration photo upload with notes (IMP-4)
        - Upload during scan step (IMP-5)
        - LiDAR scan data (not skip)
        - Mixed annotation + text feedback iterations (REGEN-4)
        - Data preservation across all step transitions
        """
        # 1. Create project
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-premium"})
        pid = resp.json()["project_id"]

        # 2. Upload 2 room photos + confirm
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        assert _mock_states[pid].step == "scan"

        # 3. Upload inspiration photo with note DURING scan step (IMP-5)
        resp = await client.post(
            f"/api/v1/projects/{pid}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration", "note": "Love the warm lighting"},
        )
        assert resp.status_code == 200
        assert _mock_states[pid].step == "scan"  # still in scan

        # 4. Upload LiDAR scan data (SCAN-1)
        scan_body = {
            "room": {"width": 4.5, "length": 6.0, "height": 2.7},
            "walls": [{"id": "wall_0", "width": 4.5, "height": 2.7}],
            "openings": [],
        }
        resp = await client.post(f"/api/v1/projects/{pid}/scan", json=scan_body)
        assert resp.status_code == 200
        assert _mock_states[pid].step == "intake"

        # 5. Intake conversation
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "living room"})
        await client.post(
            f"/api/v1/projects/{pid}/intake/message", json={"message": "warm and cozy"}
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message", json={"message": "Keep the bookshelf"}
        )

        # 6. Confirm intake with brief
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={
                "brief": {
                    "room_type": "living room",
                    "pain_points": ["too dark"],
                    "keep_items": ["bookshelf"],
                    "style_profile": {"lighting": "warm", "mood": "cozy"},
                }
            },
        )

        # 7. Select option 1 (second option)
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 1})

        # 8. Mixed iterations: 1 annotation + 1 text feedback
        await client.post(
            f"/api/v1/projects/{pid}/iterate/annotate",
            json={
                "annotations": [
                    {
                        "region_id": 1,
                        "center_x": 0.5,
                        "center_y": 0.5,
                        "radius": 0.1,
                        "instruction": "Replace this lamp with a warmer pendant light",
                    }
                ]
            },
        )
        await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "Make the overall color palette warmer with more earth tones"},
        )

        # 9. Approve design
        await client.post(f"/api/v1/projects/{pid}/approve")

        # 10. Verify comprehensive final state
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "completed"
        assert body["approved"] is True

        # Photos preserved: 2 room + 1 inspiration
        assert len(body["photos"]) == 3
        inspo = [p for p in body["photos"] if p["photo_type"] == "inspiration"]
        assert len(inspo) == 1
        assert inspo[0]["note"] == "Love the warm lighting"

        # Scan data preserved
        assert body["scan_data"] is not None
        assert body["scan_data"]["room_dimensions"]["width_m"] == 4.5

        # Design brief preserved
        assert body["design_brief"] is not None
        assert body["design_brief"]["room_type"] == "living room"
        assert body["design_brief"]["keep_items"] == ["bookshelf"]

        # Selected option 1 (not 0)
        assert body["selected_option"] == 1

        # Mixed iterations preserved
        assert body["iteration_count"] == 2
        assert len(body["revision_history"]) == 2
        assert body["revision_history"][0]["type"] == "annotation"
        assert body["revision_history"][1]["type"] == "feedback"

        # Shopping list generated
        assert body["shopping_list"] is not None
        assert len(body["shopping_list"]["items"]) >= 1

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_error_recovery_flow(self, _mock_val, client):
        """Full flow: create → progress → error → retry → complete.

        Verifies that an error during generation doesn't corrupt the project
        state and that retrying allows the flow to complete successfully.
        Exercises the mock state machine's error recovery path.
        """
        from app.models.contracts import WorkflowError

        # 1. Create project and upload photos
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-err"})
        pid = resp.json()["project_id"]
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{pid}/photos/confirm")

        # 2. Skip scan, confirm intake → generation
        await client.post(f"/api/v1/projects/{pid}/scan/skip")
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "bedroom"}},
        )
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "selection"  # delay=0 → instant completion

        # 3. Select option → iteration
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # 4. Inject an error (simulate activity failure)
        _mock_states[pid].error = WorkflowError(
            message="Generation failed: GPU timeout",
            step="iteration",
            retryable=True,
        )

        # 5. Verify error blocks approval
        resp = await client.post(f"/api/v1/projects/{pid}/approve")
        assert resp.status_code == 409
        assert resp.json()["error"] == "active_error"

        # 6. Verify error visible in project state
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["error"] is not None
        assert body["error"]["retryable"] is True

        # 7. Retry clears the error
        resp = await client.post(f"/api/v1/projects/{pid}/retry")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["error"] is None
        assert body["step"] == "iteration"  # step preserved after retry

        # 8. Approve succeeds after error cleared
        resp = await client.post(f"/api/v1/projects/{pid}/approve")
        assert resp.status_code == 200
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "completed"
        assert body["approved"] is True
        assert body["shopping_list"] is not None

        # 9. All prior data preserved through error recovery
        assert len(body["photos"]) == 2
        assert body["selected_option"] == 0

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_start_over_resumption_flow(self, _mock_val, client):
        """Full flow: create → progress to iteration → start_over → re-progress → complete.

        Verifies that start_over correctly resets design state while preserving
        photos, and that the user can complete a second pass through the entire
        flow without any leftover state from the first attempt.
        """
        # 1. Create project and upload photos
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-restart"})
        pid = resp.json()["project_id"]
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{pid}/photos/confirm")

        # 2. First pass: scan → intake → confirm → select → iterate
        await client.post(f"/api/v1/projects/{pid}/scan/skip")
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 1})
        await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "Make the lighting warmer and add more plants"},
        )

        # Verify first pass state
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "iteration"
        assert body["iteration_count"] == 1
        assert body["selected_option"] == 1

        # 3. Start over — resets design state, preserves photos
        resp = await client.post(f"/api/v1/projects/{pid}/start-over")
        assert resp.status_code == 200

        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "intake"
        assert body["iteration_count"] == 0
        assert body["selected_option"] is None
        assert body["generated_options"] == []
        assert body["revision_history"] == []
        assert body["design_brief"] is None
        assert body["shopping_list"] is None
        # Photos preserved
        assert len(body["photos"]) == 2

        # 4. Second pass: intake → confirm → select → approve → completed
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "bedroom", "pain_points": ["too small"]}},
        )
        # Select different option this time
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})
        await client.post(f"/api/v1/projects/{pid}/approve")

        # 5. Verify second pass completed cleanly
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "completed"
        assert body["approved"] is True
        assert body["selected_option"] == 0  # different from first pass
        assert body["design_brief"]["room_type"] == "bedroom"
        assert body["iteration_count"] == 0  # no iterations in second pass
        assert body["revision_history"] == []
        assert body["shopping_list"] is not None
        assert len(body["photos"]) == 2  # still preserved

    @pytest.mark.asyncio
    @patch("app.api.routes.projects.validate_photo", return_value=_VALID)
    async def test_iteration_cap_then_complete_flow(self, _mock_val, client):
        """Full flow: create → progress → 5 iterations → auto-approval → approve → complete.

        Verifies the entire journey when a user hits the iteration cap: the step
        auto-transitions to 'approval', then the user can approve and receive
        a shopping list. Mixed iteration types (annotation + text feedback) share
        the same 5-count pool.
        """
        # 1. Create project and progress to iteration
        resp = await client.post("/api/v1/projects", json={"device_fingerprint": "test-cap"})
        pid = resp.json()["project_id"]
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{pid}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{pid}/photos/confirm")
        await client.post(f"/api/v1/projects/{pid}/scan/skip")
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})
        await client.post(
            f"/api/v1/projects/{pid}/intake/confirm",
            json={"brief": {"room_type": "living room"}},
        )
        await client.post(f"/api/v1/projects/{pid}/select", json={"index": 0})

        # 2. Do 5 mixed iterations: 3 text feedback + 2 annotation
        for i in range(3):
            resp = await client.post(
                f"/api/v1/projects/{pid}/iterate/feedback",
                json={"feedback": f"Text feedback round {i + 1} with enough detail"},
            )
            assert resp.status_code == 200

        for i in range(2):
            resp = await client.post(
                f"/api/v1/projects/{pid}/iterate/annotate",
                json={
                    "annotations": [
                        {
                            "region_id": 1,
                            "center_x": 0.3 + i * 0.2,
                            "center_y": 0.5,
                            "radius": 0.1,
                            "instruction": f"Annotation edit round {i + 4} with detail",
                        }
                    ]
                },
            )
            assert resp.status_code == 200

        # 3. Verify auto-transition to approval
        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "approval"
        assert body["iteration_count"] == 5
        assert len(body["revision_history"]) == 5
        # Verify mixed types
        types = [r["type"] for r in body["revision_history"]]
        assert types == ["feedback", "feedback", "feedback", "annotation", "annotation"]

        # 4. Further iterations blocked
        resp = await client.post(
            f"/api/v1/projects/{pid}/iterate/feedback",
            json={"feedback": "This should be blocked by the iteration cap"},
        )
        assert resp.status_code == 409

        # 5. Approve from forced-approval step
        resp = await client.post(f"/api/v1/projects/{pid}/approve")
        assert resp.status_code == 200

        body = (await client.get(f"/api/v1/projects/{pid}")).json()
        assert body["step"] == "completed"
        assert body["shopping_list"] is not None
        assert body["approved"] is True
        assert len(body["photos"]) == 2


class TestRealIntakeWiring:
    """INT-2: Verify real intake agent wiring via _real_intake_message.

    These tests patch use_mock_activities=False and mock _run_intake_core
    to verify IntakeChatInput construction, history tracking, error handling,
    and cleanup on start_over/delete.
    """

    @pytest.fixture
    def mock_agent(self):
        """Patch use_mock_activities=False and mock _run_intake_core."""
        mock_core = AsyncMock(
            return_value=IntakeChatOutput(
                agent_message="Tell me about your room.",
                options=[
                    QuickReplyOption(number=1, label="Living Room", value="living room"),
                ],
                progress="Question 1 of 3",
            )
        )
        with (
            patch.object(_projects_mod.settings, "use_mock_activities", False),
            patch("app.activities.intake._run_intake_core", mock_core),
            patch(
                "app.utils.r2.generate_presigned_url",
                side_effect=lambda key: f"https://r2.example.com/{key}",
            ),
        ):
            yield mock_core

    @pytest.fixture
    def intake_project(self, project_id):
        """Project at intake step with 2 room photos and 1 inspiration photo."""
        state = _mock_states[project_id]
        state.step = "intake"
        state.photos = [
            PhotoData(
                photo_id="room-1",
                storage_key="photos/room-1.jpg",
                photo_type="room",
            ),
            PhotoData(
                photo_id="room-2",
                storage_key="photos/room-2.jpg",
                photo_type="room",
            ),
            PhotoData(
                photo_id="inspo-1",
                storage_key="photos/inspo-1.jpg",
                photo_type="inspiration",
                note="love the blue walls",
            ),
        ]
        return project_id

    @pytest.mark.asyncio
    async def test_real_intake_constructs_correct_input(self, client, intake_project, mock_agent):
        """IntakeChatInput includes correct mode, project_context, and message."""
        pid = intake_project
        # Start intake to create session
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        assert resp.status_code == 200
        assert resp.json()["agent_message"] == "Tell me about your room."

        # Verify the input passed to _run_intake_core
        call_args = mock_agent.call_args[0][0]
        assert call_args.mode == "full"
        assert call_args.user_message == "living room"
        assert call_args.conversation_history == []  # first message, no history yet
        assert call_args.project_context["room_photos"] == [
            "https://r2.example.com/photos/room-1.jpg",
            "https://r2.example.com/photos/room-2.jpg",
        ]
        assert call_args.project_context["inspiration_photos"] == [
            "https://r2.example.com/photos/inspo-1.jpg",
        ]
        assert len(call_args.project_context["inspiration_notes"]) == 1
        # photo_index is within inspiration photos (not all photos)
        assert call_args.project_context["inspiration_notes"][0]["photo_index"] == 0
        assert call_args.project_context["inspiration_notes"][0]["note"] == "love the blue walls"

    @pytest.mark.asyncio
    async def test_real_intake_accumulates_history(self, client, intake_project, mock_agent):
        """Conversation history grows across multiple send_intake_message calls."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        # First message
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "living room"},
        )
        # History should now have user + assistant messages
        session = _intake_sessions[pid]
        assert len(session.history) == 2
        assert session.history[0] == ChatMessage(role="user", content="living room")
        assert session.history[1] == ChatMessage(
            role="assistant", content="Tell me about your room."
        )

        # Second message — history is passed to agent
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "modern style"},
        )
        call_args = mock_agent.call_args[0][0]
        assert len(call_args.conversation_history) == 2  # from first turn
        assert call_args.conversation_history[0].content == "living room"
        assert call_args.conversation_history[1].content == "Tell me about your room."

        # After second call, session has 4 messages
        assert len(session.history) == 4

    @pytest.mark.asyncio
    async def test_real_intake_stores_partial_brief(self, client, intake_project, mock_agent):
        """partial_brief from agent response is stored and passed on next turn."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        # Agent returns a partial brief
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Here's a summary.",
            is_summary=True,
            partial_brief=DesignBrief(room_type="living room"),
            progress="Summary",
        )

        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "that's all"},
        )

        # Verify session stored the partial brief
        session = _intake_sessions[pid]
        assert session.last_partial_brief is not None
        assert session.last_partial_brief.room_type == "living room"

        # On next call, previous_brief should be in project_context
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Anything else?",
            progress="Follow-up",
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "add plants"},
        )
        call_args = mock_agent.call_args[0][0]
        assert "previous_brief" in call_args.project_context
        assert call_args.project_context["previous_brief"]["room_type"] == "living room"

    @pytest.mark.asyncio
    async def test_real_intake_agent_error_returns_500(self, client, intake_project, mock_agent):
        """Exception from _run_intake_core returns 500 ErrorResponse."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        mock_agent.side_effect = RuntimeError("Claude API timeout")

        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"] == "intake_error"
        # Error message is sanitized — no raw exception text leaked
        assert "design assistant" in body["message"]
        assert body["retryable"] is True

    @pytest.mark.asyncio
    async def test_real_intake_no_session_returns_409(self, client, intake_project, mock_agent):
        """Calling send_intake_message without start_intake returns 409."""
        pid = intake_project
        # Don't call start_intake — session doesn't exist

        resp = await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "hello"},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "wrong_step"
        assert "start_intake" in resp.json()["message"]

    @pytest.mark.asyncio
    async def test_start_over_clears_intake_session(self, client, intake_project, mock_agent):
        """start_over removes the intake session."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        assert pid in _intake_sessions

        await client.post(f"/api/v1/projects/{pid}/start-over")
        assert pid not in _intake_sessions

    @pytest.mark.asyncio
    async def test_delete_project_clears_intake_session(self, client, intake_project, mock_agent):
        """delete_project removes the intake session."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})
        assert pid in _intake_sessions

        await client.delete(f"/api/v1/projects/{pid}")
        assert pid not in _intake_sessions

    @pytest.mark.asyncio
    async def test_real_intake_error_doesnt_corrupt_history(
        self, client, intake_project, mock_agent
    ):
        """When agent errors, the failed message is not added to history."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        mock_agent.side_effect = RuntimeError("transient error")
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "bad message"},
        )

        session = _intake_sessions[pid]
        assert len(session.history) == 0  # nothing appended on error

    @pytest.mark.asyncio
    async def test_real_intake_injects_room_analysis(self, client, intake_project, mock_agent):
        """room_analysis from WorkflowState is injected into project_context."""
        pid = intake_project
        state = _mock_states[pid]
        state.room_analysis = RoomAnalysis(
            room_type="living room",
            room_type_confidence=0.85,
            hypothesis="Bright living room with mid-century furniture",
        )
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "hello"},
        )

        call_args = mock_agent.call_args[0][0]
        ctx = call_args.project_context
        assert "room_analysis" in ctx
        assert ctx["room_analysis"]["room_type"] == "living room"
        assert ctx["room_analysis"]["room_type_confidence"] == 0.85
        assert ctx["room_analysis"]["hypothesis"] == "Bright living room with mid-century furniture"

    @pytest.mark.asyncio
    async def test_real_intake_injects_room_context(self, client, intake_project, mock_agent):
        """room_context from WorkflowState is injected into project_context."""
        pid = intake_project
        state = _mock_states[pid]
        analysis = RoomAnalysis(room_type="bedroom", photo_count=2)
        state.room_analysis = analysis
        state.room_context = RoomContext(
            photo_analysis=analysis,
            enrichment_sources=["photos", "lidar"],
        )
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "hello"},
        )

        call_args = mock_agent.call_args[0][0]
        ctx = call_args.project_context
        assert "room_context" in ctx
        assert ctx["room_context"]["enrichment_sources"] == ["photos", "lidar"]
        assert ctx["room_context"]["photo_analysis"]["room_type"] == "bedroom"

    @pytest.mark.asyncio
    async def test_real_intake_omits_room_analysis_when_absent(
        self, client, intake_project, mock_agent
    ):
        """room_analysis/room_context absent from project_context when None in state."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "quick"})

        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "hello"},
        )

        call_args = mock_agent.call_args[0][0]
        ctx = call_args.project_context
        assert "room_analysis" not in ctx
        assert "room_context" not in ctx

    @pytest.mark.asyncio
    async def test_session_tracks_loaded_skill_ids(self, client, intake_project, mock_agent):
        """After requesting 'cozy', session.loaded_skill_ids has it."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        mock_agent.return_value = IntakeChatOutput(
            agent_message="I see you like cozy styles!",
            progress="Turn 1",
            requested_skills=["cozy"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "I want something cozy"},
        )

        session = _intake_sessions[pid]
        assert session.loaded_skill_ids == ["cozy"]

    @pytest.mark.asyncio
    async def test_loaded_skills_passed_to_prompt(self, client, intake_project, mock_agent):
        """project_context includes loaded_skill_ids on subsequent turns."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # First turn: agent requests "cozy"
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy, got it!",
            progress="Turn 1",
            requested_skills=["cozy"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "cozy please"},
        )

        # Second turn: verify loaded_skill_ids in project_context
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Tell me more.",
            progress="Turn 2",
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "warm colors"},
        )

        call_args = mock_agent.call_args[0][0]
        assert call_args.project_context["loaded_skill_ids"] == ["cozy"]

    @pytest.mark.asyncio
    async def test_loaded_skills_persist_across_turns(self, client, intake_project, mock_agent):
        """Skills loaded in turn 1 persist through turn 3."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Turn 1: request cozy
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy!", progress="Turn 1", requested_skills=["cozy"]
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "cozy"})

        # Turn 2: no new skills requested
        mock_agent.return_value = IntakeChatOutput(agent_message="More details?", progress="Turn 2")
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "yes"})

        # Turn 3: verify cozy still in context
        mock_agent.return_value = IntakeChatOutput(agent_message="Great!", progress="Turn 3")
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "plants"})

        call_args = mock_agent.call_args[0][0]
        assert call_args.project_context["loaded_skill_ids"] == ["cozy"]

    @pytest.mark.asyncio
    async def test_loaded_skills_cap_at_two(self, client, intake_project, mock_agent):
        """Requesting a 3rd skill does not exceed cap of 2."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Turn 1: load cozy + modern
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Nice blend!",
            progress="Turn 1",
            requested_skills=["cozy", "modern"],
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "cozy modern"})
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy", "modern"]

        # Turn 2: try to add a 3rd
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Scandinavian too?",
            progress="Turn 2",
            requested_skills=["scandinavian"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "also scandinavian"},
        )

        # Cap at 2 style skills — original two preserved, scandinavian dropped
        assert len(_intake_sessions[pid].loaded_skill_ids) == 2
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy", "modern"]

    @pytest.mark.asyncio
    async def test_more_space_stacks_beyond_style_cap(self, client, intake_project, mock_agent):
        """more_space is orthogonal — stacks with 2 style skills (total 3)."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Turn 1: load cozy + modern + more_space
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy modern, needs space!",
            progress="Turn 1",
            requested_skills=["cozy", "modern", "more_space"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "cozy modern but cramped"},
        )

        # All 3 survive — more_space doesn't count toward the 2-style cap
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy", "modern", "more_space"]

    @pytest.mark.asyncio
    async def test_more_space_persists_when_styles_capped(self, client, intake_project, mock_agent):
        """Adding a 3rd style skill doesn't evict more_space."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Turn 1: load cozy + more_space
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy and needs space!",
            progress="Turn 1",
            requested_skills=["cozy", "more_space"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "cozy cramped"},
        )
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy", "more_space"]

        # Turn 2: add modern — now cozy + modern + more_space
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Modern too!",
            progress="Turn 2",
            requested_skills=["modern"],
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "also modern"})
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy", "modern", "more_space"]

        # Turn 3: try to add scandinavian — style cap prevents, but more_space stays
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Scandi too?",
            progress="Turn 3",
            requested_skills=["scandinavian"],
        )
        await client.post(
            f"/api/v1/projects/{pid}/intake/message",
            json={"message": "also scandinavian"},
        )
        # cozy + modern (style cap) + more_space (orthogonal) = 3
        assert len(_intake_sessions[pid].loaded_skill_ids) == 3
        assert "cozy" in _intake_sessions[pid].loaded_skill_ids
        assert "modern" in _intake_sessions[pid].loaded_skill_ids
        assert "more_space" in _intake_sessions[pid].loaded_skill_ids

    @pytest.mark.asyncio
    async def test_start_over_clears_loaded_skills(self, client, intake_project, mock_agent):
        """start_over creates a fresh session without loaded skills."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy!", progress="Turn 1", requested_skills=["cozy"]
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "cozy"})
        assert _intake_sessions[pid].loaded_skill_ids == ["cozy"]

        # Start over removes session entirely
        await client.post(f"/api/v1/projects/{pid}/start-over")
        assert pid not in _intake_sessions

    @pytest.mark.asyncio
    async def test_loaded_skills_deduplicated(self, client, intake_project, mock_agent):
        """Requesting the same skill twice doesn't duplicate it."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        # Turn 1: load cozy
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Cozy!", progress="Turn 1", requested_skills=["cozy"]
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "cozy"})

        # Turn 2: request cozy again
        mock_agent.return_value = IntakeChatOutput(
            agent_message="Still cozy!", progress="Turn 2", requested_skills=["cozy"]
        )
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "more cozy"})

        assert _intake_sessions[pid].loaded_skill_ids == ["cozy"]

    @pytest.mark.asyncio
    async def test_loaded_skills_absent_when_none(self, client, intake_project, mock_agent):
        """project_context omits loaded_skill_ids when empty (not sent as [])."""
        pid = intake_project
        await client.post(f"/api/v1/projects/{pid}/intake/start", json={"mode": "full"})

        mock_agent.return_value = IntakeChatOutput(agent_message="Hello!", progress="Turn 1")
        await client.post(f"/api/v1/projects/{pid}/intake/message", json={"message": "hi"})

        call_args = mock_agent.call_args[0][0]
        assert "loaded_skill_ids" not in call_args.project_context


class TestForceFailureEndpoint:
    """POST /api/v1/debug/force-failure — error injection for E2E-11.

    The endpoint has 3 branches:
    1. Non-development environment → 403
    2. Real activities (use_mock_activities=False) → 409
    3. Happy path → touch sentinel file → 200
    """

    @pytest.mark.asyncio
    async def test_force_failure_returns_200_in_dev(self, client):
        """Happy path: arms the sentinel file in development mode."""
        import tempfile
        from pathlib import Path

        sentinel = Path(tempfile.gettempdir()) / "remo-force-failure"
        sentinel.unlink(missing_ok=True)

        resp = await client.post("/api/v1/debug/force-failure")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        assert sentinel.exists()

        # Cleanup
        sentinel.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_force_failure_idempotent(self, client):
        """Calling twice doesn't fail — sentinel .touch() is idempotent."""
        import tempfile
        from pathlib import Path

        sentinel = Path(tempfile.gettempdir()) / "remo-force-failure"
        sentinel.unlink(missing_ok=True)

        resp1 = await client.post("/api/v1/debug/force-failure")
        resp2 = await client.post("/api/v1/debug/force-failure")
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        sentinel.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_force_failure_blocked_outside_development(self, client):
        """Returns 403 when environment is not 'development'."""
        with patch.object(_projects_mod.settings, "environment", "production"):
            resp = await client.post("/api/v1/debug/force-failure")
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    @pytest.mark.asyncio
    async def test_force_failure_blocked_with_real_activities(self, client):
        """Returns 409 when use_mock_activities is False (real AI mode)."""
        with patch.object(_projects_mod.settings, "use_mock_activities", False):
            resp = await client.post("/api/v1/debug/force-failure")
        assert resp.status_code == 409
        assert resp.json()["error"] == "not_applicable"


class TestEdgeCases:
    """Edge cases found by mock/Temporal parity audit."""

    @pytest.mark.asyncio
    async def test_second_scan_upload_overwrites_first(self, client, project_id):
        """Uploading scan data a second time overwrites the first — no error.

        User may rescan their room to improve accuracy. The API should
        silently accept the new data rather than blocking the re-upload.
        """
        _mock_states[project_id].step = "scan"

        # First scan
        scan1 = {"room": {"width": 4.0, "length": 5.0, "height": 2.7}}
        resp = await client.post(f"/api/v1/projects/{project_id}/scan", json=scan1)
        assert resp.status_code == 200

        # Project transitions to intake — set back to scan for re-scan
        _mock_states[project_id].step = "scan"

        # Second scan with different dimensions
        scan2 = {"room": {"width": 6.0, "length": 8.0, "height": 3.0}}
        resp = await client.post(f"/api/v1/projects/{project_id}/scan", json=scan2)
        assert resp.status_code == 200

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        # Second scan data wins
        assert body["scan_data"]["room_dimensions"]["width_m"] == 6.0
        assert body["scan_data"]["room_dimensions"]["length_m"] == 8.0

    @pytest.mark.asyncio
    async def test_intake_step4_returns_summary_again(self, client, project_id):
        """Sending a 4th+ intake message returns the same summary (mock behavior).

        After the 3-step mock conversation produces a summary, further messages
        should still get a response (summary with partial brief). This is how
        the "I want to change something" correction flow works in mock mode.
        """
        _mock_states[project_id].step = "intake"

        await client.post(f"/api/v1/projects/{project_id}/intake/start", json={"mode": "quick"})
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "living room"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "modern"},
        )
        resp3 = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "Replace the couch"},
        )
        assert resp3.json()["is_summary"] is True

        # 4th message — still works, returns summary
        resp4 = await client.post(
            f"/api/v1/projects/{project_id}/intake/message",
            json={"message": "Actually, also add more plants"},
        )
        assert resp4.status_code == 200
        assert resp4.json()["is_summary"] is True
        assert resp4.json()["partial_brief"] is not None

    @pytest.mark.asyncio
    @patch(
        "app.api.routes.projects.validate_photo",
        return_value=ValidatePhotoOutput(
            passed=True,
            failures=[],
            messages=["OK"],
        ),
    )
    async def test_delete_room_photo_during_scan_keeps_step(self, _mock_val, client, project_id):
        """Deleting a room photo during scan keeps step at scan (forward-only).

        User has 2 room photos → confirms → scan → deletes 1 room photo
        → only 1 room photo remains → step stays at "scan" because the workflow
        state machine is forward-only (matches Temporal behavior).
        """
        # Upload 2 room photos and confirm to get to scan
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{project_id}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
        assert _mock_states[project_id].step == "scan"
        photo_ids = [p.photo_id for p in _mock_states[project_id].photos]
        assert len(photo_ids) == 2

        # Delete one room photo → step stays at scan (forward-only)
        resp = await client.delete(f"/api/v1/projects/{project_id}/photos/{photo_ids[0]}")
        assert resp.status_code == 204
        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "scan"
        assert len(body["photos"]) == 1

    @pytest.mark.asyncio
    @patch(
        "app.api.routes.projects.validate_photo",
        return_value=ValidatePhotoOutput(
            passed=True,
            failures=[],
            messages=["OK"],
        ),
    )
    async def test_delete_room_photo_with_inspirations_during_scan(
        self, _mock_val, client, project_id
    ):
        """Deleting 1 of 2 room photos during scan keeps step at scan (forward-only).

        Even though only 1 room photo remains (below the 2-photo confirmation
        threshold), the workflow state machine is forward-only — it never regresses.
        This matches Temporal behavior.
        """
        # Upload 2 room photos and confirm
        for i in range(2):
            await client.post(
                f"/api/v1/projects/{project_id}/photos",
                files={"file": (f"room_{i}.jpg", io.BytesIO(b"img"), "image/jpeg")},
            )
        await client.post(f"/api/v1/projects/{project_id}/photos/confirm")
        assert _mock_states[project_id].step == "scan"

        # Add an inspiration photo during scan
        await client.post(
            f"/api/v1/projects/{project_id}/photos",
            files={"file": ("inspo.jpg", io.BytesIO(b"img"), "image/jpeg")},
            data={"photo_type": "inspiration"},
        )
        assert _mock_states[project_id].step == "scan"  # still in scan

        # Delete one room photo — step stays at scan (forward-only)
        room_photos = [p for p in _mock_states[project_id].photos if p.photo_type == "room"]
        resp = await client.delete(
            f"/api/v1/projects/{project_id}/photos/{room_photos[0].photo_id}"
        )
        assert resp.status_code == 204

        body = (await client.get(f"/api/v1/projects/{project_id}")).json()
        assert body["step"] == "scan"  # forward-only: step never regresses
        # 1 room + 1 inspiration = 2 total, but only 1 room
        assert len(body["photos"]) == 2
        room_count = sum(1 for p in body["photos"] if p["photo_type"] == "room")
        assert room_count == 1
