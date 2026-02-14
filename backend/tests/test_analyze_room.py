"""Tests for the analyze_room activity (read_the_room skill).

Unit tests (no API key needed) cover:
- System prompt loading
- Message building from photo URLs
- Tool call extraction from Claude responses
- RoomAnalysis construction from tool data
- Error handling (missing API key, no photos, no tool call)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest
from temporalio.exceptions import ApplicationError

from app.activities.analyze_room import (
    ANALYZE_ROOM_TOOL,
    build_messages,
    build_room_analysis,
    extract_analysis,
    load_prompt,
)
from app.models.contracts import (
    AnalyzeRoomPhotosInput,
    InspirationNote,
)


class TestLoadPrompt:
    def test_loads_prompt_file(self):
        prompt = load_prompt()
        assert "observational protocol" in prompt.lower()
        assert "read the light" in prompt.lower()

    def test_prompt_cached(self):
        """Second call returns same object (cached)."""
        p1 = load_prompt()
        p2 = load_prompt()
        assert p1 is p2


class TestBuildMessages:
    def test_room_photos_as_image_blocks(self):
        input = AnalyzeRoomPhotosInput(
            room_photo_urls=["https://r2.example.com/room1.jpg", "https://r2.example.com/room2.jpg"]
        )
        messages = build_messages(input)
        assert len(messages) == 1
        content = messages[0]["content"]
        # 2 image blocks + 1 text instruction
        images = [b for b in content if b["type"] == "image"]
        assert len(images) == 2
        assert images[0]["source"]["url"] == "https://r2.example.com/room1.jpg"

    def test_inspiration_photos_with_notes(self):
        input = AnalyzeRoomPhotosInput(
            room_photo_urls=["https://r2.example.com/room.jpg"],
            inspiration_photo_urls=["https://r2.example.com/inspo.jpg"],
            inspiration_notes=[InspirationNote(photo_index=0, note="love the blue")],
        )
        messages = build_messages(input)
        content = messages[0]["content"]
        texts = [b["text"] for b in content if b["type"] == "text"]
        assert any("inspiration" in t.lower() for t in texts)
        assert any("love the blue" in t for t in texts)

    def test_no_inspiration_photos(self):
        input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
        messages = build_messages(input)
        content = messages[0]["content"]
        texts = [b.get("text", "") for b in content if b["type"] == "text"]
        assert not any("inspiration" in t.lower() for t in texts)

    def test_instruction_text_present(self):
        input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
        messages = build_messages(input)
        content = messages[0]["content"]
        texts = [b["text"] for b in content if b["type"] == "text"]
        assert any("analyze" in t.lower() for t in texts)


class TestExtractAnalysis:
    def test_extracts_tool_call(self):
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "analyze_room"
        mock_block.input = {"room_type": "bedroom", "hypothesis": "test"}

        response = MagicMock()
        response.content = [mock_block]

        data = extract_analysis(response)
        assert data["room_type"] == "bedroom"
        assert data["hypothesis"] == "test"

    def test_no_tool_call_returns_empty(self):
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "Just some text"

        response = MagicMock()
        response.content = [mock_block]

        data = extract_analysis(response)
        assert data == {}

    def test_wrong_tool_name_returns_empty(self):
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "interview_client"
        mock_block.input = {"question": "test"}

        response = MagicMock()
        response.content = [mock_block]

        data = extract_analysis(response)
        assert data == {}


class TestBuildRoomAnalysis:
    def test_full_data(self):
        data = {
            "room_type": "living room",
            "room_type_confidence": 0.9,
            "estimated_dimensions": "12x15 feet",
            "layout_pattern": "open plan",
            "lighting": {
                "natural_light_direction": "south-facing",
                "natural_light_intensity": "abundant",
                "window_coverage": "full wall",
                "existing_artificial": "layered",
                "lighting_gaps": ["dark corner"],
            },
            "furniture": [
                {"item": "gray sofa", "condition": "good", "keep_candidate": True},
                {"item": "coffee table", "condition": "worn"},
            ],
            "architectural_features": ["crown molding"],
            "flooring": "hardwood",
            "existing_palette": ["cool gray", "warm oak"],
            "overall_warmth": "mixed",
            "circulation_issues": ["blocked path"],
            "style_signals": ["mid-century"],
            "behavioral_signals": [
                {
                    "observation": "books on floor",
                    "inference": "needs shelving",
                    "design_implication": "add bookcase",
                }
            ],
            "tensions": ["quality mismatch"],
            "hypothesis": "Family room with good bones",
            "strengths": ["natural light"],
            "opportunities": ["better storage"],
            "uncertain_aspects": ["ceiling height"],
        }

        analysis = build_room_analysis(data, photo_count=3)

        assert analysis.room_type == "living room"
        assert analysis.room_type_confidence == 0.9
        assert analysis.lighting is not None
        assert analysis.lighting.natural_light_direction == "south-facing"
        assert analysis.lighting.lighting_gaps == ["dark corner"]
        assert len(analysis.furniture) == 2
        assert analysis.furniture[0].keep_candidate is True
        assert len(analysis.behavioral_signals) == 1
        assert analysis.behavioral_signals[0].design_implication == "add bookcase"
        assert analysis.hypothesis == "Family room with good bones"
        assert analysis.photo_count == 3

    def test_minimal_data(self):
        """Handles minimal/missing fields gracefully."""
        data = {"room_type": "bedroom"}
        analysis = build_room_analysis(data, photo_count=2)
        assert analysis.room_type == "bedroom"
        assert analysis.lighting is None
        assert analysis.furniture == []
        assert analysis.behavioral_signals == []
        assert analysis.photo_count == 2

    def test_empty_data(self):
        """Empty dict produces valid RoomAnalysis with defaults."""
        analysis = build_room_analysis({}, photo_count=1)
        assert analysis.room_type is None
        assert analysis.room_type_confidence == 0.5
        assert analysis.photo_count == 1

    def test_null_lists_treated_as_empty(self):
        """Claude returning null for list fields should not raise TypeError."""
        data = {
            "room_type": "kitchen",
            "furniture": None,
            "behavioral_signals": None,
            "lighting": {
                "natural_light_direction": "north",
                "lighting_gaps": None,
            },
        }
        analysis = build_room_analysis(data, photo_count=1)
        assert analysis.room_type == "kitchen"
        assert analysis.furniture == []
        assert analysis.behavioral_signals == []
        assert analysis.lighting is not None
        assert analysis.lighting.lighting_gaps == []

    def test_invalid_furniture_skipped_with_warning(self, caplog):
        """Malformed furniture entries are skipped with a warning log."""
        data = {
            "furniture": [
                {"item": "good sofa"},
                {"no_item_key": "bad"},  # missing required "item"
                "not a dict",
            ]
        }
        analysis = build_room_analysis(data, photo_count=1)
        assert len(analysis.furniture) == 1
        assert analysis.furniture[0].item == "good sofa"

    def test_invalid_behavioral_signal_skipped_with_warning(self, caplog):
        """Malformed behavioral signals are skipped with a warning log."""
        data = {
            "behavioral_signals": [
                {"observation": "books", "inference": "reader"},
                {"observation": "only observation"},  # missing inference
                "string not dict",
            ]
        }
        analysis = build_room_analysis(data, photo_count=1)
        assert len(analysis.behavioral_signals) == 1


class TestAnalyzeRoomTool:
    def test_tool_schema_has_required_fields(self):
        """Tool schema covers all key RoomAnalysis fields."""
        props = ANALYZE_ROOM_TOOL["input_schema"]["properties"]
        assert "room_type" in props
        assert "hypothesis" in props
        assert "lighting" in props
        assert "furniture" in props
        assert "behavioral_signals" in props
        assert "uncertain_aspects" in props

    def test_tool_name(self):
        assert ANALYZE_ROOM_TOOL["name"] == "analyze_room"


class TestAnalyzeRoomPhotosActivity:
    """Test the activity function's error handling and API call wiring."""

    @pytest.mark.asyncio
    async def test_missing_api_key_raises(self):
        input = AnalyzeRoomPhotosInput(room_photo_urls=["https://example.com/room.jpg"])
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ApplicationError, match="ANTHROPIC_API_KEY"),
        ):
            from app.activities.analyze_room import analyze_room_photos

            await analyze_room_photos(input)

    @pytest.mark.asyncio
    async def test_empty_photos_raises(self):
        input = AnalyzeRoomPhotosInput(room_photo_urls=[])
        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            pytest.raises(ApplicationError, match="No room photos"),
        ):
            from app.activities.analyze_room import analyze_room_photos

            await analyze_room_photos(input)

    @pytest.mark.asyncio
    async def test_successful_analysis(self):
        """Mock Claude API returns tool call → activity returns RoomAnalysis."""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "analyze_room"
        mock_block.input = {
            "room_type": "kitchen",
            "room_type_confidence": 0.95,
            "hypothesis": "Modern kitchen needing better lighting",
            "furniture": [{"item": "island counter", "condition": "good"}],
        }

        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
            result = await analyze_room_photos(input)

        assert result.analysis.room_type == "kitchen"
        assert result.analysis.room_type_confidence == 0.95
        assert result.analysis.hypothesis == "Modern kitchen needing better lighting"
        assert len(result.analysis.furniture) == 1
        assert result.analysis.photo_count == 1

    @pytest.mark.asyncio
    async def test_resolves_r2_keys_to_presigned_urls(self):
        """R2 storage keys should be resolved to presigned URLs before API call."""
        mock_block = MagicMock()
        mock_block.type = "tool_use"
        mock_block.name = "analyze_room"
        mock_block.input = {"room_type": "bedroom"}

        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        mock_resolve = MagicMock(
            side_effect=lambda urls: [f"https://presigned.r2/{u}" for u in urls]
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
            patch("app.utils.r2.resolve_urls", mock_resolve),
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(
                room_photo_urls=["projects/p1/photos/room.jpg"],
                inspiration_photo_urls=["projects/p1/photos/inspo.jpg"],
            )
            await analyze_room_photos(input)

        # resolve_urls should be called twice: once for room, once for inspiration
        assert mock_resolve.call_count == 2
        mock_resolve.assert_any_call(["projects/p1/photos/room.jpg"])
        mock_resolve.assert_any_call(["projects/p1/photos/inspo.jpg"])

        # The API call should receive resolved URLs, not raw keys
        call_args = mock_client.messages.create.call_args
        messages = call_args.kwargs["messages"]
        image_blocks = [b for b in messages[0]["content"] if b.get("type") == "image"]
        for img in image_blocks:
            assert img["source"]["url"].startswith("https://presigned.r2/")

    @pytest.mark.asyncio
    async def test_no_tool_call_raises(self):
        """Claude returns text instead of tool call → activity raises retryable error."""
        mock_block = MagicMock()
        mock_block.type = "text"
        mock_block.text = "I see a nice room."

        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
            pytest.raises(ApplicationError, match="tool call"),
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
            await analyze_room_photos(input)

    @pytest.mark.asyncio
    async def test_rate_limit_error_retryable(self):
        """RateLimitError should raise retryable ApplicationError."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.RateLimitError(
                message="rate limited",
                response=MagicMock(status_code=429, headers={}),
                body=None,
            )
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
            pytest.raises(ApplicationError, match="rate limited") as exc_info,
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
            await analyze_room_photos(input)

        assert exc_info.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_api_status_error_retryable(self):
        """APIStatusError (503) should raise retryable ApplicationError."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.APIStatusError(
                message="server error",
                response=MagicMock(status_code=503, headers={}),
                body=None,
            )
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
            pytest.raises(ApplicationError, match="API error") as exc_info,
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
            await analyze_room_photos(input)

        assert exc_info.value.non_retryable is False

    @pytest.mark.asyncio
    async def test_auth_error_non_retryable(self):
        """401 Unauthorized should raise non-retryable ApplicationError."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            side_effect=anthropic.AuthenticationError(
                message="Invalid API key",
                response=MagicMock(status_code=401, headers={}),
                body=None,
            )
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "bad-key"}),
            patch("app.activities.analyze_room.anthropic.AsyncAnthropic", return_value=mock_client),
            pytest.raises(ApplicationError, match="API error") as exc_info,
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(room_photo_urls=["https://r2.example.com/room.jpg"])
            await analyze_room_photos(input)

        assert exc_info.value.non_retryable is True

    @pytest.mark.asyncio
    async def test_r2_resolve_failure_propagates(self):
        """R2 URL resolution failure should propagate as an unhandled exception."""
        mock_resolve = MagicMock(side_effect=Exception("R2 service unavailable"))

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            patch("app.utils.r2.resolve_urls", mock_resolve),
            pytest.raises(Exception, match="R2 service unavailable"),
        ):
            from app.activities.analyze_room import analyze_room_photos

            input = AnalyzeRoomPhotosInput(
                room_photo_urls=["projects/p1/photos/room.jpg"],
            )
            await analyze_room_photos(input)
