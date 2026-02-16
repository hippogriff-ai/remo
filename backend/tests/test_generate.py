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
        assert "architectural elements" in prompt or "architecture" in prompt

    def test_build_prompt_with_designer_brain_fields(self):
        """PR-6 follow-up: New DesignBrief fields appear in generation prompt."""
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(
            room_type="living room",
            emotional_drivers=["started WFH", "room feels oppressive"],
            usage_patterns="couple WFH Mon-Fri, host dinners monthly",
            renovation_willingness="repaint yes, fixtures maybe, tile no",
            room_analysis_hypothesis="Bright room needing warmth and better storage",
        )
        prompt = _build_generation_prompt(brief, [])
        assert "started WFH" in prompt
        assert "couple WFH Mon-Fri" in prompt
        assert "repaint yes" in prompt
        assert "Bright room needing warmth" in prompt

    def test_build_prompt_without_designer_brain_fields(self):
        """New fields absent when not populated (backward compat)."""
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(room_type="bedroom")
        prompt = _build_generation_prompt(brief, [])
        assert "Emotional drivers" not in prompt
        assert "Usage patterns" not in prompt
        assert "Renovation scope" not in prompt
        assert "Room analysis" not in prompt

    def test_build_prompt_includes_lifestyle(self):
        """Lifestyle field appears separately in generation prompt."""
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(
            room_type="living room",
            occupants="couple, 30s",
            lifestyle="Morning yoga, weekend hosting",
        )
        prompt = _build_generation_prompt(brief, [])
        assert "couple, 30s" in prompt
        assert "Morning yoga, weekend hosting" in prompt

    def test_build_prompt_with_variant(self):
        """Option variant text appears in the generated prompt (A5)."""
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [], option_variant="Test variant direction")
        assert "Test variant direction" in prompt

    def test_build_prompt_without_variant(self):
        """Default option_variant is empty string, producing no extra text."""
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [])
        assert "Design Direction:" not in prompt

    def test_variant_a_and_b_differ(self):
        """Variant A and B produce meaningfully different prompts (A5)."""
        from app.activities.generate import _OPTION_VARIANTS, _build_generation_prompt

        brief = DesignBrief(room_type="living room")
        prompt_a = _build_generation_prompt(brief, [], option_variant=_OPTION_VARIANTS[0])
        prompt_b = _build_generation_prompt(brief, [], option_variant=_OPTION_VARIANTS[1])
        assert prompt_a != prompt_b
        assert "primary style" in prompt_a
        assert "complementary variation" in prompt_b

    def test_prompt_uses_narrative_format(self):
        """A3: Prompt uses narrative paragraphs, not bullet lists."""
        from app.activities.generate import _build_generation_prompt

        prompt = _build_generation_prompt(None, [])
        # v5+: uses specific camera model instead of generic "full-frame camera"
        assert "Canon EOS R5" in prompt or "full-frame camera" in prompt
        assert "physically accurate materials" in prompt


