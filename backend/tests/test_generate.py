"""Tests for generate_designs activity.

Contract validation and unit tests (no API keys needed).
Integration tests marked with @pytest.mark.integration.
"""

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from app.models.contracts import (
    DesignBrief,
    DesignOption,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    InspirationNote,
    StyleProfile,
)


class TestGenerateDesignsContract:
    """Verify activity inputs/outputs match T0's contracts."""

    def test_input_model_accepts_minimal(self):
        inp = GenerateDesignsInput(room_photo_urls=["https://example.com/room.jpg"])
        assert len(inp.room_photo_urls) == 1
        assert inp.inspiration_photo_urls == []
        assert inp.design_brief is None

    def test_input_model_accepts_full(self):
        inp = GenerateDesignsInput(
            room_photo_urls=["https://example.com/room1.jpg", "https://example.com/room2.jpg"],
            inspiration_photo_urls=["https://example.com/inspo1.jpg"],
            inspiration_notes=[InspirationNote(photo_index=0, note="Love the warm tones")],
            design_brief=DesignBrief(
                room_type="living room",
                style_profile=StyleProfile(mood="cozy", colors=["beige", "white"]),
                keep_items=["bookshelf"],
            ),
        )
        assert len(inp.room_photo_urls) == 2

    def test_output_requires_exactly_2_options(self):
        output = GenerateDesignsOutput(
            options=[
                DesignOption(image_url="https://r2.example.com/a.png", caption="A"),
                DesignOption(image_url="https://r2.example.com/b.png", caption="B"),
            ]
        )
        assert len(output.options) == 2

    def test_output_rejects_1_option(self):
        with pytest.raises(ValueError):
            GenerateDesignsOutput(options=[DesignOption(image_url="url", caption="A")])

    def test_output_rejects_3_options(self):
        with pytest.raises(ValueError):
            GenerateDesignsOutput(
                options=[
                    DesignOption(image_url="url", caption="A"),
                    DesignOption(image_url="url", caption="B"),
                    DesignOption(image_url="url", caption="C"),
                ]
            )


class TestProjectIdExtraction:
    """Tests for _extract_project_id helper."""

    def test_extracts_from_r2_storage_key(self):
        from app.activities.generate import _extract_project_id

        urls = ["projects/abc-123/room_photos/photo1.jpg"]
        assert _extract_project_id(urls) == "abc-123"

    def test_extracts_from_presigned_url(self):
        from app.activities.generate import _extract_project_id

        urls = ["https://r2.example.com/projects/proj-456/room_photos/photo.jpg?sig=abc"]
        assert _extract_project_id(urls) == "proj-456"

    def test_extracts_from_first_matching_url(self):
        from app.activities.generate import _extract_project_id

        urls = [
            "projects/first-id/room_photos/photo1.jpg",
            "projects/second-id/room_photos/photo2.jpg",
        ]
        assert _extract_project_id(urls) == "first-id"

    def test_raises_when_no_project_id_found(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import _extract_project_id

        with pytest.raises(ApplicationError, match="Could not extract project_id"):
            _extract_project_id(["https://example.com/random/image.jpg"])

    def test_raises_on_empty_urls(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import _extract_project_id

        with pytest.raises(ApplicationError, match="Could not extract project_id"):
            _extract_project_id([])


class TestPromptBuilding:
    """Tests for prompt construction logic."""

    def test_build_prompt_minimal(self):
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [])
        assert "interior design" in prompt.lower() or "redesign" in prompt.lower()

    def test_build_prompt_with_brief(self):
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(
            room_type="bedroom",
            style_profile=StyleProfile(
                mood="serene",
                colors=["lavender", "cream"],
                lighting="soft natural",
            ),
            keep_items=["wardrobe"],
            pain_points=["too dark"],
        )
        prompt = _build_generation_prompt(brief, [])
        assert "bedroom" in prompt
        assert "serene" in prompt
        assert "lavender" in prompt
        assert "wardrobe" in prompt
        assert "too dark" in prompt

    def test_build_prompt_with_inspiration(self):
        from app.activities.generate import _build_generation_prompt

        notes = [InspirationNote(photo_index=0, note="Love the wooden beams")]
        prompt = _build_generation_prompt(None, notes)
        assert "wooden beams" in prompt

    def test_build_prompt_with_all_style_fields(self):
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(
            room_type="kitchen",
            occupants="family of 4",
            style_profile=StyleProfile(
                mood="bright",
                colors=["white", "blue"],
                textures=["marble", "wood"],
                lighting="pendant fixtures",
                clutter_level="minimal",
            ),
            keep_items=["island"],
            pain_points=["poor lighting", "not enough storage"],
            constraints=["budget under $5000", "keep existing appliances"],
        )
        prompt = _build_generation_prompt(brief, [])
        assert "kitchen" in prompt
        assert "family of 4" in prompt
        assert "marble" in prompt
        assert "pendant fixtures" in prompt
        assert "minimal" in prompt
        assert "poor lighting" in prompt
        assert "budget under $5000" in prompt
        assert "island" in prompt

    def test_prompt_includes_preservation_clause(self):
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [])
        assert "camera angle" in prompt
        assert "room geometry" in prompt or "architecture" in prompt


