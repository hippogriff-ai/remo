"""Tests for all Pydantic contract models.

Validates that every model:
- Accepts valid data
- Rejects invalid data with appropriate errors
- Serializes/deserializes correctly
- Enforces Field constraints (min_length, ge/le, etc.)
"""

import pytest
from pydantic import ValidationError

from app.models.contracts import (
    ChatMessage,
    CreateProjectRequest,
    DesignBrief,
    DesignOption,
    ErrorResponse,
    GenerateDesignsOutput,
    GenerateInpaintInput,
    GenerateShoppingListOutput,
    InspirationNote,
    IntakeChatInput,
    LassoEditRequest,
    LassoRegion,
    PhotoData,
    PhotoUploadResponse,
    ProductMatch,
    RevisionRecord,
    RoomDimensions,
    SelectOptionRequest,
    StyleProfile,
    ValidatePhotoInput,
    ValidatePhotoOutput,
    WorkflowError,
    WorkflowState,
)


class TestStyleProfile:
    """StyleProfile is fully optional — all fields default to None or empty list."""

    def test_empty_valid(self):
        """All fields are optional; empty construction succeeds."""
        p = StyleProfile()
        assert p.lighting is None
        assert p.colors == []

    def test_full_valid(self):
        """All fields populated."""
        p = StyleProfile(
            lighting="warm",
            colors=["navy", "cream"],
            textures=["velvet"],
            clutter_level="minimal",
            mood="cozy",
        )
        assert p.lighting == "warm"
        assert len(p.colors) == 2


class TestInspirationNote:
    """InspirationNote requires photo_index and note."""

    def test_valid(self):
        """Minimal valid construction."""
        n = InspirationNote(photo_index=0, note="love the shelving")
        assert n.agent_clarification is None

    def test_missing_note_fails(self):
        """Note is required."""
        with pytest.raises(ValidationError):
            InspirationNote(photo_index=0)


class TestDesignBrief:
    """DesignBrief requires room_type; everything else is optional."""

    def test_minimal(self):
        """Only room_type required."""
        b = DesignBrief(room_type="living room")
        assert b.occupants is None
        assert b.pain_points == []

    def test_full(self):
        """All fields populated including nested StyleProfile."""
        b = DesignBrief(
            room_type="bedroom",
            occupants="couple",
            pain_points=["too dark"],
            keep_items=["bed frame"],
            style_profile=StyleProfile(lighting="warm"),
            constraints=["budget $5000"],
            inspiration_notes=[InspirationNote(photo_index=0, note="moody lighting")],
        )
        assert b.style_profile.lighting == "warm"
        assert len(b.inspiration_notes) == 1


class TestRoomDimensions:
    """RoomDimensions requires width/length/height."""

    def test_valid(self):
        """Minimal construction with dimensions."""
        d = RoomDimensions(width_m=4.5, length_m=6.0, height_m=2.7)
        assert d.walls == []
        assert d.openings == []

    def test_missing_height_fails(self):
        """Height is required."""
        with pytest.raises(ValidationError):
            RoomDimensions(width_m=4.5, length_m=6.0)


class TestLassoRegion:
    """LassoRegion has constraints: region_id 1-3, instruction min 10 chars."""

    def test_valid(self):
        """Valid region with all constraints met."""
        r = LassoRegion(
            region_id=1,
            path_points=[(0.1, 0.2), (0.3, 0.4), (0.5, 0.6)],
            action="Replace",
            instruction="Replace the old sofa with a modern sectional",
        )
        assert r.region_id == 1

    def test_region_id_too_high(self):
        """Region ID must be 1-3."""
        with pytest.raises(ValidationError):
            LassoRegion(
                region_id=4,
                path_points=[(0.1, 0.2)],
                action="Replace",
                instruction="Replace the sofa with a new one",
            )

    def test_region_id_too_low(self):
        """Region ID must be >= 1."""
        with pytest.raises(ValidationError):
            LassoRegion(
                region_id=0,
                path_points=[(0.1, 0.2)],
                action="Replace",
                instruction="Replace the sofa with a new one",
            )

    def test_instruction_too_short(self):
        """Instruction must be at least 10 characters."""
        with pytest.raises(ValidationError):
            LassoRegion(
                region_id=1,
                path_points=[(0.1, 0.2)],
                action="Replace",
                instruction="short",
            )


class TestProductMatch:
    """ProductMatch has numeric constraints on price_cents and confidence_score."""

    def test_valid(self):
        """Full valid product match."""
        p = ProductMatch(
            category_group="Seating",
            product_name="IKEA KIVIK Sofa",
            retailer="IKEA",
            price_cents=79900,
            product_url="https://ikea.com/kivik",
            confidence_score=0.85,
            why_matched="Similar L-shaped sectional in grey fabric",
        )
        assert p.price_cents == 79900

    def test_negative_price_fails(self):
        """Price cannot be negative."""
        with pytest.raises(ValidationError):
            ProductMatch(
                category_group="Seating",
                product_name="Chair",
                retailer="Store",
                price_cents=-100,
                product_url="https://example.com",
                confidence_score=0.5,
                why_matched="match",
            )

    def test_confidence_out_of_range(self):
        """Confidence score must be 0-1."""
        with pytest.raises(ValidationError):
            ProductMatch(
                category_group="Seating",
                product_name="Chair",
                retailer="Store",
                price_cents=100,
                product_url="https://example.com",
                confidence_score=1.5,
                why_matched="match",
            )


