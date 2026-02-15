"""Tests for all Pydantic contract models.

Validates that every model:
- Accepts valid data
- Rejects invalid data with appropriate errors
- Serializes/deserializes correctly
- Enforces Field constraints (min_length, ge/le, etc.)
"""

import pytest
from pydantic import ValidationError

from app.activities.mock_stubs import (
    analyze_room_photos as mock_analyze_room_photos,
)
from app.activities.mock_stubs import (
    edit_design as mock_edit_design,
)
from app.activities.mock_stubs import (
    generate_designs as mock_generate_designs,
)
from app.activities.mock_stubs import (
    generate_shopping_list as mock_generate_shopping_list,
)
from app.activities.mock_stubs import (
    load_style_skill as mock_load_style_skill,
)
from app.models.contracts import (
    AnalyzeRoomPhotosInput,
    AnalyzeRoomPhotosOutput,
    AnnotationEditRequest,
    AnnotationRegion,
    BehavioralSignal,
    ChatMessage,
    CostBreakdown,
    CreateProjectRequest,
    DesignBrief,
    DesignOption,
    EditDesignInput,
    EditDesignOutput,
    ErrorResponse,
    FeasibilityNote,
    FurnitureObservation,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    GenerateShoppingListInput,
    GenerateShoppingListOutput,
    InspirationNote,
    IntakeChatInput,
    IntakeChatOutput,
    LightingAssessment,
    LoadSkillInput,
    LoadSkillOutput,
    PhotoData,
    PhotoUploadResponse,
    ProductMatch,
    ProfessionalFee,
    QuickReplyOption,
    RenovationIntent,
    RevisionRecord,
    RoomAnalysis,
    RoomContext,
    RoomDimensions,
    ScanData,
    SelectOptionRequest,
    SkillManifest,
    SkillSummary,
    StyleProfile,
    StyleSkillPack,
    TextFeedbackRequest,
    UnmatchedItem,
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
        assert b.lifestyle is None
        assert b.pain_points == []

    def test_full(self):
        """All fields populated including nested StyleProfile."""
        b = DesignBrief(
            room_type="bedroom",
            occupants="couple",
            lifestyle="Morning yoga, weekend hosting",
            pain_points=["too dark"],
            keep_items=["bed frame"],
            style_profile=StyleProfile(lighting="warm"),
            constraints=["budget $5000"],
            inspiration_notes=[InspirationNote(photo_index=0, note="moody lighting")],
        )
        assert b.style_profile.lighting == "warm"
        assert b.lifestyle == "Morning yoga, weekend hosting"
        assert len(b.inspiration_notes) == 1

    def test_lifestyle_none_is_backwards_compatible(self):
        """DesignBrief(lifestyle=None) is valid — INT-6 backwards compatibility.

        Covers INT-6 TDD criterion: DesignBrief(lifestyle=None) is valid.
        """
        b = DesignBrief(room_type="office")
        assert b.lifestyle is None
        d = b.model_dump()
        b2 = DesignBrief.model_validate(d)
        assert b2.lifestyle is None

    def test_lifestyle_serializes_correctly(self):
        """DesignBrief with lifestyle serializes and deserializes.

        Covers INT-6 TDD criterion: serializes/deserializes correctly.
        """
        b = DesignBrief(
            room_type="living room",
            lifestyle="Morning yoga, weekend hosting",
        )
        d = b.model_dump()
        assert d["lifestyle"] == "Morning yoga, weekend hosting"
        b2 = DesignBrief.model_validate(d)
        assert b2.lifestyle == "Morning yoga, weekend hosting"


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


class TestAnnotationRegion:
    """AnnotationRegion: region_id 1-3, center/radius 0-1, instruction min 10."""

    def test_valid(self):
        """Valid region with all constraints met."""
        r = AnnotationRegion(
            region_id=1,
            center_x=0.5,
            center_y=0.4,
            radius=0.2,
            instruction="Replace the old sofa with a modern sectional",
        )
        assert r.region_id == 1
        assert r.center_x == 0.5
        assert r.center_y == 0.4
        assert r.radius == 0.2

    def test_region_id_too_high(self):
        """Region ID must be 1-3."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=4,
                center_x=0.5,
                center_y=0.5,
                radius=0.2,
                instruction="Replace the sofa with a new one",
            )

    def test_region_id_too_low(self):
        """Region ID must be >= 1."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=0,
                center_x=0.5,
                center_y=0.5,
                radius=0.2,
                instruction="Replace the sofa with a new one",
            )

    def test_instruction_too_short(self):
        """Instruction must be at least 10 characters."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.2,
                instruction="short",
            )

    def test_center_x_out_of_range(self):
        """center_x must be between 0 and 1."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=1,
                center_x=1.5,
                center_y=0.5,
                radius=0.2,
                instruction="Replace the sofa with a new one",
            )

    def test_center_y_out_of_range(self):
        """center_y must be between 0 and 1."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=-0.1,
                radius=0.2,
                instruction="Replace the sofa with a new one",
            )

    def test_radius_out_of_range(self):
        """radius must be between 0 and 1."""
        with pytest.raises(ValidationError):
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=1.5,
                instruction="Replace the sofa with a new one",
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
    """RevisionRecord type is a plain str (annotation, feedback, etc.)."""

    def test_annotation_type(self):
        """Valid annotation revision."""
        r = RevisionRecord(
            revision_number=1,
            type="annotation",
            base_image_url="https://r2.example.com/base.png",
            revised_image_url="https://r2.example.com/rev1.png",
        )
        assert r.type == "annotation"

    def test_instructions_default_empty(self):
        """Instructions field defaults to empty list."""
        r = RevisionRecord(
            revision_number=1,
            type="feedback",
            base_image_url="https://r2.example.com/base.png",
            revised_image_url="https://r2.example.com/rev1.png",
        )
        assert r.instructions == []

    def test_instructions_populated(self):
        """Instructions field can be populated."""
        r = RevisionRecord(
            revision_number=1,
            type="annotation",
            base_image_url="https://r2.example.com/base.png",
            revised_image_url="https://r2.example.com/rev1.png",
            instructions=["Replace the sofa", "Add a floor lamp"],
        )
        assert len(r.instructions) == 2
        assert r.instructions[0] == "Replace the sofa"


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


class TestEditDesignInput:
    """EditDesignInput accepts annotations, feedback, or both."""

    def test_with_annotations(self):
        """Valid input with annotation regions."""
        inp = EditDesignInput(
            project_id="proj-1",
            base_image_url="https://r2/base.png",
            room_photo_urls=["https://r2/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.4,
                    radius=0.2,
                    instruction="Replace the lamp with a floor lamp",
                )
            ],
        )
        assert len(inp.annotations) == 1
        assert inp.feedback is None

    def test_with_feedback(self):
        """Valid input with text feedback."""
        inp = EditDesignInput(
            project_id="proj-1",
            base_image_url="https://r2/base.png",
            room_photo_urls=["https://r2/room.jpg"],
            feedback="Make the room brighter and more open",
        )
        assert inp.feedback == "Make the room brighter and more open"
        assert inp.annotations == []

    def test_defaults(self):
        """Annotations and feedback can both be omitted (defaults)."""
        inp = EditDesignInput(
            project_id="proj-1",
            base_image_url="https://r2/base.png",
            room_photo_urls=["https://r2/room.jpg"],
        )
        assert inp.annotations == []
        assert inp.feedback is None
        assert inp.inspiration_photo_urls == []
        assert inp.design_brief is None
        assert inp.chat_history_key is None


class TestEditDesignOutput:
    """EditDesignOutput requires revised_image_url and chat_history_key."""

    def test_valid(self):
        """Valid output with both required fields."""
        out = EditDesignOutput(
            revised_image_url="https://r2/rev1.png",
            chat_history_key="proj-1/chat/abc123",
        )
        assert out.revised_image_url == "https://r2/rev1.png"
        assert out.chat_history_key == "proj-1/chat/abc123"

    def test_missing_chat_history_key_fails(self):
        """chat_history_key is required."""
        with pytest.raises(ValidationError):
            EditDesignOutput(revised_image_url="https://r2/rev1.png")


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
                    type="annotation",
                    base_image_url="https://r2/opt0.png",
                    revised_image_url="https://r2/rev1.png",
                    instructions=["Replace the sofa"],
                )
            ],
            iteration_count=1,
            error=WorkflowError(message="Revision failed", retryable=True),
            chat_history_key="proj-1/chat/abc123",
        )
        json_str = s.model_dump_json()
        restored = WorkflowState.model_validate_json(json_str)
        assert restored.step == "iteration"
        assert restored.selected_option == 0
        assert restored.error.retryable is True
        assert restored.revision_history[0].type == "annotation"
        assert restored.revision_history[0].instructions == ["Replace the sofa"]
        assert restored.chat_history_key == "proj-1/chat/abc123"

    def test_completed_state_all_fields(self):
        """Completed state with all fields populated — what iOS polls at end of flow.

        Exercises every WorkflowState field including scan_data, shopping_list,
        and approved=True, which the basic round-trip test doesn't cover.
        """
        s = WorkflowState(
            step="completed",
            photos=[
                PhotoData(
                    photo_id="p1",
                    storage_key="projects/1/photos/room_0.jpg",
                    photo_type="room",
                ),
                PhotoData(
                    photo_id="p2",
                    storage_key="projects/1/photos/inspo_0.jpg",
                    photo_type="inspiration",
                    note="Love the warm lighting",
                ),
            ],
            scan_data=ScanData(
                storage_key="projects/1/scan.json",
                room_dimensions=RoomDimensions(width_m=4.5, length_m=6.0, height_m=2.7),
            ),
            design_brief=DesignBrief(
                room_type="living room",
                pain_points=["too dark"],
                keep_items=["bookshelf"],
                style_profile=StyleProfile(lighting="warm"),
                inspiration_notes=[InspirationNote(photo_index=0, note="Love the warm lighting")],
            ),
            generated_options=[
                DesignOption(image_url="https://r2/opt0.png", caption="Modern"),
                DesignOption(image_url="https://r2/opt1.png", caption="Warm"),
            ],
            selected_option=1,
            current_image="https://r2/rev2.png",
            revision_history=[
                RevisionRecord(
                    revision_number=1,
                    type="annotation",
                    base_image_url="https://r2/opt1.png",
                    revised_image_url="https://r2/rev1.png",
                    instructions=["Replace the lamp"],
                ),
                RevisionRecord(
                    revision_number=2,
                    type="feedback",
                    base_image_url="https://r2/rev1.png",
                    revised_image_url="https://r2/rev2.png",
                ),
            ],
            iteration_count=2,
            shopping_list=GenerateShoppingListOutput(
                items=[
                    ProductMatch(
                        category_group="Furniture",
                        product_name="Accent Chair",
                        retailer="Store A",
                        price_cents=24999,
                        product_url="https://example.com/chair",
                        confidence_score=0.92,
                        why_matched="Matches style",
                    )
                ],
                unmatched=[
                    UnmatchedItem(
                        category="Rug",
                        search_keywords="modern area rug 5x7",
                        google_shopping_url="https://google.com/search?q=rug",
                    )
                ],
                total_estimated_cost_cents=24999,
            ),
            approved=True,
            error=None,
            chat_history_key="proj-1/chat/final",
        )
        json_str = s.model_dump_json()
        restored = WorkflowState.model_validate_json(json_str)
        assert restored.step == "completed"
        assert restored.approved is True
        assert len(restored.photos) == 2
        assert restored.photos[1].note == "Love the warm lighting"
        assert restored.scan_data is not None
        assert restored.scan_data.room_dimensions.width_m == 4.5
        assert restored.design_brief.inspiration_notes[0].note == "Love the warm lighting"
        assert restored.shopping_list is not None
        assert restored.shopping_list.items[0].product_name == "Accent Chair"
        assert len(restored.shopping_list.unmatched) == 1
        assert restored.shopping_list.total_estimated_cost_cents == 24999
        assert restored.selected_option == 1
        assert restored.iteration_count == 2
        assert len(restored.revision_history) == 2
        assert restored.error is None


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

    def test_annotation_edit_request_empty_fails(self):
        """AnnotationEditRequest must have at least 1 annotation."""
        with pytest.raises(ValidationError):
            AnnotationEditRequest(annotations=[])

    def test_text_feedback_request_empty_fails(self):
        """TextFeedbackRequest must have non-empty feedback."""
        with pytest.raises(ValidationError):
            TextFeedbackRequest(feedback="")

    def test_text_feedback_request_too_short_fails(self):
        """TextFeedbackRequest rejects feedback under 10 chars (REGEN-2)."""
        with pytest.raises(ValidationError):
            TextFeedbackRequest(feedback="darker")

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


class TestActivityInputRoundTrip:
    """JSON round-trip tests for all activity Input models.

    Critical for P2 integration: Temporal's pydantic data converter serializes
    activity inputs to JSON and deserializes on the worker. If a model doesn't
    survive round-trip, the activity will receive corrupted data at runtime.
    Each test uses fully-populated realistic data to catch nested model issues.
    """

    def test_generate_designs_input_round_trip(self):
        """GenerateDesignsInput with all fields survives JSON round-trip."""
        inp = GenerateDesignsInput(
            room_photo_urls=["photos/room_0.jpg", "photos/room_1.jpg"],
            inspiration_photo_urls=["photos/inspo_0.jpg"],
            inspiration_notes=[
                InspirationNote(
                    photo_index=0,
                    note="Love the warm lighting",
                    agent_clarification="User prefers amber tones",
                ),
            ],
            design_brief=DesignBrief(
                room_type="living room",
                occupants="couple with dog",
                lifestyle="Morning yoga, weekend hosting",
                pain_points=["too dark", "cluttered"],
                keep_items=["bookshelf"],
                style_profile=StyleProfile(
                    lighting="warm",
                    colors=["navy", "cream"],
                    textures=["velvet", "linen"],
                    clutter_level="minimal",
                    mood="cozy",
                ),
                constraints=["budget $5000"],
                inspiration_notes=[
                    InspirationNote(photo_index=0, note="moody lighting"),
                ],
            ),
            room_dimensions=RoomDimensions(
                width_m=4.5,
                length_m=6.0,
                height_m=2.7,
                walls=[{"id": "wall_0", "width": 4.5, "height": 2.7}],
                openings=[{"type": "door", "width": 0.9}],
            ),
            room_context=RoomContext(
                photo_analysis=RoomAnalysis(
                    room_type="living room",
                    hypothesis="Open-plan living area with natural light",
                ),
                # Deliberately different from top-level dims to catch swap bugs
                room_dimensions=RoomDimensions(width_m=4.6, length_m=6.1, height_m=2.8),
                enrichment_sources=["photos", "lidar"],
            ),
        )
        restored = GenerateDesignsInput.model_validate_json(inp.model_dump_json())
        assert restored.room_photo_urls == inp.room_photo_urls
        assert restored.inspiration_photo_urls == inp.inspiration_photo_urls
        assert len(restored.inspiration_notes) == 1
        assert restored.inspiration_notes[0].agent_clarification == "User prefers amber tones"
        assert restored.design_brief.lifestyle == "Morning yoga, weekend hosting"
        assert restored.design_brief.style_profile.mood == "cozy"
        assert restored.room_dimensions.width_m == 4.5
        assert len(restored.room_dimensions.walls) == 1
        assert restored.room_context is not None
        assert restored.room_context.enrichment_sources == ["photos", "lidar"]
        assert restored.room_context.photo_analysis is not None
        assert restored.room_context.photo_analysis.room_type == "living room"
        assert restored.room_context.photo_analysis.hypothesis == (
            "Open-plan living area with natural light"
        )
        assert restored.room_context.room_dimensions is not None
        assert restored.room_context.room_dimensions.width_m == 4.6
        assert restored.room_context.room_dimensions.length_m == 6.1
        assert restored.room_context.room_dimensions.height_m == 2.8

    def test_edit_design_input_annotation_round_trip(self):
        """EditDesignInput with annotations + room_context survives JSON round-trip."""
        inp = EditDesignInput(
            project_id="proj-abc",
            base_image_url="https://r2.example.com/base.png",
            room_photo_urls=["photos/room_0.jpg"],
            inspiration_photo_urls=["photos/inspo_0.jpg"],
            design_brief=DesignBrief(room_type="office"),
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.3,
                    center_y=0.7,
                    radius=0.15,
                    instruction="Replace the desk lamp with a standing lamp",
                ),
                AnnotationRegion(
                    region_id=2,
                    center_x=0.8,
                    center_y=0.2,
                    radius=0.1,
                    instruction="Remove the wall clock entirely",
                ),
            ],
            chat_history_key="chat/proj-abc/history.json",
            room_dimensions=RoomDimensions(width_m=3.8, length_m=4.2, height_m=2.6),
            room_context=RoomContext(
                photo_analysis=RoomAnalysis(
                    room_type="office",
                    hypothesis="Compact home office with desk and shelving",
                ),
                # Deliberately different from top-level dims to catch swap bugs
                room_dimensions=RoomDimensions(width_m=3.9, length_m=4.3, height_m=2.7),
                enrichment_sources=["photos", "lidar"],
            ),
        )
        restored = EditDesignInput.model_validate_json(inp.model_dump_json())
        assert restored.project_id == "proj-abc"
        assert len(restored.annotations) == 2
        assert restored.annotations[0].center_x == 0.3
        assert restored.annotations[1].region_id == 2
        assert restored.inspiration_photo_urls == ["photos/inspo_0.jpg"]
        assert restored.chat_history_key == "chat/proj-abc/history.json"
        assert restored.design_brief is not None
        assert restored.design_brief.room_type == "office"
        assert restored.room_dimensions is not None
        assert restored.room_dimensions.width_m == 3.8
        assert restored.room_dimensions.length_m == 4.2
        assert restored.room_context is not None
        assert restored.room_context.enrichment_sources == ["photos", "lidar"]
        assert restored.room_context.photo_analysis is not None
        assert restored.room_context.photo_analysis.room_type == "office"
        assert restored.room_context.photo_analysis.hypothesis == (
            "Compact home office with desk and shelving"
        )
        assert restored.room_context.room_dimensions is not None
        assert restored.room_context.room_dimensions.width_m == 3.9
        assert restored.room_context.room_dimensions.length_m == 4.3

    def test_edit_design_input_feedback_round_trip(self):
        """EditDesignInput with text feedback (no LiDAR) survives JSON round-trip."""
        inp = EditDesignInput(
            project_id="proj-def",
            base_image_url="https://r2.example.com/rev1.png",
            room_photo_urls=["photos/room_0.jpg", "photos/room_1.jpg"],
            feedback="Make the room brighter with warmer lighting throughout",
            room_context=RoomContext(
                photo_analysis=RoomAnalysis(
                    room_type="bedroom",
                    hypothesis="Dim bedroom needing better lighting",
                ),
                enrichment_sources=["photos"],
            ),
        )
        restored = EditDesignInput.model_validate_json(inp.model_dump_json())
        assert restored.feedback == "Make the room brighter with warmer lighting throughout"
        assert restored.annotations == []
        assert restored.design_brief is None
        assert restored.room_dimensions is None  # No LiDAR scan
        assert restored.room_context is not None
        assert restored.room_context.enrichment_sources == ["photos"]
        assert restored.room_context.photo_analysis is not None
        assert restored.room_context.photo_analysis.room_type == "bedroom"
        assert restored.room_context.photo_analysis.hypothesis == (
            "Dim bedroom needing better lighting"
        )
        assert restored.room_context.room_dimensions is None  # Photos-only path

    def test_generate_shopping_list_input_round_trip(self):
        """GenerateShoppingListInput with revision history survives JSON round-trip."""
        inp = GenerateShoppingListInput(
            design_image_url="https://r2.example.com/final.png",
            original_room_photo_urls=["photos/room_0.jpg", "photos/room_1.jpg"],
            design_brief=DesignBrief(
                room_type="bedroom",
                pain_points=["poor lighting"],
                keep_items=["bed frame"],
            ),
            revision_history=[
                RevisionRecord(
                    revision_number=1,
                    type="annotation",
                    base_image_url="https://r2.example.com/opt0.png",
                    revised_image_url="https://r2.example.com/rev1.png",
                    instructions=["Replace the lamp", "Add plants"],
                ),
                RevisionRecord(
                    revision_number=2,
                    type="feedback",
                    base_image_url="https://r2.example.com/rev1.png",
                    revised_image_url="https://r2.example.com/rev2.png",
                    instructions=["Make it warmer"],
                ),
            ],
            room_dimensions=RoomDimensions(width_m=3.5, length_m=4.0, height_m=2.5),
        )
        restored = GenerateShoppingListInput.model_validate_json(inp.model_dump_json())
        assert restored.design_image_url == inp.design_image_url
        assert len(restored.revision_history) == 2
        assert restored.revision_history[0].instructions == ["Replace the lamp", "Add plants"]
        assert restored.revision_history[1].type == "feedback"
        assert restored.room_dimensions.height_m == 2.5

    def test_shopping_input_with_room_context_round_trip(self):
        """GenerateShoppingListInput with room_context survives JSON round-trip."""
        inp = GenerateShoppingListInput(
            design_image_url="https://r2.example.com/final.png",
            original_room_photo_urls=["photos/room_0.jpg"],
            room_dimensions=RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.4),
            room_context=RoomContext(
                photo_analysis=RoomAnalysis(
                    room_type="living room",
                    hypothesis="Spacious room with good natural light",
                ),
                room_dimensions=RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.4),
                enrichment_sources=["photos", "lidar"],
            ),
        )
        restored = GenerateShoppingListInput.model_validate_json(inp.model_dump_json())
        assert restored.room_context is not None
        assert restored.room_context.enrichment_sources == ["photos", "lidar"]
        assert restored.room_context.photo_analysis.room_type == "living room"
        assert restored.room_context.room_dimensions.width_m == 4.0

    def test_intake_chat_input_round_trip(self):
        """IntakeChatInput with conversation history survives JSON round-trip."""
        inp = IntakeChatInput(
            mode="full",
            project_context={
                "room_photos": ["photos/room_0.jpg"],
                "inspiration_photos": ["photos/inspo_0.jpg"],
                "inspiration_notes": [{"photo_index": 0, "note": "warm tones"}],
                "previous_brief": {"room_type": "living room", "occupants": "family"},
            },
            conversation_history=[
                ChatMessage(role="assistant", content="What room is this?"),
                ChatMessage(role="user", content="It's my living room"),
                ChatMessage(role="assistant", content="What style do you like?"),
                ChatMessage(role="user", content="Modern minimalist"),
            ],
            user_message="I also want it to feel cozy",
        )
        restored = IntakeChatInput.model_validate_json(inp.model_dump_json())
        assert restored.mode == "full"
        assert len(restored.conversation_history) == 4
        assert restored.conversation_history[1].content == "It's my living room"
        assert restored.project_context["previous_brief"]["room_type"] == "living room"
        assert restored.user_message == "I also want it to feel cozy"


class TestActivityOutputRoundTrip:
    """JSON round-trip tests for all activity Output models.

    Verifies that activity outputs (produced by T2/T3 activities) survive
    serialization through Temporal's data converter back to the workflow.
    """

    def test_generate_designs_output_round_trip(self):
        """GenerateDesignsOutput with 2 options round-trips correctly."""
        out = GenerateDesignsOutput(
            options=[
                DesignOption(
                    image_url="https://r2.example.com/opt0.png",
                    caption="Warm minimalist — linen sofa, walnut coffee table",
                ),
                DesignOption(
                    image_url="https://r2.example.com/opt1.png",
                    caption="Scandi modern — light oak, white textiles",
                ),
            ]
        )
        restored = GenerateDesignsOutput.model_validate_json(out.model_dump_json())
        assert len(restored.options) == 2
        assert "walnut" in restored.options[0].caption
        assert restored.options[1].image_url == "https://r2.example.com/opt1.png"

    def test_edit_design_output_round_trip(self):
        """EditDesignOutput round-trips correctly."""
        out = EditDesignOutput(
            revised_image_url="https://r2.example.com/rev3.png",
            chat_history_key="chat/proj-abc/history.json",
        )
        restored = EditDesignOutput.model_validate_json(out.model_dump_json())
        assert restored.revised_image_url == out.revised_image_url
        assert restored.chat_history_key == out.chat_history_key

    def test_generate_shopping_list_output_round_trip(self):
        """GenerateShoppingListOutput with items and unmatched round-trips correctly."""
        out = GenerateShoppingListOutput(
            items=[
                ProductMatch(
                    category_group="Furniture",
                    product_name="IKEA KIVIK Sofa",
                    retailer="IKEA",
                    price_cents=79900,
                    product_url="https://ikea.com/kivik",
                    image_url="https://ikea.com/kivik.jpg",
                    confidence_score=0.92,
                    why_matched="Similar L-shaped sectional in grey fabric",
                    fit_status="fits",
                    fit_detail="Fits within room width",
                    dimensions='98"W x 37"D x 32"H',
                ),
                ProductMatch(
                    category_group="Lighting",
                    product_name="West Elm Tripod Lamp",
                    retailer="West Elm",
                    price_cents=19900,
                    product_url="https://westelm.com/tripod",
                    confidence_score=0.78,
                    why_matched="Matches warm ambient lighting preference",
                ),
            ],
            unmatched=[
                UnmatchedItem(
                    category="Area Rug",
                    search_keywords="modern geometric rug 5x7 navy",
                    google_shopping_url="https://google.com/search?tbm=shop&q=rug",
                ),
            ],
            total_estimated_cost_cents=99800,
        )
        restored = GenerateShoppingListOutput.model_validate_json(out.model_dump_json())
        assert len(restored.items) == 2
        assert restored.items[0].dimensions == '98"W x 37"D x 32"H'
        assert restored.items[1].image_url is None
        assert len(restored.unmatched) == 1
        assert restored.unmatched[0].category == "Area Rug"
        assert restored.total_estimated_cost_cents == 99800

    def test_intake_chat_output_round_trip(self):
        """IntakeChatOutput with all fields populated round-trips correctly."""
        out = IntakeChatOutput(
            agent_message="Great choice! What activities do you do in this room?",
            options=[
                QuickReplyOption(number=1, label="Relaxing", value="relaxing"),
                QuickReplyOption(number=2, label="Working", value="working"),
                QuickReplyOption(number=3, label="Entertaining", value="entertaining"),
            ],
            is_open_ended=False,
            progress="2/4 domains covered",
            is_summary=False,
            partial_brief=DesignBrief(
                room_type="living room",
                style_profile=StyleProfile(lighting="warm", mood="cozy"),
            ),
        )
        restored = IntakeChatOutput.model_validate_json(out.model_dump_json())
        assert restored.agent_message == out.agent_message
        assert len(restored.options) == 3
        assert restored.options[2].value == "entertaining"
        assert restored.progress == "2/4 domains covered"
        assert restored.partial_brief.style_profile.mood == "cozy"

    def test_intake_chat_output_requested_skills_round_trip(self):
        """IntakeChatOutput with requested_skills round-trips correctly."""
        out = IntakeChatOutput(
            agent_message="I see you want cozy!",
            requested_skills=["cozy", "modern"],
        )
        restored = IntakeChatOutput.model_validate_json(out.model_dump_json())
        assert restored.requested_skills == ["cozy", "modern"]

    def test_intake_chat_output_requested_skills_default_empty(self):
        """IntakeChatOutput defaults requested_skills to empty list."""
        out = IntakeChatOutput(agent_message="Hello!")
        assert out.requested_skills == []
        restored = IntakeChatOutput.model_validate_json(out.model_dump_json())
        assert restored.requested_skills == []

    def test_intake_chat_output_summary_round_trip(self):
        """IntakeChatOutput summary (is_summary=True) round-trips correctly."""
        out = IntakeChatOutput(
            agent_message="Here's your design brief summary...",
            is_summary=True,
            partial_brief=DesignBrief(
                room_type="bedroom",
                occupants="couple",
                lifestyle="Morning meditation, reading",
                pain_points=["too dark", "cluttered nightstands"],
                keep_items=["king bed", "dresser"],
                style_profile=StyleProfile(
                    lighting="warm",
                    colors=["sage", "cream", "walnut"],
                    textures=["linen", "wood"],
                    clutter_level="minimal",
                    mood="serene",
                ),
                constraints=["budget $3000"],
                inspiration_notes=[
                    InspirationNote(photo_index=0, note="Love the headboard"),
                ],
            ),
        )
        restored = IntakeChatOutput.model_validate_json(out.model_dump_json())
        assert restored.is_summary is True
        assert restored.options is None
        assert restored.partial_brief.lifestyle == "Morning meditation, reading"
        assert len(restored.partial_brief.style_profile.colors) == 3


class TestMockStubOutputConformance:
    """Verify mock stubs produce outputs that satisfy all contract constraints.

    The mock stubs in mock_stubs.py are used by the Temporal worker during
    development. Their outputs must be valid contract models — otherwise
    the workflow tests pass with invalid data and break when real activities
    are wired.

    Note: No intake chat mock exists in mock_stubs.py — T3 owns the real
    implementation and the mock API uses canned responses instead. The
    IntakeChatOutput round-trip is covered in TestActivityOutputRoundTrip.
    """

    @pytest.mark.asyncio
    async def test_mock_generate_designs_output(self):
        """Mock generate_designs returns valid GenerateDesignsOutput (2 options)."""
        from app.activities.mock_stubs import FORCE_FAILURE_SENTINEL

        # Clean up any leftover sentinel from E2E error-injection tests
        FORCE_FAILURE_SENTINEL.unlink(missing_ok=True)

        inp = GenerateDesignsInput(
            room_photo_urls=["photos/room_0.jpg", "photos/room_1.jpg"],
        )
        out = await mock_generate_designs(inp)
        assert isinstance(out, GenerateDesignsOutput)
        assert len(out.options) == 2
        # Verify specific mock URLs (not just non-empty)
        assert "r2.example.com/mock/option_0.png" in out.options[0].image_url
        assert "r2.example.com/mock/option_1.png" in out.options[1].image_url
        assert out.options[0].caption == "Mock A"
        assert out.options[1].caption == "Mock B"
        # Round-trip through JSON
        restored = GenerateDesignsOutput.model_validate_json(out.model_dump_json())
        assert len(restored.options) == 2
        assert restored.options[0].image_url == out.options[0].image_url

    @pytest.mark.asyncio
    async def test_mock_generate_designs_force_failure(self):
        """Mock generate_designs raises ApplicationError when sentinel file exists.

        Covers the one-shot error injection path (mock_stubs.py:37-39) used by
        E2E-11 to test the error → retry cycle.
        """
        from temporalio.exceptions import ApplicationError

        from app.activities.mock_stubs import FORCE_FAILURE_SENTINEL

        FORCE_FAILURE_SENTINEL.touch()
        inp = GenerateDesignsInput(
            room_photo_urls=["photos/room_0.jpg"],
        )
        with pytest.raises(ApplicationError, match="Injected failure"):
            await mock_generate_designs(inp)
        # Sentinel is consumed (deleted) after the error
        assert not FORCE_FAILURE_SENTINEL.exists()

    @pytest.mark.asyncio
    async def test_mock_edit_design_output(self):
        """Mock edit_design returns valid EditDesignOutput with project-scoped key."""
        inp = EditDesignInput(
            project_id="test-proj",
            base_image_url="https://r2.example.com/base.png",
            room_photo_urls=["photos/room_0.jpg"],
            feedback="Make it brighter",
        )
        out = await mock_edit_design(inp)
        assert isinstance(out, EditDesignOutput)
        # Verify mock returns unique URL with expected prefix
        assert out.revised_image_url.startswith("https://r2.example.com/mock/edit_")
        assert out.revised_image_url.endswith(".png")
        assert out.chat_history_key == "chat/test-proj/history.json"
        # Two calls produce different URLs (unique per invocation)
        out2 = await mock_edit_design(inp)
        assert out2.revised_image_url != out.revised_image_url
        # Round-trip
        restored = EditDesignOutput.model_validate_json(out.model_dump_json())
        assert restored.revised_image_url == out.revised_image_url
        assert restored.chat_history_key == out.chat_history_key

    @pytest.mark.asyncio
    async def test_mock_generate_shopping_list_output(self):
        """Mock generate_shopping_list returns valid GenerateShoppingListOutput."""
        inp = GenerateShoppingListInput(
            design_image_url="https://r2.example.com/final.png",
            original_room_photo_urls=["photos/room_0.jpg"],
        )
        out = await mock_generate_shopping_list(inp)
        assert isinstance(out, GenerateShoppingListOutput)
        assert out.total_estimated_cost_cents == 9999
        assert len(out.items) == 1
        # Verify the mock item has specific known values
        item = out.items[0]
        assert item.category_group == "Furniture"
        assert item.product_name == "Mock Chair"
        assert item.price_cents == 9999
        assert item.confidence_score == 0.9
        # Phase 1a: cost_breakdown populated
        assert out.cost_breakdown is not None
        assert out.cost_breakdown.materials_cents == 9999
        assert out.cost_breakdown.total_low_cents == 9999
        assert out.cost_breakdown.total_high_cents == 12000
        # Round-trip
        restored = GenerateShoppingListOutput.model_validate_json(out.model_dump_json())
        assert restored.total_estimated_cost_cents == 9999
        assert restored.items[0].product_name == "Mock Chair"
        assert restored.cost_breakdown.materials_cents == 9999

    @pytest.mark.asyncio
    async def test_mock_load_style_skill_known_ids(self):
        """Mock load_style_skill returns packs for known skill IDs."""
        out = await mock_load_style_skill(
            LoadSkillInput(skill_ids=["mid-century-modern", "japandi"])
        )
        assert isinstance(out, LoadSkillOutput)
        assert len(out.skill_packs) == 2
        assert out.not_found == []
        ids = {p.skill_id for p in out.skill_packs}
        assert ids == {"mid-century-modern", "japandi"}

    @pytest.mark.asyncio
    async def test_mock_load_style_skill_mixed(self):
        """Mock load_style_skill handles mix of known and unknown IDs."""
        out = await mock_load_style_skill(LoadSkillInput(skill_ids=["japandi", "art-deco"]))
        assert len(out.skill_packs) == 1
        assert out.skill_packs[0].skill_id == "japandi"
        assert out.not_found == ["art-deco"]

    @pytest.mark.asyncio
    async def test_mock_load_style_skill_all_unknown(self):
        """Mock load_style_skill returns all IDs as not_found if unknown."""
        out = await mock_load_style_skill(LoadSkillInput(skill_ids=["unknown-style"]))
        assert len(out.skill_packs) == 0
        assert out.not_found == ["unknown-style"]


# ---------------------------------------------------------------------------
# Phase 1a: Skill System Models
# ---------------------------------------------------------------------------


class TestSkillSummary:
    """SkillSummary — lightweight skill reference."""

    def test_valid(self):
        """Valid construction with all fields."""
        s = SkillSummary(
            skill_id="mid-century-modern",
            name="Mid-Century Modern",
            description="Clean lines, organic curves",
            style_tags=["retro", "minimal"],
        )
        assert s.skill_id == "mid-century-modern"
        assert len(s.style_tags) == 2

    def test_style_tags_default_empty(self):
        """style_tags defaults to empty list."""
        s = SkillSummary(skill_id="x", name="X", description="X")
        assert s.style_tags == []

    def test_round_trip(self):
        """JSON serialization round-trip."""
        s = SkillSummary(skill_id="a", name="A", description="A", style_tags=["t"])
        restored = SkillSummary.model_validate_json(s.model_dump_json())
        assert restored.style_tags == ["t"]


class TestStyleSkillPack:
    """StyleSkillPack — full knowledge pack with versioning."""

    def test_valid_with_knowledge(self):
        """Valid pack with knowledge dict."""
        p = StyleSkillPack(
            skill_id="japandi",
            name="Japandi",
            description="Japanese minimalism meets Scandinavian warmth",
            version=2,
            style_tags=["minimal", "warm"],
            applicable_room_types=["living room", "bedroom"],
            knowledge={"principles": ["wabi-sabi", "hygge"]},
        )
        assert p.version == 2
        assert p.knowledge["principles"][0] == "wabi-sabi"

    def test_version_default_one(self):
        """Version defaults to 1."""
        p = StyleSkillPack(skill_id="x", name="X", description="X")
        assert p.version == 1

    def test_version_zero_fails(self):
        """Version must be >= 1."""
        with pytest.raises(ValidationError):
            StyleSkillPack(skill_id="x", name="X", description="X", version=0)

    def test_empty_knowledge_valid(self):
        """Empty knowledge dict is valid (T3 defines contents)."""
        p = StyleSkillPack(skill_id="x", name="X", description="X", knowledge={})
        assert p.knowledge == {}

    def test_round_trip(self):
        """JSON round-trip preserves knowledge dict."""
        p = StyleSkillPack(
            skill_id="a",
            name="A",
            description="A",
            knowledge={"k": [1, 2, 3]},
        )
        restored = StyleSkillPack.model_validate_json(p.model_dump_json())
        assert restored.knowledge == {"k": [1, 2, 3]}


class TestSkillManifest:
    """SkillManifest — available skills for a project."""

    def test_empty_valid(self):
        """Empty manifest is valid."""
        m = SkillManifest()
        assert m.skills == []
        assert m.default_skill_ids == []

    def test_with_skills(self):
        """Manifest with skills and defaults."""
        m = SkillManifest(
            skills=[
                SkillSummary(skill_id="mcm", name="MCM", description="Mid-Century"),
                SkillSummary(skill_id="jap", name="Japandi", description="Japandi"),
            ],
            default_skill_ids=["mcm"],
        )
        assert len(m.skills) == 2
        assert m.default_skill_ids == ["mcm"]


class TestLoadSkillInput:
    """LoadSkillInput — activity input for skill loading."""

    def test_valid_single(self):
        """Single skill ID is valid."""
        inp = LoadSkillInput(skill_ids=["mid-century-modern"])
        assert len(inp.skill_ids) == 1

    def test_valid_multiple(self):
        """Multiple skill IDs are valid."""
        inp = LoadSkillInput(skill_ids=["a", "b", "c"])
        assert len(inp.skill_ids) == 3

    def test_empty_fails(self):
        """Empty list fails (min_length=1)."""
        with pytest.raises(ValidationError):
            LoadSkillInput(skill_ids=[])


class TestLoadSkillOutput:
    """LoadSkillOutput — activity output with loaded packs."""

    def test_empty_valid(self):
        """All defaults are valid (no packs loaded, nothing not found)."""
        out = LoadSkillOutput()
        assert out.skill_packs == []
        assert out.not_found == []

    def test_with_packs_and_not_found(self):
        """Mix of loaded packs and not-found IDs."""
        out = LoadSkillOutput(
            skill_packs=[
                StyleSkillPack(skill_id="a", name="A", description="A"),
            ],
            not_found=["b", "c"],
        )
        assert len(out.skill_packs) == 1
        assert out.not_found == ["b", "c"]

    def test_round_trip(self):
        """JSON round-trip."""
        out = LoadSkillOutput(
            skill_packs=[
                StyleSkillPack(
                    skill_id="x",
                    name="X",
                    description="X",
                    knowledge={"k": "v"},
                ),
            ],
            not_found=["y"],
        )
        restored = LoadSkillOutput.model_validate_json(out.model_dump_json())
        assert restored.skill_packs[0].knowledge == {"k": "v"}
        assert restored.not_found == ["y"]


# ---------------------------------------------------------------------------
# Phase 1a: Cost/Feasibility Models
# ---------------------------------------------------------------------------


class TestFeasibilityNote:
    """FeasibilityNote — renovation intervention assessment."""

    def test_valid(self):
        """Valid feasibility note."""
        n = FeasibilityNote(
            intervention="Remove wall between kitchen and living room",
            assessment="needs_verification",
            confidence=0.7,
            explanation="Could be load-bearing; needs structural assessment",
            cost_impact="adds $2-5K for structural engineer",
            professional_needed="structural engineer",
        )
        assert n.assessment == "needs_verification"
        assert n.confidence == 0.7

    def test_invalid_assessment_rejected(self):
        """Invalid assessment value is rejected by Literal type."""
        with pytest.raises(ValidationError):
            FeasibilityNote(
                intervention="test",
                assessment="maybe",
                confidence=0.5,
                explanation="test",
            )

    def test_all_valid_assessments(self):
        """All four assessment values are accepted."""
        for value in [
            "likely_feasible",
            "needs_verification",
            "risky",
            "not_feasible",
        ]:
            n = FeasibilityNote(
                intervention="test",
                assessment=value,
                confidence=0.5,
                explanation="test",
            )
            assert n.assessment == value

    def test_confidence_out_of_range(self):
        """Confidence must be 0-1."""
        with pytest.raises(ValidationError):
            FeasibilityNote(
                intervention="test",
                assessment="risky",
                confidence=1.5,
                explanation="test",
            )

    def test_optional_fields_default_none(self):
        """cost_impact and professional_needed default to None."""
        n = FeasibilityNote(
            intervention="test",
            assessment="likely_feasible",
            confidence=0.9,
            explanation="Simple paint job",
        )
        assert n.cost_impact is None
        assert n.professional_needed is None


class TestProfessionalFee:
    """ProfessionalFee — estimated cost for a professional service."""

    def test_valid(self):
        """Valid professional fee."""
        f = ProfessionalFee(
            professional_type="structural engineer",
            reason="Load-bearing wall assessment",
            estimate_cents=50000,
        )
        assert f.estimate_cents == 50000

    def test_negative_estimate_fails(self):
        """Negative estimate is rejected."""
        with pytest.raises(ValidationError):
            ProfessionalFee(
                professional_type="plumber",
                reason="test",
                estimate_cents=-100,
            )


class TestCostBreakdown:
    """CostBreakdown — detailed project cost breakdown."""

    def test_minimal_defaults(self):
        """All defaults produce valid model."""
        c = CostBreakdown()
        assert c.materials_cents == 0
        assert c.labor_estimate_cents is None
        assert c.professional_fees == []
        assert c.total_low_cents == 0
        assert c.total_high_cents == 0

    def test_full(self):
        """Full cost breakdown with professional fees."""
        c = CostBreakdown(
            materials_cents=150000,
            labor_estimate_cents=80000,
            labor_estimate_note="2 days painting + 1 day installation",
            professional_fees=[
                ProfessionalFee(
                    professional_type="painter",
                    reason="Full room repaint",
                    estimate_cents=40000,
                ),
            ],
            permit_fees_estimate_cents=5000,
            total_low_cents=275000,
            total_high_cents=350000,
        )
        assert c.materials_cents == 150000
        assert len(c.professional_fees) == 1
        assert c.permit_fees_estimate_cents == 5000

    def test_negative_materials_fails(self):
        """Negative materials_cents is rejected."""
        with pytest.raises(ValidationError):
            CostBreakdown(materials_cents=-1)

    def test_negative_total_fails(self):
        """Negative total is rejected."""
        with pytest.raises(ValidationError):
            CostBreakdown(total_low_cents=-1)

    def test_round_trip(self):
        """JSON round-trip with nested ProfessionalFee."""
        c = CostBreakdown(
            materials_cents=9999,
            professional_fees=[
                ProfessionalFee(
                    professional_type="engineer",
                    reason="assessment",
                    estimate_cents=50000,
                ),
            ],
            total_low_cents=59999,
            total_high_cents=80000,
        )
        restored = CostBreakdown.model_validate_json(c.model_dump_json())
        assert restored.professional_fees[0].estimate_cents == 50000
        assert restored.total_high_cents == 80000


class TestRenovationIntent:
    """RenovationIntent — user's renovation scope."""

    def test_cosmetic(self):
        """Valid cosmetic renovation."""
        r = RenovationIntent(
            scope="cosmetic",
            interventions=["repaint walls", "new curtains"],
        )
        assert r.scope == "cosmetic"
        assert len(r.interventions) == 2

    def test_structural_with_feasibility(self):
        """Structural scope with feasibility notes."""
        r = RenovationIntent(
            scope="structural",
            interventions=["remove wall"],
            feasibility_notes=[
                FeasibilityNote(
                    intervention="remove wall",
                    assessment="risky",
                    confidence=0.6,
                    explanation="Likely load-bearing",
                    professional_needed="structural engineer",
                ),
            ],
            estimated_permits=["building permit"],
        )
        assert r.scope == "structural"
        assert len(r.feasibility_notes) == 1
        assert r.estimated_permits == ["building permit"]

    def test_invalid_scope_rejected(self):
        """Invalid scope value is rejected."""
        with pytest.raises(ValidationError):
            RenovationIntent(scope="nuclear")

    def test_all_valid_scopes(self):
        """All three scope values are accepted."""
        for scope in ["cosmetic", "moderate", "structural"]:
            r = RenovationIntent(scope=scope)
            assert r.scope == scope

    def test_defaults(self):
        """Lists default to empty."""
        r = RenovationIntent(scope="cosmetic")
        assert r.interventions == []
        assert r.feasibility_notes == []
        assert r.estimated_permits == []