class TestRoomContextFormatting:
    """Tests for _format_room_context and room_dimensions in prompt building."""

    def test_format_none_returns_empty(self):
        from app.activities.generate import _format_room_context

        assert _format_room_context(None) == ""

    def test_format_basic_dimensions(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(width_m=4.2, length_m=5.8, height_m=2.7)
        result = _format_room_context(dims)
        assert "4.2m" in result
        assert "5.8m" in result
        assert "2.7m" in result

    def test_format_includes_floor_area(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5, floor_area_sqm=20.0)
        result = _format_room_context(dims)
        assert "20.0 m²" in result

    def test_format_includes_openings(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[{"type": "door"}, {"type": "window"}],
        )
        result = _format_room_context(dims)
        assert "door" in result
        assert "window" in result

    def test_format_includes_furniture(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "sofa"}, {"type": "table"}],
        )
        result = _format_room_context(dims)
        assert "sofa" in result
        assert "table" in result

    def test_format_includes_surfaces(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            surfaces=[{"type": "floor", "material": "hardwood"}],
        )
        result = _format_room_context(dims)
        assert "hardwood" in result

    def test_format_empty_arrays_omit_sections(self):
        """Explicit empty arrays should not produce Openings/furniture/Surfaces lines."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[],
            furniture=[],
            surfaces=[],
        )
        result = _format_room_context(dims)
        assert "Openings:" not in result
        assert "furniture" not in result
        assert "Surfaces:" not in result
        assert "4.0m" in result

    def test_format_missing_keys_uses_fallback(self):
        """Dict entries without 'type' or 'material' use fallback values."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[{}],
            furniture=[{}],
            surfaces=[{"type": "floor"}],
        )
        result = _format_room_context(dims)
        assert "opening" in result
        assert "item" in result
        assert "unknown" in result

    def test_format_all_fields_populated(self):
        """All fields present should produce complete context block."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.2,
            length_m=5.8,
            height_m=2.7,
            floor_area_sqm=24.36,
            openings=[{"type": "door"}, {"type": "window"}, {"type": "window"}],
            furniture=[{"type": "sofa"}, {"type": "table"}],
            surfaces=[{"type": "floor", "material": "hardwood"}],
        )
        result = _format_room_context(dims)
        assert "4.2m" in result
        assert "24.4" in result or "24.3" in result
        assert "door" in result
        assert result.count("window") == 2
        assert "sofa" in result
        assert "hardwood" in result

    def test_non_dict_entries_filtered(self):
        """Non-dict entries in openings/furniture/surfaces are silently skipped.

        Uses model_construct to bypass Pydantic validation (simulates corrupt
        data from parse_room_dimensions or other non-validated paths).
        """
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions.model_construct(
            width_m=3.0,
            length_m=4.0,
            height_m=2.5,
            openings=[{"type": "door"}, "bad-string", 42],
            furniture=[None, {"type": "chair"}, True],
            surfaces=[{"type": "wall", "material": "paint"}, 3.14],
        )
        result = _format_room_context(dims)
        assert "door" in result
        assert "chair" in result
        assert "paint" in result
        assert "bad-string" not in result
        assert "42" not in result
        assert "3.14" not in result

    def test_build_prompt_with_dimensions(self):
        from app.activities.generate import _build_generation_prompt
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(width_m=4.2, length_m=5.8, height_m=2.7, floor_area_sqm=24.36)
        prompt = _build_generation_prompt(None, [], room_dimensions=dims)
        assert "4.2m" in prompt
        assert "5.8m" in prompt
        assert "24.4 m²" in prompt or "24.3 m²" in prompt or "24.4" in prompt

    def test_build_prompt_without_dimensions(self):
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [], room_dimensions=None)
        # Should not contain dimension text but should still be valid
        assert "redesign" in prompt.lower() or "interior design" in prompt.lower()


class TestPromptFiles:
    """Verify prompt template files exist and are valid."""

    def test_generation_prompt_exists(self):
        path = Path(__file__).parent.parent / "prompts" / "generation.txt"
        assert path.exists()
        content = path.read_text()
        assert "{brief}" in content
        assert "{room_context}" in content
        assert len(content) > 50

    def test_edit_prompt_exists(self):
        path = Path(__file__).parent.parent / "prompts" / "edit.txt"
        assert path.exists()
        content = path.read_text()
        assert "{edit_instructions}" in content

    def test_preservation_prompt_exists(self):
        path = Path(__file__).parent.parent / "prompts" / "room_preservation.txt"
        assert path.exists()
        content = path.read_text()
        assert "camera angle" in content


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
        mock_part.text = None
    else:
        mock_part = MagicMock()
        mock_part.as_image.return_value = None
        mock_part.text = "I cannot generate that image."
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]
    response.candidates = [mock_candidate]
    return response


class TestDownloadImage:
    """Tests for download_image with mocked httpx."""

    @pytest.mark.asyncio
    async def test_download_success(self):
        from app.utils.http import download_image

        img = _make_test_image()
        img_bytes = _image_bytes(img)

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


class TestGenerateSingleOption:
    """Tests for _generate_single_option with mocked Gemini."""

    @pytest.mark.asyncio
    async def test_returns_image_on_success(self):
        from app.activities.generate import _generate_single_option

        response = _mock_gemini_response(with_image=True)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = response

        with patch("app.activities.generate.get_client", return_value=mock_client):
            result = await _generate_single_option("test prompt", [_make_test_image()], [], 0)
            assert isinstance(result, Image.Image)

    @pytest.mark.asyncio
    async def test_retries_on_text_only_response(self):
        from app.activities.generate import _generate_single_option

        text_response = _mock_gemini_response(with_image=False)
        image_response = _mock_gemini_response(with_image=True)
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = [
            text_response,
            image_response,
        ]

        with patch("app.activities.generate.get_client", return_value=mock_client):
            result = await _generate_single_option("test prompt", [_make_test_image()], [], 0)
            assert isinstance(result, Image.Image)
            assert mock_client.models.generate_content.call_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_retry_fails(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import _generate_single_option

        text_response = _mock_gemini_response(with_image=False)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = text_response

        with (
            patch("app.activities.generate.get_client", return_value=mock_client),
            pytest.raises(ApplicationError, match="text-only response"),
        ):
            await _generate_single_option("test prompt", [_make_test_image()], [], 0)


class TestGenerateDesignsActivity:
    """Tests for the full generate_designs activity with mocks."""

    @pytest.fixture(autouse=True)
    def _mock_r2_resolve(self):
        """Mock R2 URL resolution so tests don't need real R2 credentials."""
        with patch(
            "app.utils.r2.generate_presigned_url",
            side_effect=lambda key: f"https://r2.example.com/{key}",
        ):
            yield

    @pytest.mark.asyncio
    async def test_error_on_rate_limit(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                return_value=[_make_test_image()],
            ),
            patch(
                "app.activities.generate._generate_single_option",
                new_callable=AsyncMock,
                side_effect=Exception("429 RESOURCE_EXHAUSTED"),
            ),
        ):
            with pytest.raises(ApplicationError, match="rate limited") as exc_info:
                await generate_designs(inp)
            assert not exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_error_on_safety_block(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                return_value=[_make_test_image()],
            ),
            patch(
                "app.activities.generate._generate_single_option",
                new_callable=AsyncMock,
                side_effect=Exception("SAFETY content blocked"),
            ),
        ):
            with pytest.raises(ApplicationError, match="Content policy") as exc_info:
                await generate_designs(inp)
            assert exc_info.value.non_retryable

    @pytest.mark.asyncio
    async def test_happy_path_end_to_end(self):
        """Full activity happy path: download → generate 2 → upload → output."""
        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                side_effect=[[_make_test_image()], []],
            ),
            patch(
                "app.activities.generate._generate_single_option",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ),
            patch(
                "app.activities.generate._upload_image",
                side_effect=[
                    "https://r2.example.com/option_0.png",
                    "https://r2.example.com/option_1.png",
                ],
            ),
        ):
            result = await generate_designs(inp)
            assert len(result.options) == 2
            assert result.options[0].image_url == "https://r2.example.com/option_0.png"
            assert result.options[1].image_url == "https://r2.example.com/option_1.png"

    @pytest.mark.asyncio
    async def test_error_on_no_room_photos(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                side_effect=[[], []],
            ),
            pytest.raises(ApplicationError, match="No room photos"),
        ):
            await generate_designs(inp)

    @pytest.mark.asyncio
    async def test_generic_error_retryable(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                return_value=[_make_test_image()],
            ),
            patch(
                "app.activities.generate._generate_single_option",
                new_callable=AsyncMock,
                side_effect=RuntimeError("unexpected error"),
            ),
            pytest.raises(ApplicationError, match="Generation failed"),
        ):
            await generate_designs(inp)

    @pytest.mark.asyncio
    async def test_application_error_passthrough(self):
        from temporalio.exceptions import ApplicationError

        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(room_photo_urls=["projects/test-proj/room_photos/room.jpg"])

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                side_effect=ApplicationError("custom", non_retryable=True),
            ),
            pytest.raises(ApplicationError, match="custom"),
        ):
            await generate_designs(inp)


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