class TestColorPaletteFormatting:
    """Tests for _format_color_palette 60-30-10 proportional hierarchy."""

    def test_single_color(self):
        from app.activities.generate import _format_color_palette

        result = _format_color_palette(["warm ivory"])
        assert "warm ivory" in result
        assert "dominant" in result

    def test_two_colors(self):
        from app.activities.generate import _format_color_palette

        result = _format_color_palette(["warm ivory", "walnut brown"])
        assert "70%" in result
        assert "30%" in result

    def test_three_colors_uses_60_30_10(self):
        from app.activities.generate import _format_color_palette

        result = _format_color_palette(["warm ivory", "walnut brown", "olive green"])
        assert "60%" in result
        assert "30%" in result
        assert "10%" in result
        assert "warm ivory" in result
        assert "walnut brown" in result
        assert "olive green" in result

    def test_four_colors_has_extras(self):
        from app.activities.generate import _format_color_palette

        result = _format_color_palette(["a", "b", "c", "terracotta"])
        assert "Additional accents: terracotta" in result

    def test_color_palette_in_full_prompt(self):
        from app.activities.generate import _build_generation_prompt

        brief = DesignBrief(
            room_type="living room",
            style_profile=StyleProfile(
                colors=["warm ivory", "walnut brown", "olive green"],
            ),
        )
        prompt = _build_generation_prompt(brief, [])
        assert "Color palette (60/30/10)" in prompt
        assert "warm ivory (60%" in prompt


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
        """Explicit empty arrays should not produce OPENINGS/FURNITURE/SURFACES sections."""
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
        assert "FIXED OPENINGS" not in result
        assert "EXISTING FURNITURE" not in result
        assert "SURFACES" not in result
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
        assert "24.4 m²" in result or "24.3 m²" in result
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
            walls=[],
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

    def test_format_furniture_with_dimensions(self):
        """G5: Furniture includes bounding-box dimensions when available."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[
                {"type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8},
                {"type": "table", "width": 1.2},
                {"type": "chair"},  # no dimensions
            ],
        )
        result = _format_room_context(dims)
        assert "sofa" in result
        assert "2.1m" in result
        assert "0.9m" in result
        assert "table" in result
        assert "1.2m" in result
        assert "chair" in result

    def test_format_openings_with_dimensions(self):
        """G5/G13: Openings include width × height when available."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[
                {"type": "door", "width": 0.9, "height": 2.1},
                {"type": "window"},  # no dimensions
            ],
        )
        result = _format_room_context(dims)
        assert "door (0.9m × 2.1m)" in result
        assert "window" in result
        # window without dimensions should not have parens
        assert "window (" not in result

    def test_format_furniture_partial_dimensions(self):
        """G5: Furniture with only some dimension fields formats correctly."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[
                {"type": "lamp", "height": 1.5},  # only height
                {"type": "rug", "width": 2.0, "depth": 3.0},  # no height
            ],
        )
        result = _format_room_context(dims)
        assert "lamp" in result
        assert "1.5m" in result
        assert "rug" in result
        assert "2.0m" in result
        assert "3.0m" in result

    def test_format_non_numeric_dimensions_degrade_gracefully(self):
        """Review fix: Non-numeric dimension values don't crash _format_room_context."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[
                {"type": "door", "width": "wide", "height": 2.1},
                {"type": "window", "width": 1.0, "height": "tall"},
            ],
            furniture=[
                {"type": "sofa", "width": "big", "depth": 0.9, "height": 0.8},
            ],
        )
        result = _format_room_context(dims)
        # Non-numeric values should fall back to type-only (no crash)
        assert "door" in result
        assert "window" in result
        assert "sofa" in result
        # The raw malformed values should NOT appear in opening/furniture descriptions
        # ("wide" appears in "4.0m wide" geometry header, but not as a door dimension)
        assert '"wide"' not in result
        assert "tall" not in result
        assert "big" not in result

    def test_format_surfaces_missing_type_uses_fallback(self):
        """Surface dict missing 'type' key should use 'surface' as fallback."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            surfaces=[{"material": "tile"}],
        )
        result = _format_room_context(dims)
        assert "surface: tile" in result

    def test_format_opening_none_type_uses_fallback(self):
        """G23: Opening with type=None uses 'opening' fallback, not literal 'None'."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[{"type": None, "width": 0.9, "height": 2.1}],
        )
        result = _format_room_context(dims)
        assert "FIXED OPENINGS" in result
        assert "opening (0.9m" in result
        assert "None" not in result

    def test_format_furniture_none_type_uses_fallback(self):
        """G23: Furniture with type=None uses 'item' fallback, not literal 'None'."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": None, "width": 2.0, "depth": 0.9, "height": 0.8}],
        )
        result = _format_room_context(dims)
        assert "item" in result
        assert "2.0m" in result
        assert "None" not in result

    def test_format_surface_none_type_uses_fallback(self):
        """G23: Surface with type=None uses 'surface' fallback, not literal 'None'."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            surfaces=[{"type": None, "material": "hardwood"}],
        )
        result = _format_room_context(dims)
        assert "surface: hardwood" in result
        assert "None" not in result

    def test_format_surface_none_material_uses_fallback(self):
        """G23: Surface with material=None uses 'unknown' fallback."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            surfaces=[{"type": "floor", "material": None}],
        )
        result = _format_room_context(dims)
        assert "floor: unknown" in result
        assert "None" not in result

    def test_format_empty_string_type_uses_fallback(self):
        """Empty string type uses fallback (empty string is falsy in Python)."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[{"type": "", "width": 0.9, "height": 2.1}],
            furniture=[{"type": ""}],
            surfaces=[{"type": "", "material": "tile"}],
        )
        result = _format_room_context(dims)
        # Empty string is falsy, so `"" or "opening"` → "opening"
        assert "opening (0.9m" in result
        assert "item" in result
        assert "surface: tile" in result

    def test_format_floor_area_sqm_none_omits_line(self):
        """When floor_area_sqm is None, floor area line should be omitted."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions.model_construct(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            floor_area_sqm=None,
            openings=[],
            furniture=[],
            surfaces=[],
            walls=[],
        )
        result = _format_room_context(dims)
        assert "4.0m" in result
        assert "Floor area:" not in result

    def test_format_partially_invalid_furniture_dims_drops_all(self):
        """When one furniture dimension is invalid, all dims are dropped (type only).

        The try/except wraps all float() calls, so a valid width followed by
        an invalid depth causes the except to clear dim_parts entirely. This is
        intentional — partial dims could mislead the generation model.
        """
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "table", "width": 2.0, "depth": "bad", "height": 0.8}],
        )
        result = _format_room_context(dims)
        # Type preserved but all dimensions dropped
        assert "table" in result
        assert "2.0m" not in result  # valid width also dropped
        assert "bad" not in result

    def test_format_large_furniture_dims_no_crash(self):
        """Furniture with implausibly large dimensions formats without crash.

        Parser validates room dims (max 50m) but not furniture dims — RoomPlan
        could theoretically report large bounding boxes for misdetected objects.
        """
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "sofa", "width": 100.0, "depth": 0.9, "height": 0.8}],
        )
        result = _format_room_context(dims)
        assert "sofa" in result
        assert "100.0m" in result

    def test_format_mixed_valid_invalid_entries_in_list(self):
        """A list with both valid and invalid entries should format valid ones.

        The per-entry `isinstance(o, dict)` check skips non-dict entries,
        and per-entry try/except isolates malformed individual items.
        Uses model_construct to bypass Pydantic validation (defense-in-depth test).
        """
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions.model_construct(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            floor_area_sqm=20.0,
            walls=[],
            openings=[
                {"type": "door", "width": 0.9, "height": 2.1},  # valid
                "not_a_dict",  # skipped by isinstance check
                {"type": "window", "width": "bad", "height": 1.2},  # invalid dims
            ],
            furniture=[
                42,  # not a dict, skipped
                {"type": "sofa", "width": 2.1, "depth": 0.9, "height": 0.8},  # valid
            ],
            surfaces=[],
        )
        result = _format_room_context(dims)
        assert "door (0.9m × 2.1m)" in result
        assert "window" in result  # type preserved, dims dropped
        assert "sofa" in result
        assert "2.1m" in result

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


class TestAspectRatioDetection:
    """Tests for _detect_aspect_ratio — snaps input image ratio to nearest Gemini-supported value.

    Covers A2: aspect ratio matching so output matches input room photo proportions.
    """

    def test_landscape_4_3(self):
        """Standard 4:3 landscape photo (e.g., 4032x3024 iPhone)."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (4032, 3024))
        assert _detect_aspect_ratio(img) == "4:3"

    def test_landscape_16_9(self):
        """Widescreen 16:9 landscape photo."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (1920, 1080))
        assert _detect_aspect_ratio(img) == "16:9"

    def test_portrait_3_4(self):
        """Portrait mode phone photo (3:4)."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (3024, 4032))
        assert _detect_aspect_ratio(img) == "3:4"

    def test_portrait_9_16(self):
        """Vertical video / portrait 9:16."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (1080, 1920))
        assert _detect_aspect_ratio(img) == "9:16"

    def test_square(self):
        """Square image -> 1:1."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (500, 500))
        assert _detect_aspect_ratio(img) == "1:1"

    def test_near_4_3(self):
        """Slightly off from 4:3 still snaps to 4:3."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (1350, 1000))  # 1.35:1, closest to 4:3 (1.333)
        assert _detect_aspect_ratio(img) == "4:3"

    def test_zero_height(self):
        """Degenerate zero-height image falls back to 1:1."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (100, 0))
        assert _detect_aspect_ratio(img) == "1:1"

    def test_zero_width(self):
        """Degenerate zero-width image falls back to 1:1."""
        from app.activities.generate import _detect_aspect_ratio

        img = Image.new("RGB", (0, 100))
        assert _detect_aspect_ratio(img) == "1:1"