# ---------------------------------------------------------------------------
# Phase 1a: Evolution / Backward Compatibility
# ---------------------------------------------------------------------------


class TestDesignBriefEvolution:
    """DesignBrief backward compatibility with Phase 1a fields."""

    def test_existing_minimal_still_works(self):
        """Old-style DesignBrief (no new fields) still valid."""
        b = DesignBrief(room_type="living room")
        assert b.style_skills_used == []
        assert b.renovation_intent is None

    def test_with_new_fields(self):
        """DesignBrief with Phase 1a fields."""
        b = DesignBrief(
            room_type="kitchen",
            style_skills_used=["mid-century-modern", "japandi"],
            renovation_intent=RenovationIntent(
                scope="moderate",
                interventions=["replace countertops"],
                feasibility_notes=[
                    FeasibilityNote(
                        intervention="replace countertops",
                        assessment="likely_feasible",
                        confidence=0.9,
                        explanation="Standard granite swap",
                    ),
                ],
            ),
        )
        assert len(b.style_skills_used) == 2
        assert b.renovation_intent.scope == "moderate"

    def test_forward_compat_old_json(self):
        """Old JSON without new fields deserializes correctly."""
        old_json = '{"room_type": "bedroom", "pain_points": ["too dark"]}'
        b = DesignBrief.model_validate_json(old_json)
        assert b.room_type == "bedroom"
        assert b.style_skills_used == []
        assert b.renovation_intent is None

    def test_round_trip_with_renovation(self):
        """DesignBrief with renovation_intent round-trips through JSON."""
        b = DesignBrief(
            room_type="office",
            renovation_intent=RenovationIntent(
                scope="structural",
                feasibility_notes=[
                    FeasibilityNote(
                        intervention="open wall",
                        assessment="not_feasible",
                        confidence=0.95,
                        explanation="Exterior wall",
                    ),
                ],
            ),
        )
        restored = DesignBrief.model_validate_json(b.model_dump_json())
        assert restored.renovation_intent.scope == "structural"
        assert restored.renovation_intent.feasibility_notes[0].confidence == 0.95


