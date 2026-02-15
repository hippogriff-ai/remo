"""Tests for edit_design activity.

Contract validation and unit tests (no API keys needed).
Integration tests marked with @pytest.mark.integration.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.models.contracts import (
    AnnotationRegion,
    EditDesignInput,
    EditDesignOutput,
)


class TestEditDesignContract:
    """Verify activity inputs/outputs match T0's contracts."""

    def test_input_minimal_annotation(self):
        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace sofa with a leather armchair",
                ),
            ],
        )
        assert inp.chat_history_key is None
        assert len(inp.annotations) == 1

    def test_input_minimal_feedback(self):
        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Make the room warmer with earth tones",
        )
        assert inp.feedback is not None
        assert inp.annotations == []

    def test_input_with_history_key(self):
        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/revision_1.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more plants",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )
        assert inp.chat_history_key is not None

    def test_output_model(self):
        output = EditDesignOutput(
            revised_image_url="https://r2.example.com/revision.png",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )
        assert output.revised_image_url.startswith("https://")
        assert "gemini_chat_history" in output.chat_history_key

    def test_annotation_region_constraints(self):
        # Valid region
        region = AnnotationRegion(
            region_id=1,
            center_x=0.5,
            center_y=0.5,
            radius=0.1,
            instruction="Change this to marble",
        )
        assert region.region_id == 1

    def test_annotation_region_id_range(self):
        with pytest.raises(ValueError):
            AnnotationRegion(
                region_id=0,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Invalid region",
            )

        with pytest.raises(ValueError):
            AnnotationRegion(
                region_id=4,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Invalid region",
            )

    def test_annotation_coordinate_range(self):
        with pytest.raises(ValueError):
            AnnotationRegion(
                region_id=1,
                center_x=1.5,  # out of range
                center_y=0.5,
                radius=0.1,
                instruction="Out of bounds",
            )

    def test_annotation_instruction_min_length(self):
        with pytest.raises(ValueError):
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="short",  # < 10 chars
            )

    def test_input_with_both_annotations_and_feedback(self):
        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace sofa with leather chair",
                ),
            ],
            feedback="Make the room feel cozier overall",
        )
        assert len(inp.annotations) == 1
        assert inp.feedback is not None

    def test_multiple_annotations(self):
        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.3,
                    center_y=0.4,
                    radius=0.1,
                    instruction="Replace sofa with a leather armchair",
                ),
                AnnotationRegion(
                    region_id=2,
                    center_x=0.7,
                    center_y=0.3,
                    radius=0.08,
                    instruction="Change lamp to a modern pendant light",
                ),
                AnnotationRegion(
                    region_id=3,
                    center_x=0.5,
                    center_y=0.8,
                    radius=0.12,
                    instruction="Replace rug with a jute natural fiber rug",
                ),
            ],
        )
        assert len(inp.annotations) == 3