class TestChatMessage:
    """ChatMessage role must be 'user' or 'assistant'."""

    def test_user_message(self):
        """Valid user message."""
        m = ChatMessage(role="user", content="I want a cozy room")
        assert m.role == "user"

    def test_invalid_role(self):
        """Invalid role is rejected."""
        with pytest.raises(ValidationError):
            ChatMessage(role="system", content="hello")


class TestRevisionRecord:
    """RevisionRecord type must be 'lasso' or 'regen'."""

    def test_lasso_type(self):
        """Valid lasso revision."""
        r = RevisionRecord(
            revision_number=1,
            type="lasso",
            base_image_url="https://r2.example.com/base.png",
            revised_image_url="https://r2.example.com/rev1.png",
        )
        assert r.type == "lasso"

    def test_invalid_type(self):
        """Invalid revision type is rejected."""
        with pytest.raises(ValidationError):
            RevisionRecord(
                revision_number=1,
                type="unknown",
                base_image_url="https://r2.example.com/base.png",
                revised_image_url="https://r2.example.com/rev1.png",
            )


class TestPhotoData:
    """PhotoData photo_type must be 'room' or 'inspiration'."""

    def test_room_photo(self):
        """Valid room photo data."""
        p = PhotoData(photo_id="abc", storage_key="projects/1/photos/room_0.jpg", photo_type="room")
        assert p.note is None

    def test_invalid_type(self):
        """Invalid photo type is rejected."""
        with pytest.raises(ValidationError):
            PhotoData(photo_id="abc", storage_key="key", photo_type="selfie")


class TestGenerateDesignsOutput:
    """GenerateDesignsOutput must have exactly 2 options."""

    def test_exactly_two(self):
        """Two options is valid."""
        out = GenerateDesignsOutput(
            options=[
                DesignOption(image_url="https://r2/opt0.png", caption="Modern"),
                DesignOption(image_url="https://r2/opt1.png", caption="Classic"),
            ]
        )
        assert len(out.options) == 2

    def test_one_option_fails(self):
        """Fewer than 2 options is rejected."""
        with pytest.raises(ValidationError):
            GenerateDesignsOutput(
                options=[DesignOption(image_url="https://r2/opt0.png", caption="Solo")]
            )

    def test_three_options_fails(self):
        """More than 2 options is rejected."""
        with pytest.raises(ValidationError):
            GenerateDesignsOutput(
                options=[
                    DesignOption(image_url="https://r2/opt0.png", caption="A"),
                    DesignOption(image_url="https://r2/opt1.png", caption="B"),
                    DesignOption(image_url="https://r2/opt2.png", caption="C"),
                ]
            )


class TestGenerateInpaintInput:
    """GenerateInpaintInput regions must be 1-3."""

    def test_one_region(self):
        """Single region is valid."""
        inp = GenerateInpaintInput(
            base_image_url="https://r2/base.png",
            regions=[
                LassoRegion(
                    region_id=1,
                    path_points=[(0.1, 0.2), (0.3, 0.4)],
                    action="Replace",
                    instruction="Replace the lamp with a floor lamp",
                )
            ],
        )
        assert len(inp.regions) == 1

    def test_empty_regions_fails(self):
        """Zero regions is rejected."""
        with pytest.raises(ValidationError):
            GenerateInpaintInput(base_image_url="https://r2/base.png", regions=[])


class TestGenerateShoppingListOutput:
    """GenerateShoppingListOutput total_estimated_cost_cents must be >= 0."""

    def test_valid(self):
        """Valid output with items."""
        out = GenerateShoppingListOutput(
            items=[
                ProductMatch(
                    category_group="Seating",
                    product_name="Sofa",
                    retailer="IKEA",
                    price_cents=50000,
                    product_url="https://ikea.com",
                    confidence_score=0.9,
                    why_matched="matched",
                )
            ],
            total_estimated_cost_cents=50000,
        )
        assert out.total_estimated_cost_cents == 50000

    def test_negative_total_fails(self):
        """Negative total is rejected."""
        with pytest.raises(ValidationError):
            GenerateShoppingListOutput(items=[], total_estimated_cost_cents=-1)


class TestIntakeChatInput:
    """IntakeChatInput mode must be quick/full/open."""

    def test_quick_mode(self):
        """Valid quick mode input."""
        inp = IntakeChatInput(
            mode="quick",
            project_context={"photos": 2},
            conversation_history=[],
            user_message="Hi, I want a modern living room",
        )
        assert inp.mode == "quick"

    def test_invalid_mode(self):
        """Invalid mode is rejected."""
        with pytest.raises(ValidationError):
            IntakeChatInput(
                mode="express",
                project_context={},
                conversation_history=[],
                user_message="hello",
            )