class TestShoppingListOutputEvolution:
    """GenerateShoppingListOutput backward compatibility with cost_breakdown."""

    def test_existing_without_cost_breakdown(self):
        """Old-style output (no cost_breakdown) still valid."""
        out = GenerateShoppingListOutput(items=[], total_estimated_cost_cents=0)
        assert out.cost_breakdown is None

    def test_with_cost_breakdown(self):
        """Output with CostBreakdown."""
        out = GenerateShoppingListOutput(
            items=[],
            total_estimated_cost_cents=150000,
            cost_breakdown=CostBreakdown(
                materials_cents=100000,
                labor_estimate_cents=50000,
                total_low_cents=150000,
                total_high_cents=200000,
            ),
        )
        assert out.cost_breakdown.materials_cents == 100000

    def test_forward_compat_old_json(self):
        """Old JSON without cost_breakdown deserializes correctly."""
        old_json = '{"items": [], "total_estimated_cost_cents": 5000}'
        out = GenerateShoppingListOutput.model_validate_json(old_json)
        assert out.cost_breakdown is None
        assert out.total_estimated_cost_cents == 5000

    def test_round_trip_with_cost_breakdown(self):
        """Shopping list with cost_breakdown survives JSON round-trip."""
        out = GenerateShoppingListOutput(
            items=[
                ProductMatch(
                    category_group="Furniture",
                    product_name="Chair",
                    retailer="Store",
                    price_cents=50000,
                    product_url="https://example.com",
                    confidence_score=0.9,
                    why_matched="matched",
                ),
            ],
            total_estimated_cost_cents=50000,
            cost_breakdown=CostBreakdown(
                materials_cents=50000,
                professional_fees=[
                    ProfessionalFee(
                        professional_type="designer",
                        reason="consultation",
                        estimate_cents=20000,
                    ),
                ],
                total_low_cents=70000,
                total_high_cents=90000,
            ),
        )
        restored = GenerateShoppingListOutput.model_validate_json(out.model_dump_json())
        assert restored.cost_breakdown.materials_cents == 50000
        assert len(restored.cost_breakdown.professional_fees) == 1