class TestEditInstructions:
    """Test edit instruction building."""

    def test_build_instructions_single(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Replace with oak table",
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "Region 1:" in result
        assert "50% from left" in result
        assert "50% from top" in result
        assert "oak table" in result

    def test_build_instructions_multiple(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.3,
                center_y=0.5,
                radius=0.1,
                instruction="Replace sofa with sectional",
            ),
            AnnotationRegion(
                region_id=2,
                center_x=0.7,
                center_y=0.5,
                radius=0.1,
                instruction="Add floor-to-ceiling bookshelf",
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "Region 1:" in result
        assert "Region 2:" in result
        assert "left side" in result  # 30% → left
        assert "right side" in result  # 70% → right
        assert "sectional" in result
        assert "bookshelf" in result

    def test_build_instructions_with_action(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Replace with modern pendant light",
                action="Replace",
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "ACTION: Replace" in result
        assert "modern pendant light" in result

    def test_build_instructions_with_avoid(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Change the finish on this cabinet",
                avoid=["chrome", "brass"],
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "AVOID: chrome, brass" in result

    def test_build_instructions_with_constraints(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Resize this bookshelf to be taller",
                constraints=["budget-friendly", "kid-friendly"],
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "CONSTRAINTS: budget-friendly, kid-friendly" in result

    def test_build_instructions_with_all_fields(self):
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=2,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Remove this old armchair entirely",
                action="Remove",
                avoid=["leaving empty space"],
                constraints=["minimal style"],
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "Region 2:" in result
        assert "center of the image" in result
        assert "ACTION: Remove" in result
        assert "old armchair" in result
        assert "AVOID: leaving empty space" in result
        assert "CONSTRAINTS: minimal style" in result

    def test_build_instructions_without_optional_fields(self):
        """Instruction-only annotations should not include Action/Avoid/Constraints labels."""
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Make this wall a lighter color",
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "ACTION:" not in result
        assert "AVOID:" not in result
        assert "CONSTRAINTS:" not in result
        assert "lighter color" in result

    def test_build_instructions_empty_avoid_and_constraints(self):
        """Explicit empty arrays should suppress Avoid/Constraints labels."""
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Replace lamp",
                avoid=[],
                constraints=[],
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "AVOID:" not in result
        assert "CONSTRAINTS:" not in result

    def test_build_instructions_all_three_regions(self):
        """Verify all three region IDs get coordinate descriptions."""
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=i,
                center_x=0.3 * i,
                center_y=0.5,
                radius=0.1,
                instruction=f"Edit region {i} with detail",
            )
            for i in range(1, 4)
        ]
        result = _build_edit_instructions(annotations)
        assert "Region 1:" in result
        assert "Region 2:" in result
        assert "Region 3:" in result
        # Verify position descriptions differ based on coordinates
        assert "left side" in result   # 0.3 → left
        assert "center" in result      # 0.6 → center
        assert "right side" in result  # 0.9 → right

    def test_build_instructions_action_and_avoid_only(self):
        """Action + avoid without constraints should omit Constraints label."""
        from app.activities.edit import _build_edit_instructions

        annotations = [
            AnnotationRegion(
                region_id=1,
                center_x=0.5,
                center_y=0.5,
                radius=0.1,
                instruction="Remove shelf",
                action="Remove",
                avoid=["leaving marks"],
            ),
        ]
        result = _build_edit_instructions(annotations)
        assert "ACTION: Remove" in result
        assert "AVOID: leaving marks" in result
        assert "CONSTRAINTS:" not in result


class TestPositionDescription:
    """Test coordinate-to-text position descriptions."""

    def test_center(self):
        from app.activities.edit import _position_description

        result = _position_description(0.5, 0.5, 0.1)
        assert "center of the image" in result

    def test_upper_left(self):
        from app.activities.edit import _position_description

        result = _position_description(0.1, 0.1, 0.1)
        assert "upper-left" in result

    def test_lower_right(self):
        from app.activities.edit import _position_description

        result = _position_description(0.8, 0.8, 0.1)
        assert "lower-right" in result

    def test_percentage_coordinates(self):
        from app.activities.edit import _position_description

        result = _position_description(0.3, 0.7, 0.1)
        assert "30% from left" in result
        assert "70% from top" in result

    def test_large_area(self):
        from app.activities.edit import _position_description

        result = _position_description(0.5, 0.5, 0.3)
        assert "large area" in result

    def test_medium_area(self):
        from app.activities.edit import _position_description

        result = _position_description(0.5, 0.5, 0.15)
        assert "medium area" in result

    def test_small_area(self):
        from app.activities.edit import _position_description

        result = _position_description(0.5, 0.5, 0.05)
        assert "small area" in result


def _make_test_image(w: int = 100, h: int = 100) -> Image.Image:
    return Image.new("RGB", (w, h), "white")


def _image_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_gemini_response(with_image: bool = True) -> MagicMock:
    """Create a mock Gemini response with optional image."""
    response = MagicMock()
    if with_image:
        mock_genai_img = MagicMock()
        mock_genai_img.image_bytes = _image_bytes(_make_test_image())
        mock_part = MagicMock()
        mock_part.as_image.return_value = mock_genai_img
        mock_part.text = "Here is your redesigned room."
    else:
        mock_part = MagicMock()
        mock_part.as_image.return_value = None
        mock_part.text = "Cannot generate image."
    mock_content = MagicMock()
    mock_content.parts = [mock_part]
    mock_content.role = "model"
    mock_candidate = MagicMock()
    mock_candidate.content = mock_content
    response.candidates = [mock_candidate]
    return response


class TestEditDesignActivity:
    """Tests for edit_design activity with mocked external calls."""

    @pytest.mark.asyncio
    async def test_rejects_no_annotations_or_feedback(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
        )

        with pytest.raises(ApplicationError, match="Either annotations or feedback"):
            await edit_design(inp)

    @pytest.mark.asyncio
    async def test_bootstrap_with_annotations(self):
        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace sofa with leather chair",
                ),
            ],
        )

        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _mock_gemini_response(with_image=True)
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            assert "gemini_chat_history" in result.chat_history_key

    @pytest.mark.asyncio
    async def test_bootstrap_with_feedback(self):
        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Make the room warmer with earth tones",
        )

        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _mock_gemini_response(with_image=True)
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"

    @pytest.mark.asyncio
    async def test_continuation_with_history(self):
        from google.genai import types

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more plants",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="hello")]),
            types.Content(role="model", parts=[types.Part(text="hi")]),
        ]

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit.continue_chat",
                return_value=_mock_gemini_response(with_image=True),
            ),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_contents_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"

    @pytest.mark.asyncio
    async def test_rate_limit_error_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more plants",
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                side_effect=Exception("429 RESOURCE_EXHAUSTED"),
            ),
        ):
            with pytest.raises(ApplicationError, match="rate limited") as exc_info:
                await edit_design(inp)
            assert not exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_safety_error_non_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Something blocked",
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                side_effect=Exception("SAFETY block triggered"),
            ),
        ):
            with pytest.raises(ApplicationError, match="Content policy") as exc_info:
                await edit_design(inp)
            assert exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_thought_signature_error_non_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more plants",
        )

        with patch(
            "app.activities.edit.download_image",
            new_callable=AsyncMock,
            side_effect=Exception("400 thought signature validation failed"),
        ):
            with pytest.raises(ApplicationError, match="corrupted") as exc_info:
                await edit_design(inp)
            assert exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_generic_error_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more plants",
        )

        with patch(
            "app.activities.edit.download_image",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unexpected network glitch"),
        ):
            with pytest.raises(ApplicationError, match="Edit failed") as exc_info:
                await edit_design(inp)
            assert not exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_bootstrap_with_both_annotations_and_feedback(self):
        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace sofa with leather chair",
                ),
            ],
            feedback="Make the room feel cozier overall",
        )

        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _mock_gemini_response(with_image=True)
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            # send_message called twice: context + edit (with both annotations and feedback)
            assert mock_chat.send_message.call_count == 2
            # Verify the edit call included "Additional feedback"
            edit_call_args = mock_chat.send_message.call_args_list[1][0][0]
            feedback_parts = [
                p for p in edit_call_args if isinstance(p, str) and "Additional feedback" in p
            ]
            assert len(feedback_parts) == 1

    @pytest.mark.asyncio
    async def test_bootstrap_retry_on_text_only_response(self):
        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Make the room brighter",
        )

        mock_chat = MagicMock()
        # First send_message = context (ignored), second = text-only, third = retry with image
        mock_chat.send_message.side_effect = [
            _mock_gemini_response(with_image=True),  # context turn
            _mock_gemini_response(with_image=False),  # edit turn: text-only
            _mock_gemini_response(with_image=True),  # retry: success
        ]
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            # 3 send_message calls: context + edit + retry
            assert mock_chat.send_message.call_count == 3
            # Verify retry message requests image generation
            retry_msg = mock_chat.send_message.call_args_list[2][0][0]
            assert "generate" in retry_msg.lower()
            assert "annotation" in retry_msg.lower()

    @pytest.mark.asyncio
    async def test_bootstrap_fails_when_no_image_after_retry(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Make the room brighter",
        )

        mock_chat = MagicMock()
        # Context succeeds, edit returns text-only, retry returns text-only
        mock_chat.send_message.side_effect = [
            _mock_gemini_response(with_image=True),  # context
            _mock_gemini_response(with_image=False),  # edit: text-only
            _mock_gemini_response(with_image=False),  # retry: still text-only
        ]
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            pytest.raises(ApplicationError, match="failed to generate"),
        ):
            await edit_design(inp)

    @pytest.mark.asyncio
    async def test_continuation_with_annotations(self):
        """Test continue_chat path with annotations (exercises image serialization)."""
        from google.genai import types

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace the lamp with a pendant light",
                ),
            ],
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="context")]),
            types.Content(role="model", parts=[types.Part(text="ok")]),
        ]

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit.continue_chat",
                return_value=_mock_gemini_response(with_image=True),
            ),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_contents_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ) as mock_serialize,
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            # Verify the serialized history was extended
            call_args = mock_serialize.call_args[0]
            updated_history = call_args[0]
            # Original 2 turns + user turn + model turn = 4
            assert len(updated_history) == 4
            # Verify structure: roles alternate user/model
            assert updated_history[0].role == "user"
            assert updated_history[1].role == "model"
            assert updated_history[2].role == "user"
            assert updated_history[3].role == "model"
            # The user turn should have image + text parts (annotation)
            assert len(updated_history[2].parts) >= 2

    @pytest.mark.asyncio
    async def test_continuation_with_both_annotations_and_feedback(self):
        """Test continue_chat with both annotations and feedback."""
        from google.genai import types

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            annotations=[
                AnnotationRegion(
                    region_id=1,
                    center_x=0.5,
                    center_y=0.5,
                    radius=0.1,
                    instruction="Replace the lamp with a pendant light",
                ),
            ],
            feedback="Make overall lighting warmer",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="context")]),
            types.Content(role="model", parts=[types.Part(text="ok")]),
        ]

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit.continue_chat",
                return_value=_mock_gemini_response(with_image=True),
            ),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_contents_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"

    @pytest.mark.asyncio
    async def test_continuation_retry_on_text_only(self):
        """Test continue_chat retry path when first response is text-only."""
        from google.genai import types

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add a plant in the corner",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="context")]),
            types.Content(role="model", parts=[types.Part(text="ok")]),
        ]

        # First call returns text-only, second (retry) returns image
        mock_continue = MagicMock(
            side_effect=[
                _mock_gemini_response(with_image=False),
                _mock_gemini_response(with_image=True),
            ]
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            patch("app.activities.edit.continue_chat", mock_continue),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_contents_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ) as mock_serialize,
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            # continue_chat called twice (original + retry)
            assert mock_continue.call_count == 2
            # Verify retry message content
            retry_call_parts = mock_continue.call_args_list[1][0][1]
            assert any("annotation" in str(p).lower() for p in retry_call_parts)
            # History: 2 original + user + model + retry user + retry model = 6
            call_args = mock_serialize.call_args[0]
            updated_history = call_args[0]
            assert len(updated_history) == 6

    @pytest.mark.asyncio
    async def test_continuation_fails_when_no_image_after_retry(self):
        """Test that continuation raises when retry also returns text-only."""
        from google.genai import types
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add a plant in the corner",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="context")]),
            types.Content(role="model", parts=[types.Part(text="ok")]),
        ]

        # Both calls return text-only
        mock_continue = MagicMock(
            side_effect=[
                _mock_gemini_response(with_image=False),
                _mock_gemini_response(with_image=False),
            ]
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            patch("app.activities.edit.continue_chat", mock_continue),
            pytest.raises(ApplicationError, match="continuation"),
        ):
            await edit_design(inp)

    @pytest.mark.asyncio
    async def test_application_error_passthrough(self):
        """ApplicationError from inner code should re-raise directly."""
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add plants",
        )

        with patch(
            "app.activities.edit.download_image",
            new_callable=AsyncMock,
            side_effect=ApplicationError("custom error", non_retryable=True),
        ):
            with pytest.raises(ApplicationError, match="custom error") as exc_info:
                await edit_design(inp)
            assert exc_info.value.non_retryable


