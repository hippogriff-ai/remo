"""Annotation drawing utility for Gemini-based image editing.

Draws numbered circle badges on images to mark regions for targeted edits.
Gemini uses these visual annotations to identify which areas to modify.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from app.models.contracts import AnnotationRegion

# Circle outline colors by region_id (1-indexed)
REGION_COLORS = {
    1: "#FF0000",  # red
    2: "#0000FF",  # blue
    3: "#00FF00",  # green
}

OUTLINE_WIDTH = 4
BADGE_RADIUS = 16
FONT_SIZE = 24
MIN_CIRCLE_FRACTION = 0.02  # minimum 2% of image dimension


def draw_annotations(
    image: Image.Image,
    regions: list[AnnotationRegion],
) -> Image.Image:
    """Draw numbered circle annotations on an image.

    Args:
        image: Base PIL Image to annotate.
        regions: List of AnnotationRegion with normalized 0-1 coordinates.

    Returns:
        New PIL Image with annotations drawn (original is not modified).
    """
    annotated = image.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = annotated.size
    min_dim = min(w, h)

    font = _load_font(FONT_SIZE)

    for region in regions:
        cx = int(region.center_x * w)
        cy = int(region.center_y * h)
        r = _clamp_radius(region.radius, min_dim)
        color = REGION_COLORS.get(region.region_id, "#FF0000")

        _draw_circle_outline(draw, cx, cy, r, color)
        _draw_badge(draw, cx, cy, r, region.region_id, color, font)

    annotated = Image.alpha_composite(annotated, overlay)
    return annotated.convert("RGB")


def _clamp_radius(normalized_radius: float, min_dim: int) -> int:
    """Convert normalized radius to pixels, clamping to minimum visible size."""
    r = int(normalized_radius * min_dim)
    min_r = int(MIN_CIRCLE_FRACTION * min_dim)
    return max(r, min_r)


def _draw_circle_outline(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, color: str) -> None:
    """Draw a colored circle outline around the target area."""
    draw.ellipse(
        [(cx - r, cy - r), (cx + r, cy + r)],
        outline=color,
        width=OUTLINE_WIDTH,
    )


def _draw_badge(
    draw: ImageDraw.ImageDraw,
    cx: int,
    cy: int,
    r: int,
    region_id: int,
    color: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    """Draw a numbered badge at the top-right of the circle."""
    badge_x = cx + r - BADGE_RADIUS
    badge_y = cy - r - BADGE_RADIUS

    # Filled circle background
    draw.ellipse(
        [
            (badge_x - BADGE_RADIUS, badge_y - BADGE_RADIUS),
            (badge_x + BADGE_RADIUS, badge_y + BADGE_RADIUS),
        ],
        fill=color,
        outline="white",
        width=2,
    )

    # White number with black stroke for readability
    text = str(region_id)
    bbox = font.getbbox(text)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = badge_x - tw // 2
    ty = badge_y - th // 2 - bbox[1]

    # Black stroke (draw text offset in 4 directions)
    for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
        draw.text((tx + dx, ty + dy), text, fill="black", font=font)

    draw.text((tx, ty), text, fill="white", font=font)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font, falling back to default if system fonts unavailable."""
    font_paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def image_to_bytes(image: Image.Image, fmt: str = "PNG") -> bytes:
    """Convert PIL Image to bytes."""
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()