class TestIntakeChatInputEvolution:
    """IntakeChatInput backward compatibility with available_skills."""

    def test_existing_without_skills(self):
        """Old-style input (no available_skills) still valid."""
        inp = IntakeChatInput(
            mode="full",
            project_context={},
            conversation_history=[],
            user_message="hello",
        )
        assert inp.available_skills == []

    def test_with_skills(self):
        """Input with available skills."""
        inp = IntakeChatInput(
            mode="quick",
            project_context={},
            conversation_history=[],
            user_message="hello",
            available_skills=[
                SkillSummary(
                    skill_id="mcm",
                    name="Mid-Century Modern",
                    description="Clean lines",
                ),
            ],
        )
        assert len(inp.available_skills) == 1

    def test_forward_compat_old_json(self):
        """Old JSON without available_skills deserializes correctly."""
        old_json = (
            '{"mode": "full", "project_context": {}, '
            '"conversation_history": [], "user_message": "hi"}'
        )
        inp = IntakeChatInput.model_validate_json(old_json)
        assert inp.available_skills == []


class TestAllModelsImportable:
    """Verify all models from contracts can be imported via wildcard."""

    def test_star_import(self):
        """All public models are importable from contracts module."""
        import app.models.contracts as c

        expected = [
            "StyleProfile",
            "InspirationNote",
            "SkillSummary",
            "StyleSkillPack",
            "SkillManifest",
            "FeasibilityNote",
            "ProfessionalFee",
            "CostBreakdown",
            "RenovationIntent",
            "DesignBrief",
            "RoomDimensions",
            "AnnotationRegion",
            "DesignOption",
            "ProductMatch",
            "UnmatchedItem",
            "ChatMessage",
            "QuickReplyOption",
            "WorkflowError",
            "RevisionRecord",
            "PhotoData",
            "ScanData",
            # Room Analysis (Designer Brain)
            "LightingAssessment",
            "FurnitureObservation",
            "BehavioralSignal",
            "RoomAnalysis",
            "RoomContext",
            "AnalyzeRoomPhotosInput",
            "AnalyzeRoomPhotosOutput",
            # Activity I/O
            "GenerateDesignsInput",
            "GenerateDesignsOutput",
            "EditDesignInput",
            "EditDesignOutput",
            "GenerateShoppingListInput",
            "GenerateShoppingListOutput",
            "IntakeChatInput",
            "IntakeChatOutput",
            "LoadSkillInput",
            "LoadSkillOutput",
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
            "AnnotationEditRequest",
            "TextFeedbackRequest",
            "ActionResponse",
            "ErrorResponse",
        ]
        for name in expected:
            assert hasattr(c, name), f"Missing model: {name}"