class TestDownloadImageEdit:
    """Tests for download_image (shared HTTP helper)."""

    @pytest.mark.asyncio
    async def test_download_success(self):
        from app.utils.http import download_image

        img_bytes = _image_bytes(_make_test_image())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.headers = {"content-type": "image/png"}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            result = await download_image("https://example.com/test.png")
            assert result.size == (100, 100)

    @pytest.mark.asyncio
    async def test_download_404_non_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError) as exc_info:
                await download_image("https://example.com/missing.png")
            assert exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_download_500_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError) as exc_info:
                await download_image("https://example.com/error.png")
            assert not exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_download_wrong_content_type(self):
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<html>not an image</html>"
        mock_response.headers = {"content-type": "text/html"}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError, match="Expected image"):
                await download_image("https://example.com/page.html")

    @pytest.mark.asyncio
    async def test_download_timeout_retryable(self):
        import httpx
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.TimeoutException("timed out")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError) as exc_info:
                await download_image("https://example.com/slow.png")
            assert not exc_info.value.non_retryable


class TestDownloadImages:
    """Tests for download_images concurrent helper."""

    @pytest.mark.asyncio
    async def test_downloads_multiple(self):
        from app.utils.http import download_images

        img_bytes = _image_bytes(_make_test_image())
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = img_bytes
        mock_response.headers = {"content-type": "image/png"}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            results = await download_images(["https://a.com/1.png", "https://a.com/2.png"])
            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_downloads_empty_list(self):
        from app.utils.http import download_images

        results = await download_images([])
        assert results == []


