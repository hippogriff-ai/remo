"""Tests for photo validation — resolution, blur, and content classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
from PIL import Image, ImageDraw, ImageFilter

from app.activities.validation import (
    MIN_RESOLUTION,
    _check_blur,
    _check_content,
    _check_resolution,
    _detect_media_type,
    validate_photo,
)
from app.models.contracts import ValidatePhotoInput, ValidatePhotoOutput


def _make_image(width: int, height: int, mode: str = "RGB") -> Image.Image:
    """Create a solid-color test image of the given size."""
    return Image.new(mode, (width, height), color=(128, 128, 128))


def _make_sharp_image(width: int = 2048, height: int = 1536) -> Image.Image:
    """Create an image with high-frequency detail (passes blur check)."""
    img = Image.new("RGB", (width, height), color=(200, 200, 200))
    draw = ImageDraw.Draw(img)
    # Draw a dense grid of alternating black/white lines for high variance
    for x in range(0, width, 4):
        draw.line([(x, 0), (x, height)], fill=(0, 0, 0), width=1)
    for y in range(0, height, 4):
        draw.line([(0, y), (width, y)], fill=(0, 0, 0), width=1)
    return img


def _make_blurry_image(width: int = 2048, height: int = 1536) -> Image.Image:
    """Create an image that will fail the blur check (low Laplacian variance)."""
    # Start with a solid color — nearly zero variance after Laplacian
    img = Image.new("RGB", (width, height), color=(128, 128, 128))
    # Apply heavy Gaussian blur to ensure very low edge content
    img = img.filter(ImageFilter.GaussianBlur(radius=20))
    return img


def _image_to_bytes(img: Image.Image, fmt: str = "JPEG") -> bytes:
    """Serialize a PIL Image to bytes."""
    import io

    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


# ── Resolution checks ──────────────────────────────────────────────


class TestCheckResolution:
    """Tests for _check_resolution — minimum shortest-side pixel count."""

    def test_passes_at_exact_minimum(self) -> None:
        """1024x2048 image should pass (shortest side == minimum)."""
        img = _make_image(2048, MIN_RESOLUTION)
        ok, msg = _check_resolution(img)
        assert ok is True
        assert msg == ""

    def test_passes_large_image(self) -> None:
        """4000x3000 image should pass easily."""
        img = _make_image(4000, 3000)
        ok, msg = _check_resolution(img)
        assert ok is True

    def test_fails_below_minimum(self) -> None:
        """800x600 image should fail — shortest side 600 < 1024."""
        img = _make_image(800, 600)
        ok, msg = _check_resolution(img)
        assert ok is False
        assert "600px" in msg
        assert str(MIN_RESOLUTION) in msg

    def test_fails_one_side_below(self) -> None:
        """2000x512 image — shortest side 512 < 1024."""
        img = _make_image(2000, 512)
        ok, msg = _check_resolution(img)
        assert ok is False
        assert "512px" in msg

    def test_square_at_minimum(self) -> None:
        """1024x1024 square should pass."""
        img = _make_image(MIN_RESOLUTION, MIN_RESOLUTION)
        ok, msg = _check_resolution(img)
        assert ok is True


# ── Blur checks ─────────────────────────────────────────────────────


class TestCheckBlur:
    """Tests for _check_blur — Laplacian variance on grayscale image."""

    def test_sharp_image_passes(self) -> None:
        """Image with dense edges should have high variance and pass."""
        img = _make_sharp_image()
        ok, msg = _check_blur(img)
        assert ok is True
        assert msg == ""

    def test_blurry_image_fails(self) -> None:
        """Solid/blurred image should have low variance and fail."""
        img = _make_blurry_image()
        ok, msg = _check_blur(img)
        assert ok is False
        assert "blurry" in msg.lower()

    def test_small_image_no_resize(self) -> None:
        """Image smaller than NORMALIZE_SIZE should not be resized."""
        img = _make_sharp_image(512, 512)
        ok, _ = _check_blur(img)
        # Should still work (no crash), result depends on content
        assert isinstance(ok, bool)

    def test_grayscale_conversion(self) -> None:
        """RGBA image should be handled (converted to grayscale internally)."""
        img = _make_sharp_image()
        img = img.convert("RGBA")
        ok, _ = _check_blur(img)
        assert isinstance(ok, bool)


# ── Content classification checks ───────────────────────────────────


def _mock_anthropic_response(text: str) -> MagicMock:
    """Create a mock Anthropic API response with the given text."""
    content_block = MagicMock()
    content_block.text = text
    response = MagicMock()
    response.content = [content_block]
    return response


class TestDetectMediaType:
    """Tests for _detect_media_type — format detection from image bytes."""

    def test_jpeg_detected(self) -> None:
        """JPEG bytes should return image/jpeg."""
        img = _make_image(100, 100)
        assert _detect_media_type(_image_to_bytes(img, fmt="JPEG")) == "image/jpeg"

    def test_png_detected(self) -> None:
        """PNG bytes should return image/png."""
        img = _make_image(100, 100)
        assert _detect_media_type(_image_to_bytes(img, fmt="PNG")) == "image/png"


class TestCheckContent:
    """Tests for _check_content — Claude Haiku 4.5 image classification."""

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_room_photo_accepted(self, mock_get_client: MagicMock, _mock_media: MagicMock) -> None:
        """YES response for a room photo should pass."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "YES. This is a photo of a modern living room."
        )
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "room")
        assert ok is True
        assert msg == ""

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_room_photo_rejected(self, mock_get_client: MagicMock, _mock_media: MagicMock) -> None:
        """NO response for a room photo should fail with reason."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "NO. This appears to be a photo of a cat."
        )
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "room")
        assert ok is False
        assert "room" in msg
        assert "cat" in msg

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_inspiration_photo_accepted(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """YES response for inspiration photo should pass."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response(
            "YES. This is a beautiful interior design mood board."
        )
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "inspiration")
        assert ok is True

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_api_error_fails_open(self, mock_get_client: MagicMock, _mock_media: MagicMock) -> None:
        """Anthropic API exception should fail open (return True) for P1."""
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock(),
        )
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "room")
        assert ok is True
        assert msg == ""

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_empty_response_content_fails_open(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """Empty response.content should fail open (return True)."""
        mock_client = MagicMock()
        response = MagicMock()
        response.content = []
        mock_client.messages.create.return_value = response
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "room")
        assert ok is True
        assert msg == ""

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_non_text_block_response_fails_open(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """Response with non-text content block should fail open."""
        mock_client = MagicMock()
        content_block = MagicMock(spec=[])  # no .text attribute
        response = MagicMock()
        response.content = [content_block]
        mock_client.messages.create.return_value = response
        mock_get_client.return_value = mock_client

        ok, msg = _check_content(b"fake-image-data", "room")
        assert ok is True
        assert msg == ""

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_room_prompt_used_for_room_type(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """Room photo_type should use the interior room prompt."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("YES")
        mock_get_client.return_value = mock_client

        _check_content(b"fake-image-data", "room")

        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
        text_block = next(b for b in user_content if b["type"] == "text")
        assert "interior room" in text_block["text"]

    @patch("app.activities.validation._detect_media_type", return_value="image/png")
    @patch("app.activities.validation._get_anthropic_client")
    def test_media_type_passed_to_api(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """Detected media type should be sent to the Anthropic API."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("YES")
        mock_get_client.return_value = mock_client

        _check_content(b"fake-image-data", "room")

        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
        img_block = next(b for b in user_content if b["type"] == "image")
        assert img_block["source"]["media_type"] == "image/png"

    @patch("app.activities.validation._detect_media_type", return_value="image/jpeg")
    @patch("app.activities.validation._get_anthropic_client")
    def test_inspiration_prompt_used_for_other_type(
        self, mock_get_client: MagicMock, _mock_media: MagicMock
    ) -> None:
        """Non-room photo_type should use the design inspiration prompt."""
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_anthropic_response("YES")
        mock_get_client.return_value = mock_client

        _check_content(b"fake-image-data", "inspiration")

        call_kwargs = mock_client.messages.create.call_args[1]
        user_content = call_kwargs["messages"][0]["content"]
        text_block = next(b for b in user_content if b["type"] == "text")
        assert "design inspiration" in text_block["text"]


# ── Integration: validate_photo ─────────────────────────────────────


class TestValidatePhoto:
    """Tests for the top-level validate_photo function."""

    def test_invalid_image_data(self) -> None:
        """Corrupt bytes should return invalid_image failure."""
        inp = ValidatePhotoInput(image_data=b"not-an-image", photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "invalid_image" in result.failures
        assert len(result.messages) == 1
        assert "valid JPEG or PNG" in result.messages[0]

    def test_low_resolution_fails(self) -> None:
        """Small image should fail with low_resolution."""
        img = _make_image(500, 500)
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "low_resolution" in result.failures

    @patch(
        "app.activities.validation._check_blur",
        return_value=(False, "Image appears blurry (score: 20, minimum: 60)."),
    )
    def test_blurry_image_fails(self, _mock_blur: MagicMock) -> None:
        """Image failing blur check should return blurry failure."""
        img = _make_sharp_image(2048, 2048)
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "blurry" in result.failures

    @patch(
        "app.activities.validation._check_blur",
        return_value=(False, "Image appears blurry (score: 10, minimum: 60)."),
    )
    def test_both_resolution_and_blur_can_fail(self, _mock_blur: MagicMock) -> None:
        """Small blurry image should report both failures."""
        img = _make_image(500, 500)
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "low_resolution" in result.failures
        assert "blurry" in result.failures

    @patch("app.activities.validation.settings")
    def test_skips_content_check_when_no_api_key(self, mock_settings: MagicMock) -> None:
        """Content check should be skipped when anthropic_api_key is not set."""
        mock_settings.anthropic_api_key = ""
        img = _make_sharp_image()
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is True
        assert "Photo looks great!" in result.messages

    @patch("app.activities.validation.settings")
    def test_skips_content_check_when_basic_checks_fail(self, mock_settings: MagicMock) -> None:
        """Content classification should not run if resolution/blur failed."""
        mock_settings.anthropic_api_key = "sk-test"
        img = _make_image(500, 500)  # fails resolution
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")

        with patch("app.activities.validation._check_content") as mock_content:
            result = validate_photo(inp)
            mock_content.assert_not_called()

        assert result.passed is False

    @patch("app.activities.validation._check_content", return_value=(True, ""))
    @patch("app.activities.validation.settings")
    def test_happy_path_all_checks_pass(
        self, mock_settings: MagicMock, mock_content: MagicMock
    ) -> None:
        """Sharp, high-res image with passing content check should succeed."""
        mock_settings.anthropic_api_key = "sk-test"
        img = _make_sharp_image()
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is True
        assert result.failures == []
        assert "Photo looks great!" in result.messages
        mock_content.assert_called_once()

    @patch("app.activities.validation._check_content", return_value=(False, "Not a room"))
    @patch("app.activities.validation.settings")
    def test_content_rejection(self, mock_settings: MagicMock, mock_content: MagicMock) -> None:
        """Passing basic checks but failing content should report content_rejected."""
        mock_settings.anthropic_api_key = "sk-test"
        img = _make_sharp_image()
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img), photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "content_rejected" in result.failures
        assert "Not a room" in result.messages

    def test_output_model_structure(self) -> None:
        """validate_photo should always return a ValidatePhotoOutput."""
        inp = ValidatePhotoInput(image_data=b"garbage", photo_type="inspiration")
        result = validate_photo(inp)
        assert isinstance(result, ValidatePhotoOutput)
        assert isinstance(result.passed, bool)
        assert isinstance(result.failures, list)
        assert isinstance(result.messages, list)

    def test_truncated_image_returns_invalid(self) -> None:
        """Truncated JPEG (valid header, incomplete body) returns invalid_image.

        Image.open() succeeds lazily on truncated images, but img.load()
        forces full decode and catches the truncation inside the try/except.
        """
        # Create a valid JPEG, then truncate it
        img = _make_sharp_image()
        full_bytes = _image_to_bytes(img, fmt="JPEG")
        truncated = full_bytes[: len(full_bytes) // 4]  # Keep only header + partial body

        inp = ValidatePhotoInput(image_data=truncated, photo_type="room")
        result = validate_photo(inp)

        assert result.passed is False
        assert "invalid_image" in result.failures

    def test_png_format_accepted(self) -> None:
        """PNG images should be parsed correctly (not just JPEG)."""
        img = _make_sharp_image()
        inp = ValidatePhotoInput(image_data=_image_to_bytes(img, fmt="PNG"), photo_type="room")
        with patch("app.activities.validation.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            result = validate_photo(inp)

        assert result.passed is True