# === Designer Brain Contract Tests ===


class TestLightingAssessment:
    """LightingAssessment is fully optional — all fields default to None/empty."""

    def test_empty_valid(self):
        """All fields are optional; empty construction succeeds."""
        la = LightingAssessment()
        assert la.natural_light_direction is None
        assert la.lighting_gaps == []

    def test_full_valid(self):
        """All fields populated."""
        la = LightingAssessment(
            natural_light_direction="south-facing windows",
            natural_light_intensity="abundant",
            window_coverage="full wall",
            existing_artificial="layered",
            lighting_gaps=["dark reading corner"],
        )
        assert la.natural_light_intensity == "abundant"
        assert len(la.lighting_gaps) == 1

    def test_round_trip(self):
        """JSON serialize → deserialize preserves all fields."""
        la = LightingAssessment(
            natural_light_direction="north",
            lighting_gaps=["no task lighting", "dim hallway"],
        )
        restored = LightingAssessment.model_validate_json(la.model_dump_json())
        assert restored == la


class TestFurnitureObservation:
    """FurnitureObservation requires item, rest optional."""

    def test_minimal_valid(self):
        """Only item is required."""
        fo = FurnitureObservation(item="gray sofa")
        assert fo.condition is None
        assert fo.keep_candidate is False

    def test_full_valid(self):
        """All fields populated."""
        fo = FurnitureObservation(
            item="L-shaped sectional",
            condition="worn",
            placement_note="faces wall instead of window",
            keep_candidate=True,
        )
        assert fo.keep_candidate is True

    def test_missing_item_fails(self):
        """Item is required."""
        with pytest.raises(ValidationError):
            FurnitureObservation()

    def test_round_trip(self):
        """JSON serialize → deserialize preserves all fields."""
        fo = FurnitureObservation(item="chair", condition="good", keep_candidate=True)
        restored = FurnitureObservation.model_validate_json(fo.model_dump_json())
        assert restored == fo


