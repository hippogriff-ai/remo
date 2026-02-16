"""Artifact detection layer — detects annotation markers leaked into generated images.

Uses OpenCV HoughCircles to find residual red/blue/green circle markers
from the annotation drawing utility. Cheap, fast, and reliable.

CLIP/SSIM metrics were removed — they gave false positives and the VLM judge
(Claude Vision) is the single authoritative eval signal. See design_eval.py.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import numpy as np
    from PIL import Image

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — heavy deps gated behind try/except
# ---------------------------------------------------------------------------

_np_available = False
try:
    import numpy as np

    _np_available = True
except ImportError:
    np = None  # type: ignore[assignment]

_cv2_available = False
try:
    import cv2

    _cv2_available = True
except ImportError:
    cv2 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Annotation artifact detection constants
# ---------------------------------------------------------------------------

# Annotation artifact detection (HSV ranges for red/blue/green markers)
_ANNOTATION_HSV_RANGES = [
    # Red (wraps around hue=0)
    ((0, 120, 100), (10, 255, 255)),
    ((170, 120, 100), (180, 255, 255)),
    # Blue
    ((100, 120, 100), (130, 255, 255)),
    # Green
    ((35, 120, 100), (85, 255, 255)),
]
ARTIFACT_AREA_THRESHOLD = 0.001  # 0.1% of image area = suspicious
ARTIFACT_CIRCLE_MIN_RADIUS = 5
ARTIFACT_CIRCLE_MAX_RADIUS = 40


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ArtifactRegion:
    """A detected annotation artifact region."""

    x: int
    y: int
    radius: int
    color_range: str  # "red", "blue", or "green"


@dataclass
class ArtifactCheckResult:
    """Result of annotation artifact detection."""

    has_artifacts: bool
    artifact_count: int
    artifact_regions: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Artifact detection
# ---------------------------------------------------------------------------


def detect_annotation_artifacts(
    image: Image.Image,
) -> tuple[bool, list[ArtifactRegion]]:
    """Detect residual annotation markers (colored circles) in an image.

    Looks for the same red/blue/green circle markers used by the annotation
    drawing utility (see image.py). Uses HSV color masking + HoughCircles.

    Returns (has_artifacts, regions). Returns (False, []) if cv2 is unavailable.
    """
    if not _cv2_available or not _np_available:
        logger.debug("cv2/numpy unavailable, skipping artifact detection")
        return False, []

    img_array = np.array(image.convert("RGB"))
    hsv = cv2.cvtColor(img_array, cv2.COLOR_RGB2HSV)
    h, w = img_array.shape[:2]
    total_pixels = h * w
    color_names = ["red", "red", "blue", "green"]

    regions: list[ArtifactRegion] = []

    for idx, (lower, upper) in enumerate(_ANNOTATION_HSV_RANGES):
        mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
        colored_area = np.count_nonzero(mask)

        if colored_area / total_pixels < ARTIFACT_AREA_THRESHOLD:
            continue

        # Look for circular shapes in the mask
        circles = cv2.HoughCircles(
            mask,
            cv2.HOUGH_GRADIENT,
            dp=1.5,
            minDist=20,
            param1=50,
            param2=15,
            minRadius=ARTIFACT_CIRCLE_MIN_RADIUS,
            maxRadius=ARTIFACT_CIRCLE_MAX_RADIUS,
        )
        if circles is not None:
            for circle in circles[0]:
                cx, cy, cr = int(circle[0]), int(circle[1]), int(circle[2])
                regions.append(ArtifactRegion(x=cx, y=cy, radius=cr, color_range=color_names[idx]))

    return len(regions) > 0, regions


def run_artifact_check(image: Image.Image) -> ArtifactCheckResult:
    """Run annotation artifact detection and return structured result."""
    has, regions = detect_annotation_artifacts(image)
    return ArtifactCheckResult(
        has_artifacts=has,
        artifact_count=len(regions),
        artifact_regions=[
            {"x": r.x, "y": r.y, "radius": r.radius, "color": r.color_range} for r in regions
        ],
    )
