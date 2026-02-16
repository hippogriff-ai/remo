"""Unit tests for the artifact detection layer (image_eval.py).

Tests artifact detection with synthetic images, dataclass construction,
and graceful degradation when cv2/numpy are unavailable.

CLIP/SSIM tests removed — those metrics were dropped in favor of VLM-only eval.
"""

from __future__ import annotations

from unittest.mock import patch

from PIL import Image

from app.utils.image_eval import (
    ArtifactCheckResult,
    ArtifactRegion,
    detect_annotation_artifacts,
    run_artifact_check,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _solid_image(color: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> Image.Image:
    """Create a solid-color test image using PIL (no numpy needed)."""
    return Image.new("RGB", size, color)


# ---------------------------------------------------------------------------
# ArtifactCheckResult dataclass
# ---------------------------------------------------------------------------


class TestArtifactCheckResult:
    def test_construction(self):
        result = ArtifactCheckResult(
            has_artifacts=False,
            artifact_count=0,
            artifact_regions=[],
        )
        assert result.has_artifacts is False
        assert result.artifact_count == 0
        assert result.artifact_regions == []

    def test_with_artifacts(self):
        result = ArtifactCheckResult(
            has_artifacts=True,
            artifact_count=2,
            artifact_regions=[
                {"x": 10, "y": 20, "radius": 15, "color": "red"},
                {"x": 50, "y": 60, "radius": 10, "color": "blue"},
            ],
        )
        assert result.has_artifacts is True
        assert result.artifact_count == 2
        assert len(result.artifact_regions) == 2


class TestArtifactRegion:
    def test_construction(self):
        region = ArtifactRegion(x=100, y=200, radius=15, color_range="red")
        assert region.x == 100
        assert region.y == 200
        assert region.radius == 15
        assert region.color_range == "red"


# ---------------------------------------------------------------------------
# Artifact detection
# ---------------------------------------------------------------------------


class TestArtifactDetection:
    def test_clean_image_no_artifacts(self):
        """A plain gray image with no annotation markers should be clean."""
        img = _solid_image((200, 200, 200))
        has, regions = detect_annotation_artifacts(img)
        assert has is False
        assert regions == []

    def test_artifact_detection_skipped_when_cv2_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._cv2_available", False):
            has, regions = detect_annotation_artifacts(img)
        assert has is False
        assert regions == []

    def test_artifact_detection_skipped_when_numpy_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._np_available", False):
            has, regions = detect_annotation_artifacts(img)
        assert has is False
        assert regions == []


# ---------------------------------------------------------------------------
# run_artifact_check wrapper
# ---------------------------------------------------------------------------


class TestRunArtifactCheck:
    def test_returns_artifact_check_result(self):
        img = _solid_image((200, 200, 200))
        result = run_artifact_check(img)
        assert isinstance(result, ArtifactCheckResult)
        assert result.has_artifacts is False
        assert result.artifact_count == 0
        assert result.artifact_regions == []

    def test_skipped_when_cv2_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._cv2_available", False):
            result = run_artifact_check(img)
        assert result.has_artifacts is False
        assert result.artifact_count == 0

    def test_skipped_when_numpy_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._np_available", False):
            result = run_artifact_check(img)
        assert result.has_artifacts is False
        assert result.artifact_count == 0