class TestBehavioralSignal:
    """BehavioralSignal requires observation and inference."""

    def test_minimal_valid(self):
        """Observation + inference are required, design_implication optional."""
        bs = BehavioralSignal(observation="toys on floor", inference="young children")
        assert bs.design_implication is None

    def test_full_valid(self):
        """All fields populated."""
        bs = BehavioralSignal(
            observation="books stacked near armchair",
            inference="active reader lacking storage",
            design_implication="add reading nook with task lighting",
        )
        assert bs.design_implication is not None

    def test_missing_inference_fails(self):
        """Inference is required."""
        with pytest.raises(ValidationError):
            BehavioralSignal(observation="toys on floor")

    def test_round_trip(self):
        """JSON serialize → deserialize preserves all fields."""
        bs = BehavioralSignal(
            observation="pet bed", inference="dog owner", design_implication="durable fabrics"
        )
        restored = BehavioralSignal.model_validate_json(bs.model_dump_json())
        assert restored == bs


class TestRoomAnalysis:
    """RoomAnalysis — the core photo analysis model."""

    def test_empty_valid(self):
        """All fields have defaults; empty construction succeeds."""
        ra = RoomAnalysis()
        assert ra.room_type is None
        assert ra.room_type_confidence == 0.5
        assert ra.furniture == []
        assert ra.photo_count == 0

    def test_full_valid(self):
        """Full construction with all nested models."""
        ra = RoomAnalysis(
            room_type="living room",
            room_type_confidence=0.9,
            estimated_dimensions="12x15 feet",
            layout_pattern="open plan",
            lighting=LightingAssessment(natural_light_intensity="abundant"),
            furniture=[FurnitureObservation(item="sofa", keep_candidate=True)],
            architectural_features=["crown molding"],
            flooring="hardwood",
            existing_palette=["gray", "oak"],
            overall_warmth="warm",
            style_signals=["mid-century"],
            behavioral_signals=[BehavioralSignal(observation="books", inference="reader")],
            tensions=["modern furniture vs traditional architecture"],
            hypothesis="Warm family room with good bones",
            strengths=["natural light"],
            opportunities=["better furniture layout"],
            uncertain_aspects=["ceiling height"],
            photo_count=3,
        )
        assert ra.room_type_confidence == 0.9
        assert len(ra.furniture) == 1
        assert ra.furniture[0].keep_candidate is True

    def test_confidence_bounds(self):
        """room_type_confidence must be 0-1."""
        with pytest.raises(ValidationError):
            RoomAnalysis(room_type_confidence=1.5)
        with pytest.raises(ValidationError):
            RoomAnalysis(room_type_confidence=-0.1)

    def test_confidence_edges(self):
        """Boundary values 0 and 1 are valid."""
        assert RoomAnalysis(room_type_confidence=0.0).room_type_confidence == 0.0
        assert RoomAnalysis(room_type_confidence=1.0).room_type_confidence == 1.0

    def test_round_trip(self):
        """Full round-trip serialization with nested objects."""
        ra = RoomAnalysis(
            room_type="bedroom",
            lighting=LightingAssessment(natural_light_direction="east"),
            furniture=[FurnitureObservation(item="bed", condition="good")],
            behavioral_signals=[
                BehavioralSignal(observation="alarm clock", inference="early riser")
            ],
            hypothesis="Cozy bedroom",
            photo_count=2,
        )
        restored = RoomAnalysis.model_validate_json(ra.model_dump_json())
        assert restored == ra
        assert restored.furniture[0].item == "bed"
        assert restored.behavioral_signals[0].inference == "early riser"