class TestMakeImageConfig:
    """Tests for _make_image_config — per-call config with aspect ratio override.

    Covers A1 (2K resolution) and A2 (aspect ratio matching).
    """

    def test_returns_global_config_when_no_ratio(self):
        """Without aspect_ratio, returns the global IMAGE_CONFIG (2K)."""
        from app.activities.generate import _make_image_config
        from app.utils.gemini_chat import IMAGE_CONFIG

        config = _make_image_config(None)
        assert config is IMAGE_CONFIG

    def test_includes_2k_and_aspect_ratio(self):
        """With aspect_ratio, builds new config with both 2K and ratio."""
        from app.activities.generate import _make_image_config

        config = _make_image_config("16:9")
        assert config.image_config is not None
        assert config.image_config.image_size == "2K"
        assert config.image_config.aspect_ratio == "16:9"

    def test_different_ratios(self):
        """All supported ratios produce valid configs."""
        from app.activities.generate import _make_image_config

        for ratio in ["1:1", "3:4", "4:3", "9:16", "16:9"]:
            config = _make_image_config(ratio)
            assert config.image_config.aspect_ratio == ratio

    def test_invalid_ratio_falls_back_to_global(self):
        """Unsupported ratio falls back to global IMAGE_CONFIG (no crash)."""
        from app.activities.generate import _make_image_config
        from app.utils.gemini_chat import IMAGE_CONFIG

        config = _make_image_config("21:9")
        assert config is IMAGE_CONFIG