class TestUploadImage:
    """Tests for _upload_image."""

    def test_upload_image_returns_storage_key(self):
        """_upload_image returns an R2 key (not presigned URL) for stable storage."""
        from app.activities.edit import _upload_image

        img = _make_test_image()

        with patch("app.utils.r2.upload_object") as mock_upload:
            key = _upload_image(img, "proj-123")
            # Returns storage key, not a presigned URL
            assert key.startswith("projects/proj-123/revisions/")
            assert key.endswith(".png")
            assert not key.startswith("http")
            mock_upload.assert_called_once()
            call_args = mock_upload.call_args[0]
            assert call_args[0] == key


class TestBootstrapWithInspirationImages:
    """Test bootstrap path with inspiration images to cover line 152."""

    @pytest.mark.asyncio
    async def test_bootstrap_includes_inspiration_images_in_context(self):
        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            inspiration_photo_urls=["https://r2.example.com/inspo.jpg"],
            feedback="Make it brighter",
        )

        mock_chat = MagicMock()
        mock_chat.send_message.return_value = _mock_gemini_response(with_image=True)
        mock_chat.get_history.return_value = []

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.edit.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], [_make_test_image(50, 50)]],
            ),
            patch("app.activities.edit.create_chat", return_value=mock_chat),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit._upload_image",
                return_value="https://r2.example.com/rev.png",
            ),
            patch(
                "app.activities.edit.serialize_to_r2",
                return_value="projects/proj-123/gemini_chat_history.json",
            ),
        ):
            result = await edit_design(inp)
            assert result.revised_image_url == "https://r2.example.com/rev.png"
            # Context message should include room + inspiration + base + prompt = 4 items
            context_call_args = mock_chat.send_message.call_args_list[0][0][0]
            assert len(context_call_args) == 4