class TestRoomContext:
    """RoomContext — progressive enrichment container."""

    def test_empty_valid(self):
        """All fields are optional; empty construction succeeds."""
        rc = RoomContext()
        assert rc.photo_analysis is None
        assert rc.room_dimensions is None
        assert rc.enrichment_sources == []

    def test_photo_only(self):
        """Photo analysis without LiDAR."""
        rc = RoomContext(
            photo_analysis=RoomAnalysis(room_type="kitchen"),
            enrichment_sources=["photos"],
        )
        assert rc.room_dimensions is None
        assert "photos" in rc.enrichment_sources

    def test_full_enrichment(self):
        """Both photo analysis and LiDAR dimensions."""
        rc = RoomContext(
            photo_analysis=RoomAnalysis(room_type="office"),
            room_dimensions=RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7),
            enrichment_sources=["photos", "lidar"],
        )
        assert rc.room_dimensions.width_m == 4.0
        assert len(rc.enrichment_sources) == 2

    def test_round_trip(self):
        """JSON round-trip with nested models."""
        rc = RoomContext(
            photo_analysis=RoomAnalysis(hypothesis="test"),
            room_dimensions=RoomDimensions(width_m=3.0, length_m=4.0, height_m=2.5),
            enrichment_sources=["photos", "lidar"],
        )
        restored = RoomContext.model_validate_json(rc.model_dump_json())
        assert restored == rc


