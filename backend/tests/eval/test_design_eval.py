"""Unit tests for the deep eval layer (design_eval.py).

Tests rubric structure, tag assignment, criteria parsing, result dataclasses,
and evaluator functions with mocked API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.activities.design_eval import (
    _EDIT_CRITERIA_MAX,
    _GENERATION_CRITERIA_MAX,
    _SHOPPING_CRITERIA_MAX,
    CriterionScore,
    EditEvalResult,
    GenerationEvalResult,
    ShoppingVisualEvalResult,
    _edit_tag,
    _generation_tag,
    _parse_criteria,
    _shopping_tag,
    evaluate_edit,
    evaluate_generation,
    evaluate_shopping_visual,
)
from app.models.contracts import DesignBrief, StyleProfile

# ---------------------------------------------------------------------------
# Tag assignment
# ---------------------------------------------------------------------------


class TestGenerationTag:
    def test_excellent(self):
        assert _generation_tag(85) == "EXCELLENT"
        assert _generation_tag(100) == "EXCELLENT"

    def test_good(self):
        assert _generation_tag(70) == "GOOD"
        assert _generation_tag(84) == "GOOD"

    def test_acceptable(self):
        assert _generation_tag(55) == "ACCEPTABLE"
        assert _generation_tag(69) == "ACCEPTABLE"

    def test_weak(self):
        assert _generation_tag(40) == "WEAK"
        assert _generation_tag(54) == "WEAK"

    def test_fail(self):
        assert _generation_tag(0) == "FAIL"
        assert _generation_tag(39) == "FAIL"


class TestEditTag:
    def test_excellent(self):
        assert _edit_tag(42) == "EXCELLENT"
        assert _edit_tag(50) == "EXCELLENT"

    def test_good(self):
        assert _edit_tag(35) == "GOOD"
        assert _edit_tag(41) == "GOOD"

    def test_acceptable(self):
        assert _edit_tag(27) == "ACCEPTABLE"

    def test_weak(self):
        assert _edit_tag(20) == "WEAK"

    def test_fail(self):
        assert _edit_tag(0) == "FAIL"
        assert _edit_tag(19) == "FAIL"


class TestShoppingTag:
    def test_excellent(self):
        assert _shopping_tag(25) == "EXCELLENT"
        assert _shopping_tag(30) == "EXCELLENT"

    def test_good(self):
        assert _shopping_tag(20) == "GOOD"

    def test_acceptable(self):
        assert _shopping_tag(15) == "ACCEPTABLE"

    def test_weak(self):
        assert _shopping_tag(10) == "WEAK"

    def test_fail(self):
        assert _shopping_tag(0) == "FAIL"
        assert _shopping_tag(9) == "FAIL"


# ---------------------------------------------------------------------------
# Criteria parsing
# ---------------------------------------------------------------------------


class TestParseCriteria:
    def test_parses_valid_scores(self):
        raw = {
            "photorealism": 12,
            "style_adherence": 10,
            "color_palette": 8,
            "room_preservation": 18,
            "furniture_scale": 7,
            "lighting": 9,
            "design_coherence": 8,
            "brief_compliance": 4,
            "keep_items": 5,
        }
        criteria = _parse_criteria(raw, _GENERATION_CRITERIA_MAX)
        assert len(criteria) == 9
        assert criteria[0].name == "photorealism"
        assert criteria[0].score == 12
        assert criteria[0].max_score == 15

    def test_clamps_scores_to_max(self):
        raw = {"photorealism": 99}
        criteria = _parse_criteria(raw, {"photorealism": 15})
        assert criteria[0].score == 15

    def test_clamps_negative_scores(self):
        raw = {"photorealism": -5}
        criteria = _parse_criteria(raw, {"photorealism": 15})
        assert criteria[0].score == 0

    def test_missing_scores_default_to_zero(self):
        raw = {}
        criteria = _parse_criteria(raw, {"photorealism": 15})
        assert criteria[0].score == 0

    def test_non_integer_scores_converted(self):
        raw = {"photorealism": 12.7}
        criteria = _parse_criteria(raw, {"photorealism": 15})
        assert criteria[0].score == 12


# ---------------------------------------------------------------------------
# Rubric completeness
# ---------------------------------------------------------------------------


class TestRubricCompleteness:
    def test_generation_criteria_sum_to_100(self):
        assert sum(_GENERATION_CRITERIA_MAX.values()) == 100

    def test_edit_criteria_sum_to_50(self):
        assert sum(_EDIT_CRITERIA_MAX.values()) == 50

    def test_shopping_criteria_sum_to_30(self):
        assert sum(_SHOPPING_CRITERIA_MAX.values()) == 30

    def test_generation_has_9_criteria(self):
        assert len(_GENERATION_CRITERIA_MAX) == 9

    def test_edit_has_5_criteria(self):
        assert len(_EDIT_CRITERIA_MAX) == 5

    def test_shopping_has_3_criteria(self):
        assert len(_SHOPPING_CRITERIA_MAX) == 3


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


class TestResultDataclasses:
    def test_generation_result(self):
        result = GenerationEvalResult(
            criteria=[CriterionScore("test", 10, 15)],
            total=80,
            tag="GOOD",
            notes="test notes",
        )
        assert result.total == 80
        assert result.tag == "GOOD"
        assert result.fast_eval == {}

    def test_edit_result(self):
        result = EditEvalResult(
            criteria=[CriterionScore("test", 10, 15)],
            total=40,
            tag="GOOD",
        )
        assert result.total == 40
        assert result.fast_eval == {}

    def test_shopping_result(self):
        result = ShoppingVisualEvalResult(
            criteria=[CriterionScore("test", 10, 15)],
            total=25,
            tag="EXCELLENT",
        )
        assert result.total == 25

    def test_criterion_score(self):
        cs = CriterionScore(name="photorealism", score=12, max_score=15, notes="good")
        assert cs.name == "photorealism"
        assert cs.score == 12
        assert cs.notes == "good"


# ---------------------------------------------------------------------------
# Evaluate generation (mocked)
# ---------------------------------------------------------------------------

_MOCK_GENERATION_RESPONSE = {
    "photorealism": 12,
    "style_adherence": 13,
    "color_palette": 8,
    "room_preservation": 18,
    "furniture_scale": 9,
    "lighting": 8,
    "design_coherence": 9,
    "brief_compliance": 4,
    "keep_items": 4,
    "total": 85,
    "notes": "Strong redesign with good style adherence.",
}


def _make_brief() -> DesignBrief:
    return DesignBrief(
        room_type="living room",
        style_profile=StyleProfile(
            mood="cozy modern",
            colors=["warm white", "sage green"],
            textures=["bouclé", "linen"],
        ),
    )


class TestEvaluateGeneration:
    @pytest.mark.asyncio
    async def test_returns_generation_eval_result(self):
        import json

        mock_block = MagicMock()
        mock_block.text = json.dumps(_MOCK_GENERATION_RESPONSE)
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with (
            patch("app.activities.design_eval.os.environ.get", return_value="test-key"),
            patch("app.activities.design_eval.anthropic.AsyncAnthropic", return_value=mock_client),
            patch(
                "app.activities.design_eval._load_image_base64",
                new_callable=AsyncMock,
                return_value="fake_b64",
            ),
        ):
            result = await evaluate_generation(
                "https://example.com/original.jpg",
                "https://example.com/generated.jpg",
                _make_brief(),
            )

        assert isinstance(result, GenerationEvalResult)
        assert result.total == 85
        assert result.tag == "EXCELLENT"
        assert len(result.criteria) == 9

    @pytest.mark.asyncio
    async def test_with_fast_eval(self):
        import json

        mock_block = MagicMock()
        mock_block.text = json.dumps(_MOCK_GENERATION_RESPONSE)
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        from types import SimpleNamespace

        fast_eval = SimpleNamespace(clip_text_score=0.3, composite_score=0.5)

        with (
            patch("app.activities.design_eval.os.environ.get", return_value="test-key"),
            patch("app.activities.design_eval.anthropic.AsyncAnthropic", return_value=mock_client),
            patch(
                "app.activities.design_eval._load_image_base64",
                new_callable=AsyncMock,
                return_value="fake_b64",
            ),
        ):
            result = await evaluate_generation(
                "https://example.com/original.jpg",
                "https://example.com/generated.jpg",
                _make_brief(),
                fast_eval=fast_eval,
            )

        assert result.fast_eval == {"clip_text_score": 0.3, "composite_score": 0.5}


# ---------------------------------------------------------------------------
# Evaluate edit (mocked)
# ---------------------------------------------------------------------------

_MOCK_EDIT_RESPONSE = {
    "edit_fidelity": 13,
    "preservation_fidelity": 12,
    "artifact_cleanliness": 9,
    "seamless_blending": 4,
    "instruction_accuracy": 4,
    "total": 42,
    "notes": "Clean edit with good blending.",
}


class TestEvaluateEdit:
    @pytest.mark.asyncio
    async def test_returns_edit_eval_result(self):
        import json

        mock_block = MagicMock()
        mock_block.text = json.dumps(_MOCK_EDIT_RESPONSE)
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with (
            patch("app.activities.design_eval.os.environ.get", return_value="test-key"),
            patch("app.activities.design_eval.anthropic.AsyncAnthropic", return_value=mock_client),
            patch(
                "app.activities.design_eval._load_image_base64",
                new_callable=AsyncMock,
                return_value="fake_b64",
            ),
        ):
            result = await evaluate_edit(
                "https://example.com/original.jpg",
                "https://example.com/edited.jpg",
                "Change the sofa to navy blue",
            )

        assert isinstance(result, EditEvalResult)
        assert result.total == 42
        assert result.tag == "EXCELLENT"
        assert len(result.criteria) == 5


# ---------------------------------------------------------------------------
# Evaluate shopping visual (mocked)
# ---------------------------------------------------------------------------

_MOCK_SHOPPING_RESPONSE = {
    "visual_match": 12,
    "style_consistency": 8,
    "scale_appropriateness": 4,
    "total": 24,
    "notes": "Good visual match.",
}


class TestEvaluateShoppingVisual:
    @pytest.mark.asyncio
    async def test_returns_shopping_eval_result(self):
        import json

        mock_block = MagicMock()
        mock_block.text = json.dumps(_MOCK_SHOPPING_RESPONSE)
        mock_response = MagicMock()
        mock_response.content = [mock_block]

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)

        with (
            patch("app.activities.design_eval.os.environ.get", return_value="test-key"),
            patch("app.activities.design_eval.anthropic.AsyncAnthropic", return_value=mock_client),
            patch(
                "app.activities.design_eval._load_image_base64",
                new_callable=AsyncMock,
                return_value="fake_b64",
            ),
        ):
            result = await evaluate_shopping_visual(
                "https://example.com/room.jpg",
                "https://example.com/product.jpg",
                "Navy velvet sofa, modern style",
            )

        assert isinstance(result, ShoppingVisualEvalResult)
        assert result.total == 24
        assert result.tag == "GOOD"
        assert len(result.criteria) == 3