class TestUploadImageGenerate:
    """Tests for _upload_image in generate.py."""

    def test_upload_returns_presigned_url(self):
        from app.activities.generate import _upload_image

        img = _make_test_image()

        with (
            patch("app.utils.r2.upload_object") as mock_upload,
            patch(
                "app.utils.r2.generate_presigned_url",
                return_value="https://r2.example.com/presigned/gen.png",
            ),
        ):
            url = _upload_image(img, "proj-123", "option_0.png")
            assert url == "https://r2.example.com/presigned/gen.png"
            mock_upload.assert_called_once()
            call_args = mock_upload.call_args[0]
            assert call_args[0] == "projects/proj-123/generated/option_0.png"


class TestGenerateSingleOptionWithInspiration:
    """Test _generate_single_option with inspiration images."""

    @pytest.mark.asyncio
    async def test_includes_inspiration_images(self):
        from app.activities.generate import _generate_single_option

        response = _mock_gemini_response(with_image=True)
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = response

        room_images = [_make_test_image()]
        inspiration_images = [_make_test_image(50, 50)]

        with patch("app.activities.generate.get_client", return_value=mock_client):
            result = await _generate_single_option(
                "test prompt", room_images, inspiration_images, 0
            )
            assert isinstance(result, Image.Image)
            # Verify content includes both room + inspiration images
            call_args = mock_client.models.generate_content.call_args
            contents = call_args[1]["contents"]
            # Should be: room image + inspiration image + text prompt = 3 items
            assert len(contents) == 3