class TestGlobalImageConfig:
    """Verify the global IMAGE_CONFIG uses 2K resolution (A1)."""

    def test_image_config_has_2k(self):
        """IMAGE_CONFIG should include 2K image_size for free quality boost."""
        from app.utils.gemini_chat import IMAGE_CONFIG

        assert IMAGE_CONFIG.image_config is not None
        assert IMAGE_CONFIG.image_config.image_size == "2K"


class TestPromptFiles:
    """Verify prompt template files exist and are valid."""

    def test_generation_prompt_exists(self):
        path = Path(__file__).parent.parent / "prompts" / "generation.txt"
        assert path.exists()
        content = path.read_text()
        assert "{brief}" in content
        assert "{room_context}" in content
        assert "{option_variant}" in content
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

    @pytest.mark.asyncio
    async def test_download_network_error_retryable(self):
        """RequestError (connection refused, DNS, etc.) is retryable."""
        import httpx
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError, match="Network error") as exc_info:
                await download_image("https://example.com/unreachable.png")
            assert not exc_info.value.non_retryable
            # Exception chaining preserved for diagnostics
            assert isinstance(exc_info.value.__cause__, httpx.ConnectError)
            # Error message includes URL and exception type name
            assert "unreachable.png" in str(exc_info.value)
            assert "ConnectError" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_download_corrupt_image_non_retryable(self):
        """Corrupt image bytes (valid HTTP 200 + image content-type) is non-retryable."""
        from temporalio.exceptions import ApplicationError

        from app.utils.http import download_image

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"\x89PNG\r\n\x1a\nCORRUPT"
        mock_response.headers = {"content-type": "image/png"}

        with patch("httpx.AsyncClient") as mock_async_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_async_client.return_value = mock_client

            with pytest.raises(ApplicationError, match="corrupt") as exc_info:
                await download_image("https://example.com/broken.png")
            assert exc_info.value.non_retryable
            # Exception chaining preserved — original PIL/decode error accessible
            assert exc_info.value.__cause__ is not None
            # Error message includes URL for traceability
            assert "broken.png" in str(exc_info.value)


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

    def test_upload_returns_storage_key(self):
        """_upload_image returns an R2 key (not presigned URL) for stable storage."""
        from app.activities.generate import _upload_image

        img = _make_test_image()

        with patch("app.utils.r2.upload_object") as mock_upload:
            key = _upload_image(img, "proj-123", "option_0.png")
            assert key == "projects/proj-123/generated/option_0.png"
            assert not key.startswith("http")
            mock_upload.assert_called_once()
            call_args = mock_upload.call_args[0]
            assert call_args[0] == key


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