class TestContinueChatDirectly:
    """Test _continue_chat directly to cover the defensive guard at line 222."""

    @pytest.mark.asyncio
    async def test_raises_when_no_annotations_or_feedback(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import _continue_chat

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
        )
        base_image = _make_test_image()

        from google.genai import types

        mock_history = [
            types.Content(role="user", parts=[types.Part(text="ctx")]),
            types.Content(role="model", parts=[types.Part(text="ok")]),
        ]

        with (
            patch("app.activities.edit.restore_from_r2", return_value=mock_history),
            patch("app.activities.edit.get_client"),
            pytest.raises(ApplicationError, match="No annotations or feedback"),
        ):
            await _continue_chat(inp, base_image)


class TestCorruptR2History:
    """Gap 1: Corrupt chat history JSON from R2 during continuation edit."""

    @pytest.mark.asyncio
    async def test_corrupt_r2_json_raises_non_retryable(self):
        """When R2 returns invalid JSON for chat history, the edit activity
        should raise a non-retryable ApplicationError instead of crashing
        or retrying infinitely with the same corrupt data."""
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Make the room brighter and more modern",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit.restore_from_r2",
                side_effect=ApplicationError(
                    "Chat history corrupted: invalid JSON",
                    non_retryable=True,
                ),
            ),
        ):
            with pytest.raises(ApplicationError, match="corrupted") as exc_info:
                await edit_design(inp)
            assert exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_deserialization_failure_raises_non_retryable(self):
        """When R2 returns valid JSON but with invalid data shape (e.g.
        missing Content fields), restore_from_r2 raises non-retryable."""
        from temporalio.exceptions import ApplicationError

        from app.activities.edit import edit_design

        inp = EditDesignInput(
            project_id="proj-123",
            base_image_url="https://r2.example.com/design.png",
            room_photo_urls=["https://r2.example.com/room.jpg"],
            feedback="Add more warm lighting throughout",
            chat_history_key="projects/proj-123/gemini_chat_history.json",
        )

        with (
            patch(
                "app.activities.edit.download_image",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch("app.activities.edit.get_client"),
            patch(
                "app.activities.edit.restore_from_r2",
                side_effect=ApplicationError(
                    "Chat history deserialization failed",
                    non_retryable=True,
                ),
            ),
        ):
            with pytest.raises(ApplicationError, match="deserialization") as exc_info:
                await edit_design(inp)
            assert exc_info.value.non_retryable