class TestImageCountTruncation:
    """Tests for input image truncation to model limit."""

    @pytest.fixture(autouse=True)
    def _mock_r2_resolve(self):
        """Mock R2 URL resolution so tests don't need real R2 credentials."""
        with patch(
            "app.utils.r2.generate_presigned_url",
            side_effect=lambda key: f"https://r2.example.com/{key}",
        ):
            yield

    @pytest.mark.asyncio
    async def test_truncates_inspiration_when_over_limit(self):
        from app.activities.generate import generate_designs

        inp = GenerateDesignsInput(
            room_photo_urls=["projects/test-proj/room_photos/room.jpg"],
            inspiration_photo_urls=[f"https://example.com/inspo{i}.jpg" for i in range(15)],
        )

        with (
            patch(
                "app.activities.generate.download_images",
                new_callable=AsyncMock,
                side_effect=lambda urls: [_make_test_image() for _ in urls],
            ),
            patch(
                "app.activities.generate._generate_single_option",
                new_callable=AsyncMock,
                return_value=_make_test_image(),
            ) as mock_gen,
            patch(
                "app.activities.generate._upload_image",
                return_value="https://r2.example.com/result.png",
            ),
        ):
            result = await generate_designs(inp)
            assert len(result.options) == 2
            # _generate_single_option should receive truncated inspiration list
            call_args = mock_gen.call_args_list[0]
            inspiration_received = call_args[0][2]  # third positional arg
            assert len(inspiration_received) <= 14
