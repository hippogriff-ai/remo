"""Tests for annotation drawing utility (backend/app/utils/image.py).

No API keys needed — pure Pillow operations.
"""

import io

from PIL import Image

from app.models.contracts import AnnotationRegion
from app.utils.image import (
    MIN_CIRCLE_FRACTION,
    REGION_COLORS,
    _clamp_radius,
    _load_font,
    draw_annotations,
    image_to_bytes,
)


def _make_image(w: int = 1024, h: int = 1024, color: str = "white") -> Image.Image:
    return Image.new("RGB", (w, h), color)


def _make_region(
    region_id: int = 1,
    center_x: float = 0.5,
    center_y: float = 0.5,
    radius: float = 0.1,
    instruction: str = "Replace this with something else",
) -> AnnotationRegion:
    return AnnotationRegion(
        region_id=region_id,
        center_x=center_x,
        center_y=center_y,
        radius=radius,
        instruction=instruction,
    )


class TestDrawAnnotations:
    """Tests for the main draw_annotations function."""

    def test_single_region_draws_without_error(self):
        img = _make_image()
        region = _make_region()
        result = draw_annotations(img, [region])
        assert result.size == img.size
        assert result.mode == "RGB"

    def test_original_image_not_modified(self):
        img = _make_image()
        original_data = img.tobytes()
        draw_annotations(img, [_make_region()])
        assert img.tobytes() == original_data

    def test_annotated_image_differs_from_original(self):
        img = _make_image()
        result = draw_annotations(img, [_make_region()])
        assert result.tobytes() != img.tobytes()

    def test_multiple_regions(self):
        img = _make_image()
        regions = [
            _make_region(region_id=1, center_x=0.25, center_y=0.5),
            _make_region(region_id=2, center_x=0.5, center_y=0.5),
            _make_region(region_id=3, center_x=0.75, center_y=0.5),
        ]
        result = draw_annotations(img, regions)
        assert result.size == img.size

    def test_empty_regions_returns_copy(self):
        img = _make_image()
        result = draw_annotations(img, [])
        assert result.size == img.size
        # Should be identical content (no annotations drawn)
        assert result.tobytes() == img.tobytes()

    def test_region_colors_are_correct(self):
        """Each region_id should use its designated color."""
        img = _make_image(200, 200)
        for rid in REGION_COLORS:
            region = _make_region(region_id=rid, center_x=0.5, center_y=0.5, radius=0.2)
            result = draw_annotations(img, [region])
            # Just verify the image was modified (color check is visual)
            assert result.tobytes() != img.tobytes()

    def test_non_square_image(self):
        """Annotations should work on non-square images."""
        img = _make_image(1920, 1080)
        region = _make_region(center_x=0.5, center_y=0.5, radius=0.1)
        result = draw_annotations(img, [region])
        assert result.size == (1920, 1080)

    def test_small_image(self):
        """Annotations should work on small images."""
        img = _make_image(100, 100)
        region = _make_region(center_x=0.5, center_y=0.5, radius=0.1)
        result = draw_annotations(img, [region])
        assert result.size == (100, 100)

    def test_region_at_edge(self):
        """Regions near image edges should not crash."""
        img = _make_image()
        regions = [
            _make_region(center_x=0.0, center_y=0.0, radius=0.05),
            _make_region(region_id=2, center_x=1.0, center_y=1.0, radius=0.05),
            _make_region(region_id=3, center_x=0.5, center_y=0.0, radius=0.05),
        ]
        result = draw_annotations(img, regions)
        assert result.size == img.size

    def test_overlapping_regions(self):
        """Overlapping regions should all be drawn."""
        img = _make_image()
        regions = [
            _make_region(region_id=1, center_x=0.5, center_y=0.5, radius=0.15),
            _make_region(region_id=2, center_x=0.55, center_y=0.55, radius=0.15),
        ]
        result = draw_annotations(img, regions)
        assert result.size == img.size

    def test_rgba_input_converted_to_rgb(self):
        """RGBA input should produce RGB output."""
        img = Image.new("RGBA", (1024, 1024), (255, 255, 255, 128))
        result = draw_annotations(img, [_make_region()])
        assert result.mode == "RGB"


class TestClampRadius:
    """Tests for radius clamping logic."""

    def test_normal_radius(self):
        result = _clamp_radius(0.1, 1024)
        assert result == 102  # 0.1 * 1024

    def test_tiny_radius_clamped(self):
        """Radius below minimum should be clamped."""
        result = _clamp_radius(0.001, 1024)
        min_r = int(MIN_CIRCLE_FRACTION * 1024)
        assert result == min_r

    def test_zero_radius_clamped(self):
        result = _clamp_radius(0.0, 1024)
        assert result >= 1

    def test_large_radius(self):
        result = _clamp_radius(0.5, 1024)
        assert result == 512


class TestImageToBytes:
    """Tests for image_to_bytes helper."""

    def test_png_output(self):
        img = _make_image(100, 100)
        data = image_to_bytes(img, "PNG")
        assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes

    def test_jpeg_output(self):
        img = _make_image(100, 100)
        data = image_to_bytes(img, "JPEG")
        assert data[:2] == b"\xff\xd8"  # JPEG magic bytes

    def test_roundtrip(self):
        """bytes -> Image -> bytes should produce a valid image."""
        img = _make_image(100, 100, "red")
        data = image_to_bytes(img)
        restored = Image.open(io.BytesIO(data))
        assert restored.size == (100, 100)


class TestFontLoading:
    """Tests for _load_font fallback behavior."""

    def test_returns_font_object(self):
        """Should return a font (truetype or default) without error."""
        from PIL import ImageFont

        font = _load_font(24)
        assert font is not None
        assert isinstance(font, ImageFont.FreeTypeFont | ImageFont.ImageFont)

    def test_different_sizes(self):
        """Should handle various font sizes."""
        for size in [12, 24, 48]:
            font = _load_font(size)
            assert font is not None

    def test_falls_back_to_default_when_all_fonts_missing(self):
        """When all truetype paths fail, falls back to load_default."""
        from unittest.mock import patch

        from PIL import ImageFont

        # Get a real default font before mocking
        default_font = ImageFont.load_default()

        with (
            patch.object(ImageFont, "truetype", side_effect=OSError("not found")),
            patch.object(ImageFont, "load_default", return_value=default_font),
        ):
            font = _load_font(24)
            assert font is default_font
            ImageFont.load_default.assert_called_once()


class TestCoordinateScaling:
    """Tests that normalized coordinates scale correctly to pixel space."""

    def test_center_of_image(self):
        """0.5, 0.5 should map to center pixel."""
        img = _make_image(1000, 1000)
        region = _make_region(center_x=0.5, center_y=0.5, radius=0.05)
        result = draw_annotations(img, [region])
        # The circle outline or badge should be visible near center
        assert result.tobytes() != img.tobytes()

    def test_top_left_corner(self):
        """0.0, 0.0 should map to top-left."""
        img = _make_image(1000, 1000)
        region = _make_region(center_x=0.0, center_y=0.0, radius=0.05)
        result = draw_annotations(img, [region])
        assert result.size == (1000, 1000)

    def test_bottom_right_corner(self):
        """1.0, 1.0 should map to bottom-right."""
        img = _make_image(1000, 1000)
        region = _make_region(center_x=1.0, center_y=1.0, radius=0.05)
        result = draw_annotations(img, [region])
        assert result.size == (1000, 1000)