class TestAnalyzeRoomPhotosIO:
    """Activity I/O contracts for room photo analysis."""

    def test_input_minimal(self):
        """Only room_photo_urls is required."""
        inp = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/photo1.jpg"])
        assert inp.inspiration_photo_urls == []
        assert inp.inspiration_notes == []

    def test_input_full(self):
        """All fields populated."""
        inp = AnalyzeRoomPhotosInput(
            room_photo_urls=["https://r2.example.com/p1.jpg", "https://r2.example.com/p2.jpg"],
            inspiration_photo_urls=["https://r2.example.com/inspo.jpg"],
            inspiration_notes=[InspirationNote(photo_index=0, note="love the shelving")],
        )
        assert len(inp.room_photo_urls) == 2
        assert len(inp.inspiration_notes) == 1

    def test_output_valid(self):
        """Output wraps a RoomAnalysis."""
        out = AnalyzeRoomPhotosOutput(analysis=RoomAnalysis(room_type="living room", photo_count=2))
        assert out.analysis.room_type == "living room"

    def test_round_trip(self):
        """I/O round-trip."""
        inp = AnalyzeRoomPhotosInput(
            room_photo_urls=["url1"],
            inspiration_notes=[InspirationNote(photo_index=0, note="warm tones")],
        )
        restored = AnalyzeRoomPhotosInput.model_validate_json(inp.model_dump_json())
        assert restored == inp


class TestDesignBriefEnhancement:
    """Additive Designer Brain fields on DesignBrief."""

    def test_backward_compat(self):
        """Old JSON without new fields deserializes correctly."""
        old_json = '{"room_type": "living room"}'
        brief = DesignBrief.model_validate_json(old_json)
        assert brief.emotional_drivers == []
        assert brief.usage_patterns is None
        assert brief.renovation_willingness is None
        assert brief.room_analysis_hypothesis is None

    def test_new_fields_populate(self):
        """New fields can be set."""
        brief = DesignBrief(
            room_type="office",
            emotional_drivers=["started WFH", "room feels oppressive"],
            usage_patterns="couple WFH Mon-Fri, host dinners monthly",
            renovation_willingness="repaint yes, replace flooring no",
            room_analysis_hypothesis="Cramped WFH setup in spare bedroom",
        )
        assert len(brief.emotional_drivers) == 2
        assert "WFH" in brief.usage_patterns

    def test_round_trip_with_new_fields(self):
        """Round-trip preserves all fields including new ones."""
        brief = DesignBrief(
            room_type="bedroom",
            emotional_drivers=["new baby"],
            usage_patterns="nursery + guest room",
        )
        restored = DesignBrief.model_validate_json(brief.model_dump_json())
        assert restored == brief
        assert restored.emotional_drivers == ["new baby"]


class TestWorkflowStateEnhancement:
    """Additive Designer Brain fields on WorkflowState."""

    def test_backward_compat(self):
        """Old JSON without room_analysis/room_context deserializes."""
        old_json = '{"step": "photos"}'
        state = WorkflowState.model_validate_json(old_json)
        assert state.room_analysis is None
        assert state.room_context is None

    def test_with_room_analysis(self):
        """WorkflowState carries room analysis."""
        state = WorkflowState(
            step="intake",
            room_analysis=RoomAnalysis(
                room_type="living room",
                hypothesis="Bright open plan with mixed warmth",
            ),
        )
        assert state.room_analysis.room_type == "living room"

    def test_with_room_context(self):
        """WorkflowState carries full room context."""
        state = WorkflowState(
            step="intake",
            room_context=RoomContext(
                photo_analysis=RoomAnalysis(room_type="kitchen"),
                room_dimensions=RoomDimensions(width_m=3.0, length_m=4.0, height_m=2.5),
                enrichment_sources=["photos", "lidar"],
            ),
        )
        assert state.room_context.enrichment_sources == ["photos", "lidar"]

    def test_round_trip(self):
        """Full round-trip with nested room analysis."""
        state = WorkflowState(
            step="scan",
            room_analysis=RoomAnalysis(
                room_type="office",
                furniture=[FurnitureObservation(item="desk")],
            ),
            room_context=RoomContext(enrichment_sources=["photos"]),
        )
        restored = WorkflowState.model_validate_json(state.model_dump_json())
        assert restored == state
        assert restored.room_analysis.furniture[0].item == "desk"


class TestMockAnalyzeRoomPhotos:
    """Mock stub returns valid AnalyzeRoomPhotosOutput."""

    @pytest.mark.asyncio
    async def test_returns_valid_output(self):
        """Mock returns well-formed RoomAnalysis."""
        inp = AnalyzeRoomPhotosInput(
            room_photo_urls=["https://r2.example.com/p1.jpg", "https://r2.example.com/p2.jpg"]
        )
        out = await mock_analyze_room_photos(inp)
        assert isinstance(out, AnalyzeRoomPhotosOutput)
        assert out.analysis.room_type == "living room"
        assert out.analysis.room_type_confidence == 0.85
        assert out.analysis.photo_count == 2

    @pytest.mark.asyncio
    async def test_photo_count_matches_input(self):
        """Mock photo_count reflects actual input length."""
        inp = AnalyzeRoomPhotosInput(room_photo_urls=["url1", "url2", "url3"])
        out = await mock_analyze_room_photos(inp)
        assert out.analysis.photo_count == 3

    @pytest.mark.asyncio
    async def test_nested_models_valid(self):
        """All nested models in mock output are well-formed."""
        inp = AnalyzeRoomPhotosInput(room_photo_urls=["url1"])
        out = await mock_analyze_room_photos(inp)
        assert out.analysis.lighting is not None
        assert out.analysis.lighting.natural_light_intensity == "abundant"
        assert len(out.analysis.furniture) == 2
        assert out.analysis.furniture[0].keep_candidate is True
        assert len(out.analysis.behavioral_signals) == 1

    @pytest.mark.asyncio
    async def test_round_trip_serialization(self):
        """Mock output survives JSON round-trip (Temporal serialization)."""
        inp = AnalyzeRoomPhotosInput(room_photo_urls=["url1", "url2"])
        out = await mock_analyze_room_photos(inp)
        restored = AnalyzeRoomPhotosOutput.model_validate_json(out.model_dump_json())
        assert restored == out
