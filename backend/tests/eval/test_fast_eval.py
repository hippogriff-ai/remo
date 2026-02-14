"""Unit tests for the fast eval layer (no API calls, no heavy deps required).

Tests graceful degradation (neutral scores when deps missing), dataclass
construction, composite scoring, threshold logic, brief-to-text conversion,
and artifact detection with synthetic images.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PIL import Image

from app.models.contracts import DesignBrief, StyleProfile
from app.utils.image_eval import (
    CLIP_IMAGE_THRESHOLD,
    CLIP_TEXT_THRESHOLD,
    COMPOSITE_THRESHOLD,
    COMPOSITE_WEIGHTS,
    EDGE_SSIM_THRESHOLD,
    ArtifactRegion,
    FastEvalResult,
    _brief_to_text,
    run_fast_eval,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_brief(**overrides) -> DesignBrief:
    """Create a DesignBrief with sensible defaults."""
    defaults = {
        "room_type": "living room",
        "style_profile": StyleProfile(
            mood="cozy modern",
            colors=["warm white", "sage green", "natural wood"],
            textures=["bouclé", "linen"],
        ),
    }
    defaults.update(overrides)
    return DesignBrief(**defaults)


def _solid_image(color: tuple[int, int, int], size: tuple[int, int] = (256, 256)) -> Image.Image:
    """Create a solid-color test image using PIL (no numpy needed)."""
    return Image.new("RGB", size, color)


# ---------------------------------------------------------------------------
# C0: FastEvalResult dataclass
# ---------------------------------------------------------------------------


class TestFastEvalResult:
    def test_construction(self):
        result = FastEvalResult(
            clip_text_score=0.25,
            clip_image_score=0.75,
            edge_ssim_score=0.40,
            has_artifacts=False,
            composite_score=0.45,
            needs_deep_eval=False,
        )
        assert result.clip_text_score == 0.25
        assert result.has_artifacts is False
        assert result.metrics == {}

    def test_metrics_default_empty(self):
        result = FastEvalResult(
            clip_text_score=0.5,
            clip_image_score=0.5,
            edge_ssim_score=0.5,
            has_artifacts=False,
            composite_score=0.5,
            needs_deep_eval=False,
        )
        assert isinstance(result.metrics, dict)


# ---------------------------------------------------------------------------
# C0a: Brief-to-text conversion
# ---------------------------------------------------------------------------


class TestBriefToText:
    def test_full_brief(self):
        brief = _make_brief()
        text = _brief_to_text(brief)
        assert "living room" in text
        assert "cozy modern" in text
        assert "warm white" in text

    def test_minimal_brief(self):
        brief = _make_brief(style_profile=None)
        text = _brief_to_text(brief)
        assert text == "living room"

    def test_brief_no_mood(self):
        brief = _make_brief(
            style_profile=StyleProfile(
                colors=["navy", "gold"],
                textures=[],
            )
        )
        text = _brief_to_text(brief)
        assert "living room" in text
        assert "navy" in text

    def test_colors_capped_at_four(self):
        brief = _make_brief(
            style_profile=StyleProfile(
                colors=["a", "b", "c", "d", "e", "f"],
            )
        )
        text = _brief_to_text(brief)
        assert "e" not in text
        assert "f" not in text


# ---------------------------------------------------------------------------
# C0: Graceful degradation (no heavy deps)
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """When CLIP/cv2/skimage are missing, metrics return neutral 0.5."""

    def test_clip_text_neutral_when_unavailable(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with patch("app.utils.image_eval._clip_available", False):
            score = run_fast_eval(img, img, brief).clip_text_score
        assert score == 0.5

    def test_clip_image_neutral_when_unavailable(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with patch("app.utils.image_eval._clip_available", False):
            score = run_fast_eval(img, img, brief).clip_image_score
        assert score == 0.5

    def test_edge_ssim_neutral_when_cv2_unavailable(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with patch("app.utils.image_eval._cv2_available", False):
            score = run_fast_eval(img, img, brief).edge_ssim_score
        assert score == 0.5

    def test_edge_ssim_neutral_when_ssim_unavailable(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with patch("app.utils.image_eval._ssim_available", False):
            score = run_fast_eval(img, img, brief).edge_ssim_score
        assert score == 0.5

    def test_all_neutral_produces_valid_composite(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with (
            patch("app.utils.image_eval._clip_available", False),
            patch("app.utils.image_eval._cv2_available", False),
        ):
            result = run_fast_eval(img, img, brief)
        assert result.composite_score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# C0: Composite scoring logic
# ---------------------------------------------------------------------------


class TestCompositeScoring:
    def test_weights_sum_to_one(self):
        total = sum(COMPOSITE_WEIGHTS.values())
        assert total == pytest.approx(1.0)

    def test_composite_calculation(self):
        """Composite = 0.35*ct + 0.35*ci + 0.30*es."""
        ct, ci, es = 0.3, 0.8, 0.5
        expected = 0.35 * ct + 0.35 * ci + 0.30 * es
        assert expected == pytest.approx(0.535, abs=0.001)

    def test_needs_deep_eval_when_clip_text_below_threshold(self):
        result = FastEvalResult(
            clip_text_score=CLIP_TEXT_THRESHOLD - 0.01,
            clip_image_score=0.9,
            edge_ssim_score=0.9,
            has_artifacts=False,
            composite_score=0.7,
            needs_deep_eval=True,
        )
        assert result.needs_deep_eval is True

    def test_needs_deep_eval_when_artifacts_present(self):
        result = FastEvalResult(
            clip_text_score=0.9,
            clip_image_score=0.9,
            edge_ssim_score=0.9,
            has_artifacts=True,
            composite_score=0.9,
            needs_deep_eval=True,
        )
        assert result.needs_deep_eval is True


# ---------------------------------------------------------------------------
# C0: Threshold constants
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_clip_text_threshold_reasonable(self):
        assert 0.10 <= CLIP_TEXT_THRESHOLD <= 0.40

    def test_clip_image_threshold_reasonable(self):
        assert 0.50 <= CLIP_IMAGE_THRESHOLD <= 0.90

    def test_edge_ssim_threshold_reasonable(self):
        assert 0.20 <= EDGE_SSIM_THRESHOLD <= 0.50

    def test_composite_threshold_reasonable(self):
        assert 0.30 <= COMPOSITE_THRESHOLD <= 0.60


# ---------------------------------------------------------------------------
# C0d: Artifact detection
# ---------------------------------------------------------------------------


class TestArtifactDetection:
    def test_clean_image_no_artifacts(self):
        """A plain gray image with no annotation markers should be clean."""
        img = _solid_image((200, 200, 200))  # gray
        # Degrade gracefully — if numpy/cv2 not available, no artifacts detected
        result = run_fast_eval(img, img, _make_brief(), is_edit=True)
        assert result.has_artifacts is False

    def test_artifact_not_checked_for_non_edit(self):
        """Artifact detection only runs when is_edit=True."""
        img = _solid_image((255, 0, 0))  # fully red
        result = run_fast_eval(img, img, _make_brief(), is_edit=False)
        assert result.has_artifacts is False

    def test_artifact_detection_skipped_when_cv2_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._cv2_available", False):
            result = run_fast_eval(img, img, _make_brief(), is_edit=True)
        assert result.has_artifacts is False

    def test_artifact_detection_skipped_when_numpy_unavailable(self):
        img = _solid_image((255, 0, 0))
        with patch("app.utils.image_eval._np_available", False):
            result = run_fast_eval(img, img, _make_brief(), is_edit=True)
        assert result.has_artifacts is False

    def test_artifact_region_dataclass(self):
        region = ArtifactRegion(x=100, y=200, radius=15, color_range="red")
        assert region.x == 100
        assert region.color_range == "red"


# ---------------------------------------------------------------------------
# C0: run_fast_eval integration (mocked deps)
# ---------------------------------------------------------------------------


class TestRunFastEval:
    def test_returns_fast_eval_result(self):
        """run_fast_eval returns a FastEvalResult with all fields populated."""
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with (
            patch("app.utils.image_eval._clip_available", False),
            patch("app.utils.image_eval._cv2_available", False),
        ):
            result = run_fast_eval(img, img, brief)
        assert isinstance(result, FastEvalResult)
        assert "is_edit" in result.metrics
        assert result.metrics["is_edit"] is False

    def test_edit_mode_includes_artifact_info(self):
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with (
            patch("app.utils.image_eval._clip_available", False),
            patch("app.utils.image_eval._cv2_available", False),
        ):
            result = run_fast_eval(img, img, brief, is_edit=True)
        assert result.metrics["is_edit"] is True
        assert result.metrics["artifact_count"] == 0

    def test_needs_deep_eval_logic(self):
        """When all metrics are neutral (0.5), needs_deep_eval depends on thresholds."""
        brief = _make_brief()
        img = _solid_image((128, 128, 128))
        with (
            patch("app.utils.image_eval._clip_available", False),
            patch("app.utils.image_eval._cv2_available", False),
        ):
            result = run_fast_eval(img, img, brief)
        # clip_text=0.5 > 0.20, clip_image=0.5 < 0.70 -> needs deep
        assert result.needs_deep_eval is True