class TestValidatePhotoInput:
    """ValidatePhotoInput requires bytes and valid photo_type."""

    def test_valid(self):
        """Valid input with bytes data."""
        inp = ValidatePhotoInput(image_data=b"\x89PNG\r\n", photo_type="room")
        assert inp.photo_type == "room"

    def test_invalid_type(self):
        """Invalid photo type is rejected."""
        with pytest.raises(ValidationError):
            ValidatePhotoInput(image_data=b"data", photo_type="panorama")


class TestWorkflowState:
    """WorkflowState is the main query response — test full round-trip."""

    def test_initial_state(self):
        """Minimal initial state (photos step)."""
        s = WorkflowState(step="photos")
        assert s.photos == []
        assert s.iteration_count == 0
        assert s.approved is False
        assert s.error is None

    def test_full_state_serialization(self):
        """Full state round-trips through JSON serialization."""
        s = WorkflowState(
            step="iteration",
            photos=[
                PhotoData(
                    photo_id="p1",
                    storage_key="projects/1/photos/room_0.jpg",
                    photo_type="room",
                )
            ],
            design_brief=DesignBrief(room_type="bedroom"),
            generated_options=[
                DesignOption(image_url="https://r2/opt0.png", caption="A"),
                DesignOption(image_url="https://r2/opt1.png", caption="B"),
            ],
            selected_option=0,
            current_image="https://r2/rev1.png",
            revision_history=[
                RevisionRecord(
                    revision_number=1,
                    type="lasso",
                    base_image_url="https://r2/opt0.png",
                    revised_image_url="https://r2/rev1.png",
                )
            ],
            iteration_count=1,
            error=WorkflowError(message="Revision failed", retryable=True),
        )
        json_str = s.model_dump_json()
        restored = WorkflowState.model_validate_json(json_str)
        assert restored.step == "iteration"
        assert restored.selected_option == 0
        assert restored.error.retryable is True
        assert restored.revision_history[0].type == "lasso"


class TestAPIModels:
    """API request/response models."""

    def test_create_project_request(self):
        """CreateProjectRequest requires device_fingerprint."""
        r = CreateProjectRequest(device_fingerprint="abc-123")
        assert r.has_lidar is False

    def test_select_option_in_range(self):
        """SelectOptionRequest index must be 0 or 1."""
        r = SelectOptionRequest(index=0)
        assert r.index == 0

    def test_select_option_out_of_range(self):
        """SelectOptionRequest index must be 0 or 1."""
        with pytest.raises(ValidationError):
            SelectOptionRequest(index=2)

    def test_lasso_edit_request_empty_fails(self):
        """LassoEditRequest must have at least 1 region."""
        with pytest.raises(ValidationError):
            LassoEditRequest(regions=[])

    def test_error_response(self):
        """ErrorResponse with all fields."""
        e = ErrorResponse(
            error="workflow_not_found",
            message="Project not found",
            retryable=False,
            detail="Workflow ID xyz does not exist",
        )
        assert e.retryable is False

    def test_photo_upload_response(self):
        """PhotoUploadResponse contains nested ValidatePhotoOutput."""
        r = PhotoUploadResponse(
            photo_id="p1",
            validation=ValidatePhotoOutput(
                passed=True,
                failures=[],
                messages=["Photo accepted"],
            ),
        )
        assert r.validation.passed is True


class TestAllModelsImportable:
    """Verify all models from contracts can be imported via wildcard."""

    def test_star_import(self):
        """All public models are importable from contracts module."""
        import app.models.contracts as c

        expected = [
            "StyleProfile",
            "InspirationNote",
            "DesignBrief",
            "RoomDimensions",
            "LassoRegion",
            "DesignOption",
            "ProductMatch",
            "UnmatchedItem",
            "ChatMessage",
            "QuickReplyOption",
            "WorkflowError",
            "RevisionRecord",
            "PhotoData",
            "ScanData",
            "GenerateDesignsInput",
            "GenerateDesignsOutput",
            "GenerateInpaintInput",
            "GenerateInpaintOutput",
            "GenerateRegenInput",
            "GenerateRegenOutput",
            "GenerateShoppingListInput",
            "GenerateShoppingListOutput",
            "IntakeChatInput",
            "IntakeChatOutput",
            "ValidatePhotoInput",
            "ValidatePhotoOutput",
            "WorkflowState",
            "CreateProjectRequest",
            "CreateProjectResponse",
            "PhotoUploadResponse",
            "IntakeStartRequest",
            "IntakeMessageRequest",
            "IntakeConfirmRequest",
            "SelectOptionRequest",
            "LassoEditRequest",
            "RegenerateRequest",
            "ErrorResponse",
        ]
        for name in expected:
            assert hasattr(c, name), f"Missing model: {name}"