class TestOrientationToCompass:
    """Tests for _orientation_to_compass — degrees to compass direction."""

    def test_cardinal_south(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(0) == "south"

    def test_cardinal_west(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(90) == "west"

    def test_cardinal_north(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(180) == "north"

    def test_cardinal_east(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(270) == "east"

    def test_intercardinal_southwest(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(45) == "southwest"

    def test_intercardinal_northwest(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(135) == "northwest"

    def test_intercardinal_northeast(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(225) == "northeast"

    def test_intercardinal_southeast(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(315) == "southeast"

    def test_360_wraps_to_south(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(360) == "south"

    def test_negative_wraps(self):
        from app.activities.generate import _orientation_to_compass

        assert _orientation_to_compass(-90) == "east"


class TestStructuredRoomContext:
    """Tests for structured output format with section headers."""

    def test_has_room_geometry_header(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(width_m=4.2, length_m=5.8, height_m=2.7)
        result = _format_room_context(dims)
        assert "ROOM GEOMETRY (LiDAR-measured, precise):" in result

    def test_has_walls_section_with_compass(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.2,
            length_m=5.8,
            height_m=2.7,
            walls=[
                {"id": "wall_0", "width": 4.2, "height": 2.7, "orientation": 0},
                {"id": "wall_1", "width": 5.8, "height": 2.7, "orientation": 90},
            ],
        )
        result = _format_room_context(dims)
        assert "WALLS (2 detected):" in result
        assert "faces south (0°)" in result
        assert "faces west (90°)" in result

    def test_has_fixed_openings_header(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            openings=[{"type": "door", "width": 0.9, "height": 2.1}],
        )
        result = _format_room_context(dims)
        assert "FIXED OPENINGS (do not relocate):" in result

    def test_has_furniture_header(self):
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "sofa", "width": 2.0}],
        )
        result = _format_room_context(dims)
        assert "EXISTING FURNITURE (scale reference" in result

    def test_relative_proportions_for_large_furniture(self):
        """Large furniture (>= 20% of shorter wall) gets relative proportion."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.2,
            length_m=5.8,
            height_m=2.7,
            furniture=[
                {"type": "bathtub", "width": 1.5, "depth": 0.7, "height": 0.6},
            ],
        )
        result = _format_room_context(dims)
        # 1.5 / 4.2 = 35.7% → "spans ~36% of shorter wall"
        assert "spans ~36% of shorter wall" in result

    def test_small_furniture_no_proportion(self):
        """Small furniture (< 20% of shorter wall) gets no proportion label."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.2,
            length_m=5.8,
            height_m=2.7,
            furniture=[
                {"type": "lamp", "width": 0.3, "height": 1.5},
            ],
        )
        result = _format_room_context(dims)
        assert "spans" not in result

    def test_skips_small_furniture(self):
        """Items where all measured dimensions < 0.3m are skipped as noise."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[
                {"type": "tiny_object", "width": 0.1, "depth": 0.1, "height": 0.2},
                {"type": "sofa", "width": 2.0, "depth": 0.9, "height": 0.8},
            ],
        )
        result = _format_room_context(dims)
        assert "tiny_object" not in result
        assert "sofa" in result

    def test_non_numeric_wall_dimensions_degrade_gracefully(self):
        """Non-numeric wall width/height values don't crash _format_room_context."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            walls=[
                {"id": "wall_0", "width": "bad", "height": 2.7, "orientation": 0},
                {"id": "wall_1", "width": 5.0, "height": "bad", "orientation": 90},
            ],
        )
        result = _format_room_context(dims)
        # Wall IDs should appear but malformed dimensions are omitted
        assert "wall_0" in result
        assert "wall_1" in result
        assert "bad" not in result

    def test_caps_furniture_at_15_items(self):
        """Furniture list capped at 15 items to reduce noise."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        furniture = [{"type": f"item_{i}", "width": 1.0} for i in range(20)]
        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=furniture,
        )
        result = _format_room_context(dims)
        assert "item_14" in result
        assert "item_15" not in result

    def test_single_dimension_uses_wide_not_footprint(self):
        """Furniture with only width uses 'wide' label, not 'footprint'."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "shelf", "width": 0.8}],
        )
        result = _format_room_context(dims)
        assert "0.8m wide" in result
        assert "footprint" not in result

    def test_two_dimensions_uses_footprint(self):
        """Furniture with width + depth uses 'footprint' label."""
        from app.activities.generate import _format_room_context
        from app.models.contracts import RoomDimensions

        dims = RoomDimensions(
            width_m=4.0,
            length_m=5.0,
            height_m=2.5,
            furniture=[{"type": "table", "width": 1.2, "depth": 0.6}],
        )
        result = _format_room_context(dims)
        assert "1.2m × 0.6m footprint" in result


class TestStripChangelogLines:
    """Tests for strip_changelog_lines helper (in prompt_versioning)."""

    def test_strips_versioned_changelog(self):
        from app.utils.prompt_versioning import strip_changelog_lines

        text = "[v5: Enhanced quality]\n[v4: Added details]\n\nActual prompt text."
        result = strip_changelog_lines(text)
        assert "[v5:" not in result
        assert "[v4:" not in result
        assert "Actual prompt text." in result

    def test_preserves_non_changelog_brackets(self):
        from app.utils.prompt_versioning import strip_changelog_lines

        text = "[v5: changelog]\n\nKeep this [note] in the prompt."
        result = strip_changelog_lines(text)
        assert "[note]" in result
        assert "[v5:" not in result

    def test_no_leading_blank_lines(self):
        from app.utils.prompt_versioning import strip_changelog_lines

        text = "[v5: changelog]\n[v4: changelog]\n\nFirst real line."
        result = strip_changelog_lines(text)
        assert result.startswith("\n") is False
        assert "First real line." in result

    def test_empty_string_passthrough(self):
        from app.utils.prompt_versioning import strip_changelog_lines

        assert strip_changelog_lines("") == ""

    def test_no_changelogs_passthrough(self):
        from app.utils.prompt_versioning import strip_changelog_lines

        text = "Just a regular prompt.\nWith multiple lines."
        assert strip_changelog_lines(text) == text
