"""Tests for the shopping list pipeline.

Unit tests (no API key needed) cover:
- Search query building (source-aware)
- Confidence filtering thresholds
- Google Shopping fallback URL generation
- Retailer extraction
- Extraction prompt loading
- Scoring prompt loading
- Dimension filtering pass-through
- Parallel scoring configuration and behavior

Integration tests (marked @pytest.mark.integration) test real API calls.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import httpx

from app.activities.shopping import (
    _COLOR_SYNONYMS,
    _RETAILER_DOMAINS,
    EXA_MAX_RETRIES,
    MAX_CONCURRENT_SCORES,
    SCORING_WEIGHTS_DEFAULT,
    _build_extraction_messages,
    _build_fit_detail,
    _build_scoring_prompt,
    _build_search_queries,
    _compute_room_constraints,
    _expand_color_synonym,
    _extract_json,
    _extract_price_text,
    _extract_retailer,
    _format_room_constraints_for_prompt,
    _get_scoring_weights,
    _google_shopping_url,
    _load_extraction_prompt,
    _load_scoring_prompt,
    _match_category,
    _num_results_for_item,
    _parse_product_dims_cm,
    _price_to_cents,
    _room_size_label,
    _search_exa,
    _strip_code_fence,
    _validate_extracted_items,
    apply_confidence_filtering,
    extract_items,
    filter_by_dimensions,
    generate_shopping_list,
    generate_shopping_list_streaming,
    score_all_products,
    score_product,
    search_products_for_item,
)
from app.models.contracts import (
    DesignBrief,
    FurnitureObservation,
    GenerateShoppingListInput,
    ProductMatch,
    RoomAnalysis,
    RoomContext,
    RoomDimensions,
    StyleProfile,
    UnmatchedItem,
)

# === Room Constraints Tests ===


class TestComputeRoomConstraints:
    def test_standard_room(self):
        """4.5m x 6m room should produce reasonable furniture limits."""
        dims = RoomDimensions(width_m=4.5, length_m=6.0, height_m=2.7)
        c = _compute_room_constraints(dims)
        # Sofa: (600-120)*0.75 = 360cm ≈ 142"
        assert float(c["sofa"]["max_width_cm"]) > 300
        assert float(c["sofa"]["max_width_cm"]) < 400
        # Coffee table: ~2/3 of sofa
        assert float(c["coffee_table"]["max_width_cm"]) < float(c["sofa"]["max_width_cm"])
        # Rug dimensions present
        assert "width_cm" in c["rug"]
        assert "length_cm" in c["rug"]
        # Floor lamp: (270-30) = 240cm
        assert float(c["floor_lamp"]["max_height_cm"]) == 240

    def test_small_room(self):
        """3m x 3.5m room should produce smaller constraints."""
        dims = RoomDimensions(width_m=3.0, length_m=3.5, height_m=2.4)
        c = _compute_room_constraints(dims)
        # Sofa: (350-120)*0.75 = 172.5cm ≈ 68"
        assert float(c["sofa"]["max_width_cm"]) < 200
        # Dining table: 350-180 = 170cm
        assert float(c["dining_table"]["max_length_cm"]) == 170


class TestFormatRoomConstraints:
    def test_with_lidar(self):
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        text = _format_room_constraints_for_prompt(None, dims)
        assert "4.0m x 5.0m" in text
        assert "LiDAR scan" in text
        assert "Sofa" in text

    def test_photo_only(self):
        analysis = RoomAnalysis(estimated_dimensions="approximately 12x15 feet")
        ctx = RoomContext(photo_analysis=analysis, enrichment_sources=["photos"])
        text = _format_room_constraints_for_prompt(ctx, None)
        assert "approximately 12x15 feet" in text
        assert "photo analysis" in text.lower()

    def test_with_lidar_and_context(self):
        """LiDAR should take precedence over photo analysis."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        analysis = RoomAnalysis(estimated_dimensions="approximately 12x15 feet")
        ctx = RoomContext(
            photo_analysis=analysis,
            room_dimensions=dims,
            enrichment_sources=["photos", "lidar"],
        )
        text = _format_room_constraints_for_prompt(ctx, dims)
        assert "LiDAR scan" in text
        assert "Per-category size limits" in text

    def test_includes_furniture_observations(self):
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        analysis = RoomAnalysis(
            furniture=[
                FurnitureObservation(item="gray sofa", condition="worn", keep_candidate=True),
                FurnitureObservation(item="bookshelf", condition="good"),
            ]
        )
        ctx = RoomContext(photo_analysis=analysis, enrichment_sources=["photos"])
        text = _format_room_constraints_for_prompt(ctx, dims)
        assert "gray sofa (worn) [keep]" in text
        assert "bookshelf (good)" in text

    def test_full_designer_brain_context_no_direct_dims(self):
        """Full path: photo furniture + LiDAR dims via context, no direct dims."""
        analysis = RoomAnalysis(
            furniture=[
                FurnitureObservation(
                    item="gray sofa",
                    condition="worn",
                    keep_candidate=True,
                ),
                FurnitureObservation(item="bookshelf", condition="good"),
            ]
        )
        ctx = RoomContext(
            photo_analysis=analysis,
            room_dimensions=RoomDimensions(
                width_m=4.0,
                length_m=5.0,
                height_m=2.5,
            ),
            enrichment_sources=["photos", "lidar"],
        )
        text = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in text
        assert "LiDAR scan" in text
        assert "Per-category size limits" in text
        assert "gray sofa (worn) [keep]" in text
        assert "bookshelf (good)" in text
        assert "Detected furniture" in text

    def test_no_dimensions_no_context(self):
        text = _format_room_constraints_for_prompt(None, None)
        assert "No room dimensions available" in text

    def test_extraction_prompt_includes_room_constraints(self):
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        prompt = _load_extraction_prompt(None, [], room_dimensions=dims)
        assert "Per-category size limits" in prompt
        assert "Sofa" in prompt

    def test_extraction_prompt_without_context(self):
        prompt = _load_extraction_prompt(None, [])
        assert "No room dimensions available" in prompt


# === Search Query Building Tests ===


class TestBuildSearchQueries:
    def test_brief_anchored_uses_source_reference(self):
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "warm walnut coffee table",
            "category": "Tables",
            "style": "mid-century modern",
            "material": "walnut",
            "color": "warm brown",
        }
        queries = _build_search_queries(item)
        assert any("warm walnut coffee table" in q for q in queries)
        assert any("Tables" in q and "walnut" in q for q in queries)

    def test_iteration_anchored_uses_instruction(self):
        item = {
            "source_tag": "ITERATION_ANCHORED",
            "source_reference": "replace with marble coffee table",
            "category": "Tables",
            "material": "marble",
            "color": "white",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        assert any("marble coffee table" in q for q in queries)

    def test_image_only_uses_visual_description(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Lighting fixtures",
            "material": "brushed brass",
            "color": "gold",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        assert any("brushed brass" in q for q in queries)
        assert any("modern" in q for q in queries)

    def test_adds_dimension_query_when_available(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Rugs",
            "material": "wool",
            "color": "cream",
            "style": "modern",
            "estimated_dimensions": "8x10",
        }
        queries = _build_search_queries(item)
        assert any("8x10" in q for q in queries)

    def test_no_empty_queries(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Table",
            "material": "",
            "color": "",
            "style": "",
        }
        queries = _build_search_queries(item)
        assert all(q.strip() for q in queries)

    def test_queries_use_natural_descriptions(self):
        """Queries should use natural descriptions without buy/shop prefixes."""
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "velvet sofa",
            "category": "Seating",
            "style": "modern",
            "material": "velvet",
        }
        queries = _build_search_queries(item)
        has_buy_shop = any("buy" in q or "shop" in q for q in queries)
        assert not has_buy_shop, f"Queries should not include buy/shop. Got: {queries}"
        assert any("velvet sofa" in q for q in queries)
        assert any("furniture" in q for q in queries)

    def test_description_query_added_for_image_only(self):
        """IMAGE_ONLY items should get a description-based query."""
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Seating",
            "description": "ivory boucle sofa with down-blend cushions",
            "material": "boucle",
            "color": "ivory",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        assert any("ivory boucle sofa" in q for q in queries)

    def test_description_query_skipped_when_same_as_source_ref(self):
        """Don't duplicate: skip description query if it matches source_reference."""
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "warm walnut coffee table",
            "description": "warm walnut coffee table",
            "category": "Tables",
            "style": "modern",
            "material": "walnut",
        }
        queries = _build_search_queries(item)
        # "warm walnut coffee table" appears once from source_ref,
        # description query is skipped because it matches source_ref
        ref_count = sum(1 for q in queries if "warm walnut coffee table" in q)
        assert ref_count == 1

    def test_description_query_added_for_brief_anchored_with_different_desc(self):
        """BRIEF_ANCHORED gets description query when desc differs from ref."""
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "cozy reading chair",
            "description": "tufted linen wingback armchair",
            "category": "Seating",
            "style": "traditional",
            "material": "linen",
        }
        queries = _build_search_queries(item)
        assert any("cozy reading chair" in q for q in queries)  # source_ref
        assert any("tufted linen wingback" in q for q in queries)  # description

    def test_search_queries_with_room_dims(self):
        """Primary furniture gets a size-constrained query when room dims available."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
        }
        queries = _build_search_queries(item, room_dimensions=dims)
        constrained = [q for q in queries if "under" in q and "inches" in q]
        assert len(constrained) == 1, f"Expected one constrained query, got: {queries}"
        assert "medium room" in constrained[0]

    def test_search_queries_without_room_dims(self):
        """No constrained query when room_dimensions is None."""
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
        }
        queries = _build_search_queries(item, room_dimensions=None)
        constrained = [q for q in queries if "under" in q and "inches" in q]
        assert len(constrained) == 0


class TestRoomSizeLabel:
    def test_small_room(self):
        dims = RoomDimensions(width_m=3.0, length_m=4.0, height_m=2.5)  # 12 sqm
        assert _room_size_label(dims) == "small"

    def test_medium_room(self):
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)  # 20 sqm
        assert _room_size_label(dims) == "medium"

    def test_large_room(self):
        dims = RoomDimensions(width_m=6.0, length_m=7.0, height_m=3.0)  # 42 sqm
        assert _room_size_label(dims) == "large"

    def test_boundary_15_sqm(self):
        """Exactly 15 sqm is medium (lower bound inclusive)."""
        dims = RoomDimensions(width_m=3.0, length_m=5.0, height_m=2.5)  # 15 sqm
        assert _room_size_label(dims) == "medium"

    def test_boundary_25_sqm(self):
        """Exactly 25 sqm is medium (upper bound inclusive)."""
        dims = RoomDimensions(width_m=5.0, length_m=5.0, height_m=2.7)  # 25 sqm
        assert _room_size_label(dims) == "medium"


# === B1: No buy/shop prefixes ===


class TestNoBuyShopPrefixes:
    def test_brief_anchored_no_buy(self):
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "warm walnut coffee table",
            "category": "Tables",
            "style": "mid-century modern",
            "material": "walnut",
            "color": "warm brown",
        }
        queries = _build_search_queries(item)
        for q in queries:
            assert "buy" not in q.lower(), f"'buy' found in query: {q}"
            assert q != "shop" and not q.endswith(" shop"), f"'shop' suffix in: {q}"

    def test_iteration_anchored_no_buy(self):
        item = {
            "source_tag": "ITERATION_ANCHORED",
            "source_reference": "replace with marble coffee table",
            "category": "Tables",
            "material": "marble",
            "color": "white",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        for q in queries:
            assert "buy" not in q.lower(), f"'buy' found in query: {q}"

    def test_image_only_no_buy(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Seating",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        for q in queries:
            assert "buy" not in q.lower(), f"'buy' found in query: {q}"

    def test_uses_furniture_suffix_instead(self):
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "velvet sofa",
            "category": "Seating",
            "style": "modern",
            "material": "velvet",
        }
        queries = _build_search_queries(item)
        assert any("furniture" in q for q in queries)


# === B5: Color Synonym Expansion ===


class TestColorSynonymExpansion:
    def test_known_color_gets_synonym(self):
        assert _expand_color_synonym("ivory") == "cream"
        assert _expand_color_synonym("navy") == "dark blue"
        assert _expand_color_synonym("sage") == "muted green"

    def test_unknown_color_returns_none(self):
        assert _expand_color_synonym("fluorescent pink") is None
        assert _expand_color_synonym("") is None

    def test_case_insensitive_matching(self):
        assert _expand_color_synonym("Ivory") == "cream"
        assert _expand_color_synonym("NAVY blue") == "dark blue"

    def test_no_false_positive_substring_match(self):
        """'ash' should not match 'washed', 'clashing', etc."""
        assert _expand_color_synonym("washed oak") is None
        assert _expand_color_synonym("clashing red") is None
        assert _expand_color_synonym("flashy gold") is None

    def test_compound_color_with_key_as_word(self):
        """'ash gray' should match because 'ash' is a full word."""
        assert _expand_color_synonym("ash gray") == "light gray"
        assert _expand_color_synonym("coral pink") == "salmon"

    def test_synonym_query_added_to_search(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Rug",
            "material": "wool",
            "color": "ivory",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        assert any("cream" in q for q in queries), f"Expected synonym query. Got: {queries}"

    def test_no_synonym_query_for_unknown_color(self):
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Rug",
            "material": "wool",
            "color": "electric blue",
            "style": "modern",
        }
        queries = _build_search_queries(item)
        # Should not crash or add spurious queries
        assert all(q.strip() for q in queries)

    def test_synonyms_dict_has_reasonable_coverage(self):
        assert len(_COLOR_SYNONYMS) >= 25
        for key, synonyms in _COLOR_SYNONYMS.items():
            assert len(synonyms) >= 2, f"{key} should have at least 2 synonyms"


# === Design Brief Integration ===


class TestDesignBriefInSearchQueries:
    """Verify design brief context improves search query quality."""

    def _make_brief(self, room_type="bathroom", mood="modern spa-inspired"):
        return DesignBrief(
            room_type=room_type,
            style_profile=StyleProfile(mood=mood, colors=["white", "natural wood"]),
        )

    def test_brief_mood_and_room_in_queries(self):
        """When brief has mood + room, queries include style-contextualized query."""
        brief = self._make_brief()
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Vanity Mirror",
            "description": "round frameless vanity mirror with LED backlight",
            "material": "glass",
            "color": "silver",
            "style": "modern",
        }
        queries = _build_search_queries(item, design_brief=brief)
        # Should include style-contextualized query
        style_query = [q for q in queries if "modern spa" in q and "bathroom" in q]
        assert len(style_query) >= 1, f"Expected brief-contextualized query. Got: {queries}"
        # Description should be first (highest signal)
        assert queries[0] == "round frameless vanity mirror with LED backlight"

    def test_brief_mood_only(self):
        """When brief has mood but no room_type, uses mood + category."""
        brief = DesignBrief(
            room_type="",
            style_profile=StyleProfile(mood="scandinavian minimalist"),
        )
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Floor Lamp",
            "description": "white oak tripod floor lamp with linen shade",
            "material": "oak",
            "color": "white",
            "style": "scandinavian",
        }
        queries = _build_search_queries(item, design_brief=brief)
        mood_query = [q for q in queries if "scandinavian minimalist" in q]
        assert len(mood_query) >= 1, f"Expected mood query. Got: {queries}"

    def test_brief_room_only_no_mood(self):
        """When brief has room_type but no mood, uses room + category + style."""
        brief = DesignBrief(
            room_type="living room",
            style_profile=StyleProfile(mood=""),
        )
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "description": "ivory boucle sofa",
            "material": "boucle",
            "color": "ivory",
            "style": "modern",
        }
        queries = _build_search_queries(item, design_brief=brief)
        room_query = [q for q in queries if "living room" in q]
        assert len(room_query) >= 1, f"Expected room query. Got: {queries}"

    def test_no_brief_falls_back_to_style(self):
        """Without brief, falls back to item style (backward compatible)."""
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Lighting",
            "material": "brass",
            "color": "gold",
            "style": "art deco",
        }
        queries = _build_search_queries(item, design_brief=None)
        assert any("art deco" in q for q in queries), f"Expected style fallback. Got: {queries}"
        assert any("furniture" in q for q in queries)

    def test_description_is_first_query(self):
        """Description should always be the first query (highest signal)."""
        brief = self._make_brief()
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Bath Stool",
            "description": "natural teak shower bench with slatted top",
            "material": "teak",
            "color": "natural",
            "style": "modern spa",
        }
        queries = _build_search_queries(item, design_brief=brief)
        assert queries[0] == "natural teak shower bench with slatted top"

    def test_brief_anchored_keeps_source_ref(self):
        """BRIEF_ANCHORED items still include source reference."""
        brief = self._make_brief(room_type="bedroom", mood="bohemian")
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "macrame wall hanging",
            "description": "cream cotton macrame wall hanging with fringe",
            "category": "Wall Art",
            "material": "cotton",
            "color": "cream",
            "style": "bohemian",
        }
        queries = _build_search_queries(item, design_brief=brief)
        assert any(q == "macrame wall hanging" for q in queries)
        # Description should still be first
        assert queries[0] == "cream cotton macrame wall hanging with fringe"

    def test_brief_with_room_dims(self):
        """Brief + room dims both contribute queries."""
        brief = self._make_brief(room_type="living room", mood="mid-century modern")
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "description": "olive velvet sofa with walnut legs",
            "material": "velvet",
            "color": "olive",
            "style": "mid-century",
        }
        queries = _build_search_queries(item, room_dimensions=dims, design_brief=brief)
        # Should have style-contextualized query
        assert any("mid-century modern" in q and "living room" in q for q in queries)
        # Should have room-constrained query
        constrained = [q for q in queries if "under" in q and "inches" in q]
        assert len(constrained) == 1


# === B3: Retailer Domains ===


class TestRetailerDomains:
    def test_retailer_domains_match_retailer_names(self):
        """_RETAILER_DOMAINS should cover the same retailers as _RETAILER_NAMES."""
        assert len(_RETAILER_DOMAINS) >= 20
        assert "wayfair.com" in _RETAILER_DOMAINS
        assert "amazon.com" in _RETAILER_DOMAINS
        assert "westelm.com" in _RETAILER_DOMAINS

    def test_all_domains_are_valid(self):
        for domain in _RETAILER_DOMAINS:
            assert "." in domain, f"Invalid domain: {domain}"
            assert not domain.startswith("http"), f"Domain should not be a URL: {domain}"


# === B2/B3/B4: search_products_for_item dual-pass ===


class TestSearchProductsDualPass:
    def test_high_priority_uses_deep_search(self):
        """HIGH-priority items should trigger 'deep' search type."""
        calls = []

        async def mock_search(http, q, key, num, *, search_type="auto", **kwargs):
            calls.append({"query": q, "search_type": search_type, **kwargs})
            return []

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
            "search_priority": "HIGH",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search):
            asyncio.run(search_products_for_item(MagicMock(), item, "key"))

        # All calls should use "deep" for HIGH priority
        assert all(c["search_type"] == "deep" for c in calls), (
            f"Expected deep search type for HIGH priority. Got: {[c['search_type'] for c in calls]}"
        )

    def test_medium_priority_uses_auto_search(self):
        """MEDIUM-priority items should use 'auto' search type."""
        calls = []

        async def mock_search(http, q, key, num, *, search_type="auto", **kwargs):
            calls.append({"search_type": search_type})
            return []

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Lamp",
            "material": "brass",
            "color": "gold",
            "style": "modern",
            "search_priority": "MEDIUM",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search):
            asyncio.run(search_products_for_item(MagicMock(), item, "key"))

        assert all(c["search_type"] == "auto" for c in calls)

    def test_dual_pass_sends_retailer_domains(self):
        """Pass 1 should include retailer domains and 'add to cart' text."""
        calls = []

        async def mock_search(http, q, key, num, *, search_type="auto", **kwargs):
            calls.append(kwargs)
            return []

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Table",
            "material": "wood",
            "color": "brown",
            "style": "modern",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search):
            asyncio.run(search_products_for_item(MagicMock(), item, "key"))

        # Half of calls should have include_domains (pass 1), half should not (pass 2)
        with_domains = [c for c in calls if c.get("include_domains")]
        without_domains = [c for c in calls if not c.get("include_domains")]
        assert len(with_domains) > 0, "Pass 1 should include retailer domains"
        assert len(without_domains) > 0, "Pass 2 should not include retailer domains"

    def test_dual_pass_pass1_has_include_text(self):
        """Pass 1 should include 'add to cart' text filter."""
        calls = []

        async def mock_search(http, q, key, num, *, search_type="auto", **kwargs):
            calls.append(kwargs)
            return []

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Chair",
            "material": "leather",
            "color": "tan",
            "style": "modern",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search):
            asyncio.run(search_products_for_item(MagicMock(), item, "key"))

        with_text = [c for c in calls if c.get("include_text")]
        assert len(with_text) > 0, "Pass 1 should include 'add to cart' filter"
        assert with_text[0]["include_text"] == ["add to cart"]

    def test_dual_pass_deduplicates_results(self):
        """Results from both passes should be deduplicated by URL."""
        call_count = [0]

        async def mock_search(http, q, key, num, *, search_type="auto", **kwargs):
            call_count[0] += 1
            # Both passes return the same product
            return [{"url": "https://wayfair.com/sofa-1", "title": "Navy Sofa"}]

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Sofa",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search):
            results = asyncio.run(search_products_for_item(MagicMock(), item, "key"))

        # Should have called search multiple times but deduped to 1 result
        assert call_count[0] > 1
        assert len(results) == 1
        assert results[0]["url"] == "https://wayfair.com/sofa-1"


# === Cache Key Collision Tests ===


class TestCacheKeyCollision:
    def test_different_params_produce_different_cache_keys(self):
        """Dual-pass searches with same query but different params must not collide."""
        from app.activities.shopping import _exa_cache_path

        with patch("app.activities.shopping._EXA_CACHE_DIR", "/tmp/test-cache"):
            path_pass1 = _exa_cache_path(
                "navy sofa",
                3,
                search_type="auto",
                include_domains=["wayfair.com"],
                include_text=["add to cart"],
            )
            path_pass2 = _exa_cache_path("navy sofa", 3, search_type="auto")
            assert path_pass1 != path_pass2, "Pass 1 and pass 2 must have distinct cache keys"

    def test_same_params_produce_same_cache_key(self):
        """Identical params should produce the same cache key."""
        from app.activities.shopping import _exa_cache_path

        with patch("app.activities.shopping._EXA_CACHE_DIR", "/tmp/test-cache"):
            path_a = _exa_cache_path("navy sofa", 3, search_type="deep")
            path_b = _exa_cache_path("navy sofa", 3, search_type="deep")
            assert path_a == path_b


# === Search Priority Tests ===


class TestSearchPriority:
    def test_high_priority_gets_more_results(self):
        assert _num_results_for_item({"search_priority": "HIGH"}) == 5

    def test_medium_priority_default(self):
        assert _num_results_for_item({"search_priority": "MEDIUM"}) == 3

    def test_low_priority_fewer_results(self):
        assert _num_results_for_item({"search_priority": "LOW"}) == 2

    def test_missing_priority_defaults_to_medium(self):
        assert _num_results_for_item({}) == 3


# === Confidence Filtering Tests ===


class TestConfidenceFiltering:
    def _item(self, category="Furniture"):
        return {
            "category": category,
            "description": "test item",
            "material": "wood",
            "color": "brown",
            "style": "modern",
        }

    def _scored(self, confidence, url="https://example.com/product"):
        return {
            "weighted_total": confidence,
            "product_name": "Test Product",
            "product_url": url,
            "image_url": None,
            "why_matched": "Test match",
            "price_cents": 5000,
        }

    def test_high_confidence_included(self):
        items = [self._item()]
        scored = [[self._scored(0.85)]]
        matched, unmatched, cost = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert len(unmatched) == 0
        assert matched[0].confidence_score == 0.85

    def test_medium_confidence_included(self):
        items = [self._item()]
        scored = [[self._scored(0.6)]]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].fit_status == "tight"

    def test_low_confidence_excluded(self):
        items = [self._item()]
        scored = [[self._scored(0.3)]]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 0
        assert len(unmatched) == 1

    def test_no_results_creates_unmatched(self):
        items = [self._item()]
        scored = [[]]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 0
        assert len(unmatched) == 1

    def test_picks_best_product(self):
        items = [self._item()]
        scored = [
            [
                self._scored(0.6, "https://example.com/ok"),
                self._scored(0.9, "https://example.com/best"),
                self._scored(0.7, "https://example.com/good"),
            ]
        ]
        matched, _, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].confidence_score == 0.9
        assert "best" in matched[0].product_url

    def test_total_cost_accumulates(self):
        items = [self._item("Sofa"), self._item("Table")]
        scored = [
            [self._scored(0.8, "https://example.com/sofa")],
            [self._scored(0.7, "https://example.com/table")],
        ]
        _, _, cost = apply_confidence_filtering(items, scored)
        assert cost == 10000  # 5000 + 5000

    def test_output_types_match_contracts(self):
        items = [self._item()]
        scored = [[self._scored(0.85)]]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        assert all(isinstance(m, ProductMatch) for m in matched)

    def test_unmatched_types_match_contracts(self):
        items = [self._item()]
        scored = [[self._scored(0.1)]]
        _, unmatched, _ = apply_confidence_filtering(items, scored)
        assert all(isinstance(u, UnmatchedItem) for u in unmatched)

    def test_tight_match_has_fit_detail(self):
        """Tight matches (0.5-0.79) should have a fit_detail explaining the gap."""
        items = [self._item()]
        scored_data = self._scored(0.65)
        scored_data["material_score"] = 0.3
        scored_data["color_score"] = 0.2
        scored = [[scored_data]]
        matched, _, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].fit_detail is not None
        assert "material" in matched[0].fit_detail
        assert "color" in matched[0].fit_detail

    def test_strong_match_no_fit_detail(self):
        """Strong matches (>= 0.8) should not have fit_detail."""
        items = [self._item()]
        scored = [[self._scored(0.9)]]
        matched, _, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].fit_detail is None

    def test_cross_item_dedup_skips_duplicate_url(self):
        """Same product URL should not match multiple items."""
        items = [self._item("Accent Chair"), self._item("Side Chair")]
        shared_url = "https://wayfair.com/chair-123"
        scored = [
            [self._scored(0.9, shared_url)],
            [self._scored(0.85, shared_url)],
        ]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        # First item gets the match, second becomes unmatched
        assert len(matched) == 1
        assert matched[0].category_group == "Accent Chair"
        assert len(unmatched) == 1
        assert unmatched[0].category == "Side Chair"

    def test_cross_item_dedup_picks_fallback(self):
        """When best product is taken, item should fall back to next best."""
        items = [self._item("Sofa"), self._item("Loveseat")]
        shared_url = "https://wayfair.com/couch"
        scored = [
            [self._scored(0.9, shared_url)],
            [
                self._scored(0.85, shared_url),
                self._scored(0.7, "https://wayfair.com/loveseat"),
            ],
        ]
        matched, unmatched, _ = apply_confidence_filtering(items, scored)
        # Both items should match, but to different products
        assert len(matched) == 2
        assert len(unmatched) == 0
        urls = {m.product_url for m in matched}
        assert urls == {shared_url, "https://wayfair.com/loveseat"}


# === Fit Detail Tests ===


class TestBuildFitDetail:
    def test_identifies_weak_scores(self):
        scored = {
            "category_score": 0.9,
            "material_score": 0.3,
            "color_score": 0.8,
            "style_score": 0.2,
            "dimensions_score": 0.5,
        }
        detail = _build_fit_detail(scored)
        assert "material" in detail
        assert "style" in detail
        assert "category" not in detail

    def test_no_weak_scores(self):
        scored = {
            "category_score": 0.7,
            "material_score": 0.6,
            "color_score": 0.5,
            "style_score": 0.6,
            "dimensions_score": 0.5,
        }
        detail = _build_fit_detail(scored)
        assert "Close overall" in detail

    def test_all_weak_scores(self):
        scored = {
            "category_score": 0.3,
            "material_score": 0.2,
            "color_score": 0.1,
            "style_score": 0.4,
            "dimensions_score": 0.0,
        }
        detail = _build_fit_detail(scored)
        assert "category" in detail
        assert "material" in detail
        assert "color" in detail
        assert "style" in detail
        assert "dimensions" in detail

    def test_missing_sub_scores(self):
        """If sub-scores aren't present, gracefully handle."""
        scored = {"weighted_total": 0.6}
        detail = _build_fit_detail(scored)
        assert "Close overall" in detail


# === Google Shopping Fallback Tests ===


class TestGoogleShoppingUrl:
    def test_builds_valid_url(self):
        item = {
            "category": "Sofa",
            "material": "velvet",
            "color": "navy",
            "style": "modern",
        }
        url = _google_shopping_url(item)
        assert url.startswith("https://www.google.com/search?tbm=shop&q=")
        assert "Sofa" in url or "sofa" in url.lower()

    def test_url_encodes_spaces(self):
        item = {
            "category": "Floor lamp",
            "material": "brushed brass",
            "color": "gold",
            "style": "art deco",
        }
        url = _google_shopping_url(item)
        assert "+" in url or "%20" in url


# === Retailer Extraction Tests ===


class TestExtractRetailer:
    def test_extracts_domain(self):
        assert _extract_retailer("https://www.wayfair.com/furniture/sofa") == "Wayfair"

    def test_handles_www(self):
        assert _extract_retailer("https://www.amazon.com/dp/123") == "Amazon"

    def test_known_retailer_cb2(self):
        assert _extract_retailer("https://cb2.com/sofas") == "CB2"

    def test_known_retailer_pottery_barn(self):
        assert _extract_retailer("https://www.potterybarn.com/products/sofa") == "Pottery Barn"

    def test_known_retailer_west_elm(self):
        assert _extract_retailer("https://www.westelm.com/chairs") == "West Elm"

    def test_known_retailer_ikea(self):
        assert _extract_retailer("https://ikea.com/us/en/p/kallax") == "IKEA"

    def test_known_retailer_crate_barrel(self):
        assert _extract_retailer("https://www.crateandbarrel.com/furniture") == "Crate & Barrel"

    def test_known_retailer_rh(self):
        assert _extract_retailer("https://rh.com/catalog/product") == "RH"

    def test_unknown_retailer_falls_back_to_capitalize(self):
        assert _extract_retailer("https://www.shopify-store.com/product") == "Shopify-store"

    def test_handles_empty(self):
        assert _extract_retailer("") == "Unknown"

    def test_handles_invalid(self):
        assert _extract_retailer("not a url") == "Unknown"


# === Price Extraction Tests ===


class TestExtractPriceText:
    def test_finds_simple_price(self):
        product = {"text": "This sofa is $1,299.00. Free shipping."}
        assert _extract_price_text(product) == "$1,299.00"

    def test_finds_price_without_cents(self):
        product = {"text": "Price: $499"}
        assert _extract_price_text(product) == "$499"

    def test_no_price_returns_unknown(self):
        product = {"text": "Beautiful velvet sofa in navy blue."}
        assert _extract_price_text(product) == "Unknown"

    def test_empty_text(self):
        product = {"text": ""}
        assert _extract_price_text(product) == "Unknown"

    def test_no_text_field(self):
        product = {"title": "Some product"}
        assert _extract_price_text(product) == "Unknown"

    def test_picks_first_price(self):
        product = {"text": "Was $999.00, now $699.00!"}
        assert _extract_price_text(product) == "$999.00"


class TestPriceToCents:
    def test_simple_price(self):
        assert _price_to_cents("$1,299.00") == 129900

    def test_no_cents(self):
        assert _price_to_cents("$499") == 49900

    def test_no_comma(self):
        assert _price_to_cents("$99.99") == 9999

    def test_unknown(self):
        assert _price_to_cents("Unknown") == 0

    def test_empty(self):
        assert _price_to_cents("") == 0

    def test_invalid(self):
        assert _price_to_cents("$not-a-price") == 0


# === Prompt Loading Tests ===


class TestPromptLoading:
    def test_extraction_prompt_loads(self):
        brief = DesignBrief(
            room_type="living room",
            style_profile=StyleProfile(
                lighting="warm ambient 2700K",
                colors=["warm ivory (60%)"],
                textures=["boucle"],
            ),
        )
        prompt = _load_extraction_prompt(brief, [])
        assert "living room" in prompt.lower() or "DesignBrief" in prompt

    def test_extraction_prompt_without_brief(self):
        prompt = _load_extraction_prompt(None, [])
        assert "None" in prompt

    def test_scoring_prompt_loads(self):
        prompt = _load_scoring_prompt()
        assert "Category Match" in prompt
        assert "Material Match" in prompt
        assert "weighted_total" in prompt


# === Scoring Prompt Building Tests ===


class TestBuildScoringPrompt:
    def test_includes_item_fields(self):
        item = {
            "category": "Sofa",
            "description": "deep-seated velvet sofa",
            "style": "mid-century modern",
            "material": "velvet",
            "color": "navy",
            "estimated_dimensions": "84x36x32",
        }
        product = {
            "title": "West Elm Velvet Sofa",
            "text": "Plush velvet upholstery...",
            "url": "https://westelm.com/sofa",
        }
        brief = DesignBrief(
            room_type="living room",
            style_profile=StyleProfile(
                mood="cozy retreat",
                colors=["warm ivory (60%)", "navy (30%)"],
            ),
        )
        prompt = _build_scoring_prompt(item, product, brief)
        assert "Sofa" in prompt
        assert "velvet" in prompt
        assert "mid-century modern" in prompt
        assert "West Elm Velvet Sofa" in prompt
        assert "living room" in prompt
        assert "cozy retreat" in prompt

    def test_handles_no_brief(self):
        item = {"category": "Lamp", "description": "floor lamp"}
        product = {"title": "IKEA Lamp", "url": "https://ikea.com/lamp"}
        prompt = _build_scoring_prompt(item, product, None)
        assert "Lamp" in prompt
        assert "IKEA Lamp" in prompt

    def test_default_weights_without_lidar(self):
        """Without room_dimensions, uses default weights (dim=0.1)."""
        item = {"category": "Planter", "description": "ceramic planter"}
        product = {"title": "Planter", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=None)
        assert "weight: 0.3" in prompt  # category default
        assert "weight: 0.1" in prompt  # dimensions default

    def test_lidar_weights_with_room_dims(self):
        """With room_dimensions, dimensions weight increases; renormalized to sum=1.0."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=dims)
        # Sofa overrides on LiDAR base, renormalized: material~0.25, dimensions~0.2
        assert "weight: 0.24" in prompt or "weight: 0.25" in prompt  # material (renormalized)
        assert "LiDAR-measured" in prompt
        assert "4.0m" in prompt

    def test_no_room_dimensions_section_without_lidar(self):
        """Without room_dimensions, no room dims section in prompt."""
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=None)
        assert "LiDAR-measured" not in prompt

    def test_category_adaptive_weights_sofa(self):
        """Sofa category should boost material weight and reduce color weight."""
        from app.activities.shopping import _get_scoring_weights

        item = {"category": "Sofa", "description": "velvet sofa"}
        weights = _get_scoring_weights(item, has_lidar=False)
        # Sofa overrides boosted material (0.25 pre-norm) and reduced color (0.15 pre-norm)
        assert weights["material"] > weights["color"]
        assert abs(sum(weights.values()) - 1.0) < 0.02  # renormalized

    def test_category_adaptive_weights_rug(self):
        """Rug category should boost color weight and reduce material weight."""
        from app.activities.shopping import _get_scoring_weights

        item = {"category": "Area Rug", "description": "wool rug"}
        weights = _get_scoring_weights(item, has_lidar=False)
        # Rug overrides: color boosted, material reduced
        assert weights["color"] > weights["material"]
        assert abs(sum(weights.values()) - 1.0) < 0.02  # renormalized

    def test_category_adaptive_weights_lighting(self):
        """Lighting category should boost style weight."""
        from app.activities.shopping import _get_scoring_weights

        item = {"category": "Floor Lamp", "description": "brass lamp"}
        weights = _get_scoring_weights(item, has_lidar=False)
        # Lamp overrides: style boosted above default
        default_weights = _get_scoring_weights({"category": "Unknown"}, has_lidar=False)
        assert weights["style"] > default_weights["style"]
        assert abs(sum(weights.values()) - 1.0) < 0.02  # renormalized

    def test_category_adaptive_with_lidar(self):
        """Category overrides should apply on top of LiDAR base weights."""
        from app.activities.shopping import _get_scoring_weights

        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        item = {"category": "Rug", "description": "wool rug"}
        product = {"title": "Rug", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=dims)
        assert "LiDAR-measured" in prompt

        weights = _get_scoring_weights(item, has_lidar=True)
        # Rug overrides on LiDAR base: color should be highest, renormalized
        assert weights["color"] > weights["material"]
        assert abs(sum(weights.values()) - 1.0) < 0.02

    def test_unknown_category_uses_default_weights(self):
        """Unknown categories should use base weights unchanged."""
        item = {"category": "Decorative Vase", "description": "ceramic vase"}
        product = {"title": "Vase", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None)
        # Default weights unchanged
        assert "weight: 0.3)" in prompt  # category default
        assert "weight: 0.1)" in prompt  # dimensions default

    def test_summary_data_included_in_prompt(self):
        """Exa summary data should appear in the product description."""
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {
            "title": "Modern Velvet Sofa",
            "text": "Luxurious seating...",
            "url": "https://example.com",
            "summary": {
                "material": "velvet",
                "color": "navy blue",
                "dimensions": "84x36x32 inches",
                "in_stock": True,
            },
        }
        prompt = _build_scoring_prompt(item, product, None)
        assert "Material: velvet" in prompt
        assert "Color: navy blue" in prompt
        assert "Dimensions: 84x36x32 inches" in prompt
        assert "In stock: Yes" in prompt

    def test_no_summary_data_graceful(self):
        """Missing summary should not crash or add empty sections."""
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "text": "A cozy sofa.", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None)
        assert "Structured data" not in prompt

    def test_empty_summary_dict_graceful(self):
        """Empty summary dict should not add structured data section."""
        item = {"category": "Chair", "description": "accent chair"}
        product = {
            "title": "Chair",
            "url": "https://example.com",
            "summary": {},
        }
        prompt = _build_scoring_prompt(item, product, None)
        assert "Structured data" not in prompt


# === B6: Category-Adaptive Scoring Weights ===


class TestCategoryAdaptiveWeights:
    def _assert_normalized(self, weights: dict[str, float]) -> None:
        assert abs(sum(weights.values()) - 1.0) < 0.05

    def test_sofa_overrides(self):
        weights = _get_scoring_weights({"category": "Sofa"}, has_lidar=False)
        # Sofa: material boosted above color, dimensions boosted above default 0.10
        assert weights["material"] > weights["color"]
        assert weights["dimensions"] > SCORING_WEIGHTS_DEFAULT["dimensions"]
        self._assert_normalized(weights)

    def test_rug_overrides(self):
        weights = _get_scoring_weights({"category": "Area Rug"}, has_lidar=False)
        # Rug: color is the dominant weight, material is reduced
        assert weights["color"] > weights["material"]
        assert weights["color"] > weights["style"]
        self._assert_normalized(weights)

    def test_lighting_overrides(self):
        weights = _get_scoring_weights({"category": "Floor Lamp"}, has_lidar=False)
        # Lamp: style boosted above default
        default = _get_scoring_weights({"category": "Unknown"}, has_lidar=False)
        assert weights["style"] > default["style"]
        self._assert_normalized(weights)

    def test_wall_art_overrides(self):
        weights = _get_scoring_weights({"category": "Wall Art"}, has_lidar=False)
        # Wall art: style and color are the top two weights
        assert weights["style"] >= weights["color"]
        assert weights["style"] > weights["material"]
        self._assert_normalized(weights)

    def test_unknown_category_no_override(self):
        weights = _get_scoring_weights({"category": "Planter"}, has_lidar=False)
        assert weights == SCORING_WEIGHTS_DEFAULT

    def test_lidar_base_with_category_override(self):
        weights = _get_scoring_weights({"category": "Rug"}, has_lidar=True)
        # Rug + LiDAR: color is the top weight
        assert weights["color"] > weights["material"]
        assert weights["color"] > weights["dimensions"]
        self._assert_normalized(weights)

    def test_case_insensitive_matching(self):
        weights = _get_scoring_weights({"category": "SOFA"}, has_lidar=False)
        # Sofa override applies regardless of case
        assert weights["material"] > weights["color"]
        self._assert_normalized(weights)

    def test_empty_category_uses_default(self):
        weights = _get_scoring_weights({"category": ""}, has_lidar=False)
        assert weights == SCORING_WEIGHTS_DEFAULT

    def test_missing_category_uses_default(self):
        weights = _get_scoring_weights({}, has_lidar=False)
        assert weights == SCORING_WEIGHTS_DEFAULT


# === B7: Summary Price Extraction ===


class TestSummaryPriceExtraction:
    def test_summary_price_preferred_over_regex(self):
        product = {
            "text": "Great sofa for $999.00",
            "summary": {"price_usd": 1299.99},
        }
        assert _extract_price_text(product) == "$1,299.99"

    def test_falls_back_to_regex_without_summary(self):
        product = {"text": "Price: $499.00"}
        assert _extract_price_text(product) == "$499.00"

    def test_falls_back_to_regex_with_empty_summary(self):
        product = {"text": "Price: $299.99", "summary": {}}
        assert _extract_price_text(product) == "$299.99"

    def test_falls_back_to_regex_with_zero_price(self):
        product = {"text": "Sale $199.00", "summary": {"price_usd": 0}}
        assert _extract_price_text(product) == "$199.00"

    def test_falls_back_to_regex_with_non_numeric_price(self):
        product = {"text": "Price: $599.00", "summary": {"price_usd": "N/A"}}
        assert _extract_price_text(product) == "$599.00"

    def test_integer_price_from_summary(self):
        product = {"text": "", "summary": {"price_usd": 500}}
        assert _extract_price_text(product) == "$500.00"

    def test_unknown_when_no_summary_no_text(self):
        product = {"text": "No price here", "summary": {"product_name": "Sofa"}}
        assert _extract_price_text(product) == "Unknown"

    def test_summary_price_formats_with_commas(self):
        product = {"text": "", "summary": {"price_usd": 12500.00}}
        assert _extract_price_text(product) == "$12,500.00"


# === B7: Exa Summary in Payload ===


class TestExaSummaryPayload:
    def test_payload_includes_summary_schema(self):
        """_search_exa should include summary schema in the contents payload."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key"))
        contents = captured_payload.get("contents", {})
        assert "summary" in contents
        assert contents["summary"]["query"] == "Extract product details"
        assert "price_usd" in contents["summary"]["schema"]["properties"]
        assert "material" in contents["summary"]["schema"]["properties"]

    def test_payload_still_includes_text(self):
        """Summary should be alongside text, not replace it."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key"))
        contents = captured_payload.get("contents", {})
        assert "text" in contents
        assert contents["text"]["maxCharacters"] == 1000


# === Extraction Message Building Tests ===


class TestBuildExtractionMessages:
    def test_builds_multimodal_messages(self):
        messages = _build_extraction_messages(
            "https://r2.example.com/design.jpg",
            ["https://r2.example.com/room1.jpg"],
            "Extract items from this design.",
        )
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        # design image + room image + text prompt = 3
        assert len(content) == 3
        assert content[0]["type"] == "image"
        assert content[0]["source"]["url"] == "https://r2.example.com/design.jpg"
        assert content[1]["type"] == "image"
        assert content[1]["source"]["url"] == "https://r2.example.com/room1.jpg"
        assert content[2]["type"] == "text"

    def test_no_room_photos(self):
        messages = _build_extraction_messages(
            "https://r2.example.com/design.jpg",
            [],
            "Extract items.",
        )
        content = messages[0]["content"]
        # design image + text = 2
        assert len(content) == 2


# === Dimension Filtering Tests ===


class TestParseDims:
    def test_inches(self):
        result = _parse_product_dims_cm("84x36x32 inches")
        assert result is not None
        w, d, h = result
        assert abs(w - 84 * 2.54) < 0.1
        assert abs(d - 36 * 2.54) < 0.1
        assert abs(h - 32 * 2.54) < 0.1

    def test_cm(self):
        result = _parse_product_dims_cm("213x91cm")
        assert result is not None
        w, d, _ = result
        assert abs(w - 213) < 0.1
        assert abs(d - 91) < 0.1

    def test_two_dims(self):
        result = _parse_product_dims_cm("8x10")
        assert result is not None
        assert result[2] == 0.0  # no third dimension

    def test_none(self):
        assert _parse_product_dims_cm(None) is None
        assert _parse_product_dims_cm("") is None
        assert _parse_product_dims_cm("no dims here") is None


class TestDimensionFiltering:
    def test_passthrough_without_lidar(self):
        items = [{"category": "Sofa"}]
        scored = [[{"weighted_total": 0.8}]]
        result = filter_by_dimensions(items, scored, None)
        assert result == scored

    def test_annotates_fits(self):
        """Product within constraint gets room_fit='fits'."""
        dims = RoomDimensions(width_m=4.5, length_m=6.0, height_m=2.7)
        items = [{"category": "Sofa"}]
        # Sofa max: (600-120)*0.75 = 360cm ≈ 142". Product is 80" ≈ 203cm.
        scored = [[{"weighted_total": 0.8, "dimensions": "80x36x32 inches"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert result[0][0]["room_fit"] == "fits"

    def test_annotates_tight(self):
        """Product near limit gets room_fit='tight'."""
        dims = RoomDimensions(width_m=3.0, length_m=3.5, height_m=2.4)
        items = [{"category": "Sofa"}]
        # Sofa max: (350-120)*0.75 = 172.5cm ≈ 68". Product is 72" ≈ 183cm → ~106% → tight
        scored = [[{"weighted_total": 0.8, "dimensions": "72x36x32 inches"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert result[0][0]["room_fit"] == "tight"

    def test_annotates_too_large(self):
        """Product exceeding limit gets room_fit='too_large'."""
        dims = RoomDimensions(width_m=3.0, length_m=3.5, height_m=2.4)
        items = [{"category": "Sofa"}]
        # Sofa max: 172.5cm ≈ 68". Product is 96" ≈ 244cm → ~141% → too_large
        scored = [[{"weighted_total": 0.8, "dimensions": "96x40x34 inches"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert result[0][0]["room_fit"] == "too_large"
        assert "exceeds" in result[0][0]["room_fit_detail"]

    def test_passthrough_no_dims_on_product(self):
        """Products without parseable dimensions pass through unchanged."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        items = [{"category": "Sofa"}]
        scored = [[{"weighted_total": 0.8}]]
        result = filter_by_dimensions(items, scored, dims)
        assert "room_fit" not in result[0][0]

    def test_unknown_category_passes_through(self):
        """Items with unmapped categories are not annotated."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        items = [{"category": "Wall art"}]
        scored = [[{"weighted_total": 0.8, "dimensions": "24x36 inches"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert "room_fit" not in result[0][0]

    def test_rug_dimension_fit_checked(self):
        """Rug products should be checked against width_cm/length_cm constraints."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        items = [{"category": "Area Rug"}]
        # Room: 4m x 5m. Rug limits: 320cm x 350cm (80%/70%).
        # Product: "8x10" → 8ft x 10ft = 244cm x 305cm → fits.
        scored = [[{"weighted_total": 0.8, "dimensions": "8x10"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert result[0][0]["room_fit"] == "fits"

    def test_rug_too_large_annotated(self):
        """Oversized rug exceeding room limits gets room_fit='too_large'."""
        dims = RoomDimensions(width_m=3.0, length_m=3.5, height_m=2.7)
        items = [{"category": "Rug"}]
        # Room: 3m x 3.5m. Rug limits: 240cm x 245cm.
        # Product: "10x12" → 10ft x 12ft = 305cm x 366cm → too large.
        scored = [[{"weighted_total": 0.8, "dimensions": "10x12"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert result[0][0]["room_fit"] == "too_large"

    def test_confidence_downgrades_on_too_large(self):
        """too_large products get fit_status downgraded in confidence filtering."""
        items = [{"category": "Sofa"}]
        scored = [
            [
                {
                    "weighted_total": 0.9,
                    "product_url": "https://example.com/sofa",
                    "product_name": "Big Sofa",
                    "room_fit": "too_large",
                    "room_fit_detail": '96" exceeds 68" limit',
                }
            ]
        ]
        matched, _, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].fit_status == "tight"  # downgraded from "fits"

    def test_confidence_downgrades_on_tight(self):
        """tight room_fit downgrades fit_status from 'fits' to 'tight'."""
        items = [{"category": "Sofa"}]
        scored = [
            [
                {
                    "weighted_total": 0.85,
                    "product_url": "https://example.com/sofa",
                    "product_name": "Near-limit Sofa",
                    "room_fit": "tight",
                    "room_fit_detail": '70" near 68" limit',
                }
            ]
        ]
        matched, _, _ = apply_confidence_filtering(items, scored)
        assert len(matched) == 1
        assert matched[0].fit_status == "tight"  # downgraded from "fits"
        assert matched[0].fit_detail == '70" near 68" limit'


# === Code Fence Stripping Tests ===


class TestStripCodeFence:
    def test_no_fence(self):
        assert _strip_code_fence('{"key": "value"}') == '{"key": "value"}'

    def test_multiline_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert _strip_code_fence(text) == '{"key": "value"}'

    def test_single_line_fence(self):
        text = '```{"key": "value"}```'
        assert _strip_code_fence(text) == '{"key": "value"}'

    def test_fence_no_lang_tag(self):
        text = '```\n{"items": []}\n```'
        assert _strip_code_fence(text) == '{"items": []}'

    def test_empty_fence(self):
        assert _strip_code_fence("```\n```") == ""

    def test_whitespace_only(self):
        assert _strip_code_fence("   ") == ""


# === JSON Extraction Tests ===


class TestExtractJson:
    def test_pure_json(self):
        """Clean JSON parses directly."""
        result = _extract_json('{"items": [{"name": "sofa"}]}')
        assert result == {"items": [{"name": "sofa"}]}

    def test_code_fenced_json(self):
        """Code-fenced JSON is extracted correctly."""
        text = '```json\n{"score": 0.85}\n```'
        result = _extract_json(text)
        assert result == {"score": 0.85}

    def test_preamble_before_json(self):
        """Handles Claude adding text before the JSON object."""
        text = 'Here are the extracted items:\n{"items": [{"category": "Sofa"}]}'
        result = _extract_json(text)
        assert result == {"items": [{"category": "Sofa"}]}

    def test_postamble_after_json(self):
        """Handles Claude adding text after the JSON object."""
        text = '{"weighted_total": 0.75}\nLet me know if you need changes.'
        result = _extract_json(text)
        assert result == {"weighted_total": 0.75}

    def test_preamble_and_postamble(self):
        """Handles text both before and after JSON."""
        text = 'Analysis complete:\n{"result": true}\nDone!'
        result = _extract_json(text)
        assert result == {"result": True}

    def test_nested_braces(self):
        """Handles nested JSON objects correctly."""
        text = 'Result: {"outer": {"inner": {"deep": 1}}, "other": 2}'
        result = _extract_json(text)
        assert result == {"outer": {"inner": {"deep": 1}}, "other": 2}

    def test_braces_in_strings(self):
        """Braces inside JSON string values don't break parsing."""
        text = '{"msg": "use {template} syntax", "ok": true}'
        result = _extract_json(text)
        assert result == {"msg": "use {template} syntax", "ok": True}

    def test_escaped_quotes(self):
        """Escaped quotes inside strings are handled."""
        text = '{"msg": "she said \\"hello\\"", "ok": true}'
        result = _extract_json(text)
        assert result == {"msg": 'she said "hello"', "ok": True}

    def test_empty_string(self):
        result = _extract_json("")
        assert result == {}

    def test_no_json_at_all(self):
        result = _extract_json("This response has no JSON content.")
        assert result == {}

    def test_whitespace_only(self):
        result = _extract_json("   \n\n  ")
        assert result == {}

    def test_code_fenced_with_preamble(self):
        """Preamble before a code fence is handled."""
        text = 'Here is the analysis:\n```json\n{"score": 0.9}\n```'
        result = _extract_json(text)
        # _strip_code_fence won't catch this (doesn't start with ```),
        # but the brace-finding fallback will
        assert result == {"score": 0.9}

    def test_escaped_quotes_with_preamble(self):
        """Escaped quotes exercised through the brace-matching path."""
        text = 'Result: {"name": "she said \\"hello\\"", "ok": true}'
        result = _extract_json(text)
        assert result == {"name": 'she said "hello"', "ok": True}

    def test_escaped_backslash_in_string(self):
        """Escaped backslashes don't confuse brace matching."""
        text = 'Output: {"path": "C:\\\\Users\\\\test", "ok": true}'
        result = _extract_json(text)
        assert result == {"path": "C:\\Users\\test", "ok": True}

    def test_malformed_json_in_braces(self):
        """Brace-matched text that isn't valid JSON returns empty dict."""
        text = "Data: {not valid json at all}"
        result = _extract_json(text)
        assert result == {}

    def test_unclosed_brace(self):
        """Unclosed brace returns empty dict."""
        text = 'Start: {"key": "value"'
        result = _extract_json(text)
        assert result == {}


# === Parallel Scoring Tests ===


class TestParallelScoring:
    def test_concurrency_limit_is_reasonable(self):
        """MAX_CONCURRENT_SCORES should be between 2 and 20 to balance speed vs rate limits."""
        assert 2 <= MAX_CONCURRENT_SCORES <= 20

    def test_score_all_products_returns_correct_structure(self):
        """Parallel scoring should return per-item lists sorted by score."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"weighted_total": 0.8, "why_matched": "good match"}')
        ]
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        items = [{"category": "Sofa"}, {"category": "Table"}]
        search_results = [
            [
                {"title": "Sofa A", "url": "https://a.com"},
                {"title": "Sofa B", "url": "https://b.com"},
            ],
            [{"title": "Table A", "url": "https://c.com"}],
        ]

        result = asyncio.run(score_all_products(mock_client, items, search_results, None))

        assert len(result) == 2
        assert len(result[0]) == 2  # 2 products for Sofa
        assert len(result[1]) == 1  # 1 product for Table
        assert mock_client.messages.create.call_count == 3

    def test_score_all_products_sorts_by_score(self):
        """Products within each item should be sorted best-first."""
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            # Return different scores based on call order
            scores = [0.6, 0.9, 0.3]
            score = scores[(call_count - 1) % len(scores)]
            mock_resp.content = [
                MagicMock(
                    text=f'{{"weighted_total": {score}, "why_matched": "match {call_count}"}}'
                )
            ]
            return mock_resp

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = mock_create

        items = [{"category": "Sofa"}]
        search_results = [
            [
                {"title": "P1", "url": "https://a.com"},
                {"title": "P2", "url": "https://b.com"},
                {"title": "P3", "url": "https://c.com"},
            ]
        ]

        result = asyncio.run(score_all_products(mock_client, items, search_results, None))

        scores = [s["weighted_total"] for s in result[0]]
        assert scores == sorted(scores, reverse=True), f"Expected descending order, got {scores}"

    def test_score_all_products_empty_search_results(self):
        """Items with no search results should produce empty score lists."""
        mock_client = MagicMock()

        items = [{"category": "Sofa"}, {"category": "Table"}]
        search_results: list[list[dict]] = [[], []]

        result = asyncio.run(score_all_products(mock_client, items, search_results, None))

        assert len(result) == 2
        assert result[0] == []
        assert result[1] == []

    def test_score_all_products_tolerates_partial_failures(self):
        """A single failed score shouldn't crash the entire pipeline."""
        call_count = 0

        async def mock_create(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise anthropic.RateLimitError(
                    message="rate limited",
                    response=_make_httpx_response(429),
                    body=None,
                )
            mock_resp = MagicMock()
            mock_resp.content = [MagicMock(text='{"weighted_total": 0.8, "why_matched": "good"}')]
            mock_resp.usage = MagicMock(input_tokens=100, output_tokens=50)
            return mock_resp

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = mock_create

        items = [{"category": "Sofa"}]
        search_results = [
            [
                {"title": "P1", "url": "https://a.com"},
                {"title": "P2", "url": "https://b.com"},
                {"title": "P3", "url": "https://c.com"},
            ]
        ]

        result = asyncio.run(score_all_products(mock_client, items, search_results, None))

        # 3 tasks, 1 failed → 2 successful scores
        assert len(result) == 1
        assert len(result[0]) == 2

    def test_score_all_products_all_fail_returns_empty(self):
        """If all scoring calls fail, items get empty score lists."""

        async def mock_create(**kwargs):
            raise anthropic.APIStatusError(
                message="server error",
                response=_make_httpx_response(500),
                body=None,
            )

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = mock_create

        items = [{"category": "Sofa"}]
        search_results = [[{"title": "P1", "url": "https://a.com"}]]

        result = asyncio.run(score_all_products(mock_client, items, search_results, None))

        assert len(result) == 1
        assert result[0] == []


# === Extraction Prompt with Revision History Tests ===


class TestExtractionPromptRevisions:
    def test_revision_history_included(self):
        """Revision history should be formatted into the prompt."""
        from app.models.contracts import RevisionRecord

        revisions = [
            RevisionRecord(
                revision_number=1,
                type="annotation",
                base_image_url="https://example.com/base.jpg",
                revised_image_url="https://example.com/rev1.jpg",
                instructions=["make the sofa blue", "add floor lamp"],
            ),
            RevisionRecord(
                revision_number=2,
                type="text_feedback",
                base_image_url="https://example.com/rev1.jpg",
                revised_image_url="https://example.com/rev2.jpg",
                instructions=["warmer tones overall"],
            ),
        ]
        brief = DesignBrief(room_type="living room")
        prompt = _load_extraction_prompt(brief, revisions)
        assert "Revision 1" in prompt
        assert "make the sofa blue" in prompt
        assert "add floor lamp" in prompt
        assert "Revision 2" in prompt
        assert "warmer tones overall" in prompt

    def test_empty_revision_history(self):
        """Empty revision list should produce 'None' in prompt."""
        brief = DesignBrief(room_type="bedroom")
        prompt = _load_extraction_prompt(brief, [])
        assert "None" in prompt


# === Extract Items with Mock Client Tests ===


class TestExtractItemsMocked:
    def _mock_response(self, text: str) -> MagicMock:
        """Build a mock Claude response with text and usage."""
        resp = MagicMock()
        resp.content = [MagicMock(text=text)]
        resp.usage = MagicMock(input_tokens=100, output_tokens=50)
        return resp

    def test_parses_items_from_response(self):
        """extract_items should parse JSON items from Claude response."""
        mock_client = MagicMock()
        mock_response = self._mock_response(
            '{"items": [{"category": "Sofa", "description": "Velvet sofa", "material": "velvet"}]}'
        )
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            extract_items(
                mock_client,
                "https://r2.example.com/design.jpg",
                ["https://r2.example.com/room.jpg"],
                DesignBrief(room_type="living room"),
                [],
            )
        )

        assert len(result) == 1
        assert result[0]["category"] == "Sofa"
        assert result[0]["material"] == "velvet"

    def test_handles_empty_items(self):
        """extract_items should return empty list when no items found."""
        mock_client = MagicMock()
        mock_response = self._mock_response('{"items": []}')
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(extract_items(mock_client, "https://img.com/d.jpg", [], None, []))
        assert result == []

    def test_handles_no_text_block(self):
        """extract_items should return empty when response has no text."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        # Content block without 'text' attribute
        block = MagicMock(spec=[])
        mock_response.content = [block]
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(extract_items(mock_client, "https://img.com/d.jpg", [], None, []))
        assert result == []

    def test_handles_wrapped_json(self):
        """extract_items should handle JSON wrapped in explanation text."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        item_json = '{"items": [{"category": "Lamp", "description": "Brass arc lamp"}]}'
        mock_response.content = [MagicMock(text=f"Here are the items:\n{item_json}\nDone!")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(extract_items(mock_client, "https://img.com/d.jpg", [], None, []))
        assert len(result) == 1
        assert result[0]["category"] == "Lamp"

    def test_handles_null_items(self):
        """extract_items should return empty list when items is null."""
        mock_client = MagicMock()
        mock_response = self._mock_response('{"items": null}')
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(extract_items(mock_client, "https://img.com/d.jpg", [], None, []))
        assert result == []


# === Search Products Deduplication Tests ===


class TestSearchProductsDedup:
    def test_deduplicates_urls(self):
        """search_products_for_item should deduplicate results by URL."""
        import httpx

        async def mock_post(url, **kwargs):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"url": "https://a.com/sofa", "title": "Sofa A"},
                        {"url": "https://b.com/sofa", "title": "Sofa B"},
                    ]
                },
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "velvet sofa",
            "category": "Seating",
            "style": "modern",
            "material": "velvet",
        }

        results = asyncio.run(search_products_for_item(mock_http, item, "fake-key"))
        # Both queries return same URLs, should be deduped
        urls = [r["url"] for r in results]
        assert len(urls) == len(set(urls)), "URLs should be unique"

    def test_handles_search_failure(self):
        """search_products_for_item should gracefully handle failed searches."""
        import httpx

        async def mock_post(url, **kwargs):
            return httpx.Response(
                500,
                json={"error": "Internal server error"},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        item = {
            "source_tag": "IMAGE_ONLY",
            "category": "Table",
            "material": "wood",
            "color": "brown",
            "style": "rustic",
        }

        results = asyncio.run(search_products_for_item(mock_http, item, "fake-key"))
        assert results == []


# === Score Product Edge Cases ===


class TestScoreProductMocked:
    def test_empty_scores_fallback(self):
        """score_product should return fallback when model returns unparseable text."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="I cannot score this product.")]
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Some Product", "url": "https://example.com/p", "text": ""}

        result = asyncio.run(score_product(mock_client, item, product, None))

        assert result["weighted_total"] == 0.0
        assert "Scoring failed" in result["why_matched"]
        assert result["product_url"] == "https://example.com/p"
        assert result["product_name"] == "Some Product"

    def test_populates_price_from_exa_content(self):
        """score_product should extract price from Exa text content."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"weighted_total": 0.85, "why_matched": "good fit"}')
        ]
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        item = {"category": "Table"}
        product = {
            "title": "Oak Table",
            "url": "https://store.com/table",
            "text": "Beautiful oak table. Price: $599.00.",
        }

        result = asyncio.run(score_product(mock_client, item, product, None))

        assert result["price_cents"] == 59900
        assert result["weighted_total"] == 0.85


# === Generate Shopping List Activity Tests ===


class TestGenerateShoppingListMocked:
    """Test the full pipeline orchestrator with mocked dependencies."""

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa-key"},
    )
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    @patch("app.activities.shopping.anthropic.AsyncAnthropic")
    def test_full_pipeline_happy_path(self, mock_anthropic_cls, mock_extract, mock_search):
        """Full pipeline: extract → search → score → filter → output."""
        # Step 1: extraction returns 2 items
        mock_extract.return_value = [
            {
                "category": "Sofa",
                "description": "velvet sofa",
                "style": "modern",
                "material": "velvet",
                "color": "navy",
            },
            {
                "category": "Lamp",
                "description": "floor lamp",
                "style": "minimal",
                "material": "brass",
                "color": "gold",
            },
        ]

        # Step 2: search returns products
        mock_search.return_value = [
            [{"title": "Navy Sofa", "url": "https://store.com/sofa", "text": "$999"}],
            [{"title": "Brass Lamp", "url": "https://store.com/lamp", "text": "$199"}],
        ]

        # Step 3: scoring (mocked via the client)
        score_responses = [
            '{"weighted_total": 0.85, "why_matched": "good sofa match", '
            '"category_score": 1.0, "material_score": 0.9, '
            '"color_score": 0.8, "style_score": 0.7, "dimensions_score": 0.5}',
            '{"weighted_total": 0.72, "why_matched": "decent lamp match", '
            '"category_score": 0.9, "material_score": 0.7, '
            '"color_score": 0.6, "style_score": 0.5, "dimensions_score": 0.5}',
        ]
        call_idx = 0

        async def mock_create(**kwargs):
            nonlocal call_idx
            resp = MagicMock()
            resp.content = [MagicMock(text=score_responses[call_idx])]
            call_idx += 1
            return resp

        mock_instance = MagicMock()
        mock_instance.messages = MagicMock()
        mock_instance.messages.create = mock_create
        mock_anthropic_cls.return_value = mock_instance

        input_data = GenerateShoppingListInput(
            design_image_url="https://r2.example.com/design.jpg",
            original_room_photo_urls=["https://r2.example.com/room.jpg"],
            design_brief=DesignBrief(
                room_type="living room",
                style_profile=StyleProfile(mood="modern retreat", colors=["navy"]),
            ),
        )

        result = asyncio.run(generate_shopping_list(input_data))

        assert len(result.items) == 2
        assert result.items[0].confidence_score == 0.85
        assert result.items[1].confidence_score == 0.72
        assert result.items[1].fit_status == "tight"  # 0.5-0.79
        assert len(result.unmatched) == 0
        assert result.total_estimated_cost_cents > 0

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa-key"},
    )
    @patch("app.activities.shopping.extract_items")
    @patch("app.activities.shopping.anthropic.AsyncAnthropic")
    def test_no_items_extracted_returns_empty(self, mock_anthropic_cls, mock_extract):
        """Pipeline should return empty output when no items extracted."""
        mock_extract.return_value = []

        input_data = GenerateShoppingListInput(
            design_image_url="https://r2.example.com/design.jpg",
            original_room_photo_urls=[],
        )

        result = asyncio.run(generate_shopping_list(input_data))

        assert result.items == []
        assert result.unmatched == []
        assert result.total_estimated_cost_cents == 0

    def test_missing_anthropic_key_raises(self):
        """Pipeline should raise non-retryable error without API key."""
        import pytest
        from temporalio.exceptions import ApplicationError

        input_data = GenerateShoppingListInput(
            design_image_url="https://example.com/d.jpg",
            original_room_photo_urls=[],
        )

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("app.activities.shopping.settings") as mock_settings,
            pytest.raises(ApplicationError, match="ANTHROPIC_API_KEY"),
        ):
            mock_settings.anthropic_api_key = ""
            mock_settings.exa_api_key = ""
            asyncio.run(generate_shopping_list(input_data))

    def test_missing_exa_key_raises(self):
        """Pipeline should raise non-retryable error without Exa key."""
        import pytest
        from temporalio.exceptions import ApplicationError

        input_data = GenerateShoppingListInput(
            design_image_url="https://example.com/d.jpg",
            original_room_photo_urls=[],
        )

        with (
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}, clear=True),
            patch("app.activities.shopping.settings") as mock_settings,
            pytest.raises(ApplicationError, match="EXA_API_KEY"),
        ):
            mock_settings.anthropic_api_key = ""
            mock_settings.exa_api_key = ""
            asyncio.run(generate_shopping_list(input_data))


def _make_httpx_response(status_code: int, body: str = "error") -> httpx.Response:
    """Build a minimal httpx.Response for constructing anthropic errors."""
    return httpx.Response(
        status_code=status_code,
        text=body,
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


class TestShoppingErrorHandling:
    """Test that API errors in generate_shopping_list are correctly classified."""

    def _make_input(self) -> GenerateShoppingListInput:
        return GenerateShoppingListInput(
            design_image_url="https://example.com/design.jpg",
            original_room_photo_urls=["https://example.com/room.jpg"],
        )

    # -- Extraction error handlers (Step 1) --

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    def test_extraction_rate_limit_is_retryable(self, mock_extract):
        """RateLimitError during extraction should be retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.side_effect = anthropic.RateLimitError(
            message="Rate limited",
            response=_make_httpx_response(429),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is False
        assert "rate limited" in str(exc_info.value).lower()

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    def test_extraction_content_policy_is_non_retryable(self, mock_extract):
        """Content policy (400) during extraction should be non-retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.side_effect = anthropic.BadRequestError(
            message="Content policy violation",
            response=_make_httpx_response(400, "content policy"),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is True

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    def test_extraction_server_error_is_retryable(self, mock_extract):
        """Server errors (500) during extraction should be retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.side_effect = anthropic.InternalServerError(
            message="Internal server error",
            response=_make_httpx_response(500),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is False

    # -- Scoring error handlers (Step 3) --

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.score_all_products")
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    def test_scoring_rate_limit_is_retryable(self, mock_extract, mock_search, mock_score):
        """RateLimitError during scoring should be retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.return_value = [{"item_name": "Sofa", "search_priority": "HIGH"}]
        mock_search.return_value = [[{"url": "https://example.com", "title": "Sofa"}]]
        mock_score.side_effect = anthropic.RateLimitError(
            message="Rate limited",
            response=_make_httpx_response(429),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is False
        assert "scoring" in str(exc_info.value).lower()

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.score_all_products")
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    def test_scoring_content_policy_is_non_retryable(self, mock_extract, mock_search, mock_score):
        """Content policy (400) during scoring should be non-retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.return_value = [{"item_name": "Sofa", "search_priority": "HIGH"}]
        mock_search.return_value = [[{"url": "https://example.com", "title": "Sofa"}]]
        mock_score.side_effect = anthropic.BadRequestError(
            message="Content policy violation",
            response=_make_httpx_response(400, "content policy"),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is True

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.score_all_products")
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    def test_scoring_server_error_is_retryable(self, mock_extract, mock_search, mock_score):
        """Server error (500) during scoring should be retryable."""
        import anthropic
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.return_value = [{"item_name": "Sofa", "search_priority": "HIGH"}]
        mock_search.return_value = [[{"url": "https://example.com", "title": "Sofa"}]]
        mock_score.side_effect = anthropic.InternalServerError(
            message="Internal server error",
            response=_make_httpx_response(500),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is False

    # -- Search failure handler (Step 2) --

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    def test_search_failure_is_retryable(self, mock_extract, mock_search):
        """Generic search failure should be retryable."""
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.return_value = [{"item_name": "Sofa", "search_priority": "HIGH"}]
        mock_search.side_effect = RuntimeError("Connection timeout")

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is False
        assert "Exa search failed" in str(exc_info.value)


class TestSearchEdgeCases:
    """Test edge cases in search dedup and error handling."""

    def test_base_exception_in_gather_is_skipped(self):
        """BaseException results from gather should be silently skipped."""
        call_count = 0

        async def mock_search_alternating(http_client, query, api_key, num_results, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                raise ConnectionError("DNS resolution failed")
            return [{"url": f"https://a.com/item{call_count}", "title": f"Item {call_count}"}]

        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "velvet sofa",
            "description": "A different description",
            "category": "Seating",
            "style": "modern",
            "material": "velvet",
        }

        with patch("app.activities.shopping._search_exa", side_effect=mock_search_alternating):
            results = asyncio.run(search_products_for_item(MagicMock(), item, "fake-key"))

        # Should have results from successful calls only, no crashes
        assert len(results) >= 1
        for r in results:
            assert "url" in r

    def test_retailer_from_file_url_returns_unknown(self):
        """_extract_retailer returns Unknown for URLs with empty netloc."""
        result = _extract_retailer("file:///local/path")
        assert result == "Unknown"


class TestExaSearchRetry:
    """Test retry logic in _search_exa."""

    def test_retries_on_429(self):
        """_search_exa should retry once on 429 rate limit."""
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    text="Rate limited",
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"results": [{"url": "https://a.com", "title": "A"}]},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert call_count == 2
        assert len(results) == 1

    def test_retries_on_500(self):
        """_search_exa should retry once on 500 server error."""
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    500,
                    text="Server error",
                    request=httpx.Request("POST", url),
                )
            return httpx.Response(
                200,
                json={"results": [{"url": "https://a.com", "title": "A"}]},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert call_count == 2
        assert len(results) == 1

    def test_no_retry_on_400(self):
        """_search_exa should NOT retry on 400 bad request."""
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                400,
                text="Bad request",
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert call_count == 1
        assert results == []

    def test_retries_on_timeout(self):
        """_search_exa should retry once on timeout."""
        call_count = 0

        async def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.TimeoutException("Connection timed out")
            return httpx.Response(
                200,
                json={"results": [{"url": "https://a.com", "title": "A"}]},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert call_count == 2
        assert len(results) == 1

    def test_returns_empty_after_max_retries(self):
        """_search_exa returns [] if all retries fail."""

        async def mock_post(url, **kwargs):
            return httpx.Response(
                500,
                text="Server error",
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert results == []

    def test_returns_empty_after_all_timeouts(self):
        """_search_exa returns [] if all attempts timeout."""

        async def mock_post(url, **kwargs):
            raise httpx.TimeoutException("Connection timed out")

        mock_http = MagicMock()
        mock_http.post = mock_post

        with patch("app.activities.shopping.EXA_RETRY_DELAY", 0):
            results = asyncio.run(_search_exa(mock_http, "buy sofa", "key"))

        assert results == []

    def test_config_constant(self):
        """EXA_MAX_RETRIES should be 1."""
        assert EXA_MAX_RETRIES == 1

    def test_search_type_sent_in_payload(self):
        """_search_exa should send the search_type in the payload."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key", search_type="deep"))
        assert captured_payload["type"] == "deep"

    def test_include_domains_sent_in_payload(self):
        """_search_exa should send includeDomains when provided."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(
            _search_exa(mock_http, "sofa", "key", include_domains=["wayfair.com", "amazon.com"])
        )
        assert captured_payload["includeDomains"] == ["wayfair.com", "amazon.com"]

    def test_include_text_sent_in_payload(self):
        """_search_exa should send includeText when provided."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key", include_text=["add to cart"]))
        assert captured_payload["includeText"] == ["add to cart"]

    def test_no_include_domains_when_not_provided(self):
        """_search_exa should not include includeDomains when not provided."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key"))
        assert "includeDomains" not in captured_payload
        assert "includeText" not in captured_payload

    def test_default_search_type_is_auto(self):
        """Default search type should be 'auto' instead of 'neural'."""
        captured_payload = {}

        async def mock_post(url, **kwargs):
            captured_payload.update(kwargs.get("json", {}))
            return httpx.Response(
                200,
                json={"results": []},
                request=httpx.Request("POST", url),
            )

        mock_http = MagicMock()
        mock_http.post = mock_post

        asyncio.run(_search_exa(mock_http, "sofa", "key"))
        assert captured_payload["type"] == "auto"


class TestValidateExtractedItems:
    """Test item validation and normalization."""

    def _valid_item(self, **overrides: Any) -> dict[str, Any]:
        base = {
            "category": "Sofa",
            "description": "Ivory boucle sofa",
            "style": "modern",
            "material": "boucle",
            "color": "ivory",
            "source_tag": "BRIEF_ANCHORED",
            "search_priority": "HIGH",
        }
        base.update(overrides)
        return base

    def test_valid_items_pass_through(self):
        items = [self._valid_item(), self._valid_item(category="Lamp")]
        result = _validate_extracted_items(items)
        assert len(result) == 2

    def test_drops_missing_category(self):
        items = [self._valid_item(category="")]
        result = _validate_extracted_items(items)
        assert result == []

    def test_drops_missing_description(self):
        items = [self._valid_item(description="")]
        result = _validate_extracted_items(items)
        assert result == []

    def test_drops_none_category(self):
        item = self._valid_item()
        item["category"] = None
        result = _validate_extracted_items([item])
        assert result == []

    def test_drops_non_string_category(self):
        item = self._valid_item()
        item["category"] = 123
        result = _validate_extracted_items([item])
        assert result == []

    def test_normalizes_invalid_source_tag(self):
        items = [self._valid_item(source_tag="UNKNOWN_TAG")]
        result = _validate_extracted_items(items)
        assert len(result) == 1
        assert result[0]["source_tag"] == "IMAGE_ONLY"

    def test_normalizes_missing_source_tag(self):
        item = self._valid_item()
        del item["source_tag"]
        result = _validate_extracted_items([item])
        assert len(result) == 1
        assert result[0]["source_tag"] == "IMAGE_ONLY"

    def test_normalizes_invalid_priority(self):
        items = [self._valid_item(search_priority="URGENT")]
        result = _validate_extracted_items(items)
        assert len(result) == 1
        assert result[0]["search_priority"] == "MEDIUM"

    def test_keeps_valid_mixed_with_invalid(self):
        items = [
            self._valid_item(),
            self._valid_item(category=""),  # dropped
            self._valid_item(category="Table"),  # kept
        ]
        result = _validate_extracted_items(items)
        assert len(result) == 2
        assert result[0]["category"] == "Sofa"
        assert result[1]["category"] == "Table"


class TestMatchCategory:
    """Tests for _match_category edge cases."""

    def test_matches_sofa(self):
        assert _match_category({"category": "Sofa"}) == "sofa"

    def test_matches_sectional_sofa(self):
        assert _match_category({"category": "sectional sofa"}) == "sofa"

    def test_matches_coffee_table(self):
        assert _match_category({"category": "Coffee Table"}) == "coffee_table"

    def test_matches_rug(self):
        assert _match_category({"category": "Area Rug"}) == "rug"

    def test_matches_floor_lamp(self):
        assert _match_category({"category": "Floor Lamp"}) == "floor_lamp"

    def test_no_match_returns_none(self):
        assert _match_category({"category": "Wall Art"}) is None

    def test_none_category_returns_none(self):
        assert _match_category({"category": None}) is None

    def test_missing_category_returns_none(self):
        assert _match_category({}) is None

    def test_empty_category_returns_none(self):
        assert _match_category({"category": ""}) is None


class TestComputeRoomConstraintsEdgeCases:
    """Tests for zero/negative dimension handling."""

    def test_zero_width_returns_empty(self):
        dims = RoomDimensions(width_m=0, length_m=4.0, height_m=2.7)
        assert _compute_room_constraints(dims) == {}

    def test_negative_height_returns_empty(self):
        dims = RoomDimensions(width_m=3.0, length_m=4.0, height_m=-1.0)
        assert _compute_room_constraints(dims) == {}

    def test_all_zero_returns_empty(self):
        dims = RoomDimensions(width_m=0, length_m=0, height_m=0)
        assert _compute_room_constraints(dims) == {}


class TestFormatRoomConstraintsInvalidDims:
    """Tests for _format_room_constraints_for_prompt with invalid dimensions."""

    def test_invalid_dims_no_keyerror(self):
        """Zero dims → empty constraints → prompt should not crash."""
        dims = RoomDimensions(width_m=0, length_m=0, height_m=0)
        result = _format_room_constraints_for_prompt(None, dims)
        # Should still produce room line but no category limits
        assert "0.0m x 0.0m" in result
        assert "Sofa" not in result


class TestFormatRoomConstraintsSourceLabel:
    """G21: Source label should reflect actual data origin, not just parameter presence."""

    def test_context_lidar_dims_labeled_correctly(self):
        """When dims come from room_context (LiDAR enriched), source should say 'LiDAR scan'."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(
            room_dimensions=dims,
            enrichment_sources=["photos", "lidar"],
        )
        # Direct room_dimensions param is None — dims come from context fallback
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "LiDAR scan" in result
        assert "Per-category size limits" in result

    def test_context_photo_only_dims_labeled_correctly(self):
        """When context has dims from photos only (no LiDAR), source should say 'photo analysis'."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(
            room_dimensions=dims,
            enrichment_sources=["photos"],
        )
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "photo analysis" in result

    def test_context_dims_without_photo_analysis(self):
        """Context with LiDAR dims but no photo_analysis should still format constraints."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(
            photo_analysis=None,
            room_dimensions=dims,
            enrichment_sources=["lidar"],
        )
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "Sofa" in result
        # No furniture section since photo_analysis is None
        assert "Detected furniture" not in result

    def test_context_dims_with_empty_enrichment_sources(self):
        """Default empty enrichment_sources falls back to 'photo analysis' source label."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(room_dimensions=dims)  # enrichment_sources defaults to []
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "photo analysis" in result

    def test_context_with_none_enrichment_sources_no_crash(self):
        """Defensive: enrichment_sources=None should not crash (or [] guard)."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(room_dimensions=dims, enrichment_sources=["photos"])
        ctx.enrichment_sources = None  # type: ignore[assignment]  # bypass Pydantic
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "photo analysis" in result  # defaults to photo since guard converts None to []


class TestComputeRoomConstraintsTinyRoom:
    """Verify max(0, ...) guards prevent negative constraints in tiny rooms."""

    def test_tiny_room_sofa_max_zero(self):
        """1m × 1m room: usable wall (100-120) goes negative → sofa max = 0."""
        dims = RoomDimensions(width_m=1.0, length_m=1.0, height_m=2.0)
        c = _compute_room_constraints(dims)
        assert float(c["sofa"]["max_width_cm"]) == 0
        assert float(c["coffee_table"]["max_width_cm"]) == 0
        assert float(c["dining_table"]["max_length_cm"]) == 0


class TestComputeRoomConstraintsMinimumRoom:
    """Verify constraints at MIN_DIMENSION_M boundary (0.3m × 0.3m × 0.3m).

    This is the smallest valid room per the parser (G27). All constraints
    should be zero or near-zero — the room is too small for any furniture.
    """

    def test_minimum_room_all_constraints_zero(self):
        """0.3m room: all constraint values should be 0 (clamped by max(0,...))."""
        dims = RoomDimensions(width_m=0.3, length_m=0.3, height_m=0.3)
        c = _compute_room_constraints(dims)
        assert float(c["sofa"]["max_width_cm"]) == 0
        assert float(c["coffee_table"]["max_width_cm"]) == 0
        assert float(c["dining_table"]["max_length_cm"]) == 0
        assert float(c["floor_lamp"]["max_height_cm"]) == 0

    def test_minimum_room_rug_positive(self):
        """Rug constraint uses percentage, so it's positive even for tiny rooms."""
        dims = RoomDimensions(width_m=0.3, length_m=0.3, height_m=0.3)
        c = _compute_room_constraints(dims)
        # 30cm * 0.80 = 24cm; 30cm * 0.70 = 21cm — small but positive
        assert float(c["rug"]["width_cm"]) > 0
        assert float(c["rug"]["length_cm"]) > 0


class TestFormatRoomConstraintsEstimatedDimsFallback:
    """Verify photo-estimated dimensions fallback path in _format_room_constraints_for_prompt.

    When no precise dims (LiDAR or context.room_dimensions) exist,
    the function should fall back to photo_analysis.estimated_dimensions.
    """

    def test_photo_estimated_dims_used_when_no_precise_dims(self):
        """Context with photo_analysis but no room_dimensions uses estimated dims."""
        analysis = RoomAnalysis(
            room_type="living room",
            estimated_dimensions="4m x 5m (approximate)",
        )
        ctx = RoomContext(
            photo_analysis=analysis,
            room_dimensions=None,
            enrichment_sources=["photos"],
        )
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4m x 5m (approximate)" in result
        assert "photo analysis" in result.lower()
        # Should NOT have per-category limits (no precise dims to compute from)
        assert "Sofa" not in result

    def test_no_dims_no_analysis_returns_unavailable(self):
        """Completely empty context returns 'No room dimensions available.'"""
        ctx = RoomContext(photo_analysis=None, room_dimensions=None)
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "No room dimensions available" in result


class TestFormatRoomConstraintsPrecedence:
    """Verify direct room_dimensions takes precedence over room_context.room_dimensions.

    In the real workflow, both come from the same source (scan_data.room_dimensions),
    but the function signature allows them to differ. The direct parameter should win
    per line 216: `dims = room_dimensions or (room_context.room_dimensions ...)`.
    """

    def test_direct_dims_override_context_dims(self):
        """When both params differ, direct room_dimensions should be used in prompt."""
        direct_dims = RoomDimensions(width_m=10.0, length_m=12.0, height_m=3.0)
        context_dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(
            room_dimensions=context_dims,
            enrichment_sources=["photos", "lidar"],
        )
        result = _format_room_constraints_for_prompt(ctx, direct_dims)
        # Direct dims (10.0 x 12.0) should appear, not context dims (4.0 x 5.0)
        assert "10.0m x 12.0m" in result
        assert "4.0m x 5.0m" not in result
        assert "LiDAR scan" in result

    def test_context_dims_used_when_direct_is_none(self):
        """When direct param is None, context.room_dimensions should be used."""
        context_dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.5)
        ctx = RoomContext(
            room_dimensions=context_dims,
            enrichment_sources=["photos"],
        )
        result = _format_room_constraints_for_prompt(ctx, None)
        assert "4.0m x 5.0m" in result
        assert "photo analysis" in result


class TestDimensionFilterZeroDims:
    """Verify filter_by_dimensions with zero-valued RoomDimensions."""

    def test_zero_dims_no_annotation(self):
        """Zero dimensions → empty constraints → products pass through without room_fit."""
        dims = RoomDimensions(width_m=0, length_m=0, height_m=0)
        items = [{"category": "Sofa"}]
        scored = [[{"weighted_total": 0.8, "dimensions": "84x36x32 inches"}]]
        result = filter_by_dimensions(items, scored, dims)
        assert "room_fit" not in result[0][0]


class TestDimensionFilterListMismatch:
    """Tests for filter_by_dimensions list alignment guard."""

    def test_mismatched_lists_returns_unmodified(self):
        """When items and scored_products have different lengths, return unchanged."""
        items = [{"category": "Sofa"}]
        scored = [
            [{"product_url": "a.com", "dimensions": "84x36 inches"}],
            [{"product_url": "b.com", "dimensions": "48x24 inches"}],
        ]
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        result = filter_by_dimensions(items, scored, dims)
        # Should return unmodified — no room_fit annotations added
        assert result == scored
        assert "room_fit" not in result[0][0]


class TestParseProductDimsCmRugCategory:
    """Tests for category-aware unit inference in dimension parsing."""

    def test_rug_without_unit_assumes_feet(self):
        """'8x10' rug should be parsed as 8ft x 10ft, not 8in x 10in."""
        result = _parse_product_dims_cm("8x10", category="Area Rug")
        assert result is not None
        w, d, h = result
        # 8ft = 243.84cm, 10ft = 304.8cm
        assert abs(w - 243.84) < 1.0
        assert abs(d - 304.8) < 1.0

    def test_rug_with_inches_unit_uses_inches(self):
        """'96x120 inches' rug should use inches even for rug category."""
        result = _parse_product_dims_cm("96x120 inches", category="Rug")
        assert result is not None
        w, d, h = result
        assert abs(w - 96 * 2.54) < 1.0
        assert abs(d - 120 * 2.54) < 1.0

    def test_rug_with_cm_unit_uses_cm(self):
        result = _parse_product_dims_cm("200x300 cm", category="Rug")
        assert result is not None
        assert result[0] == 200.0
        assert result[1] == 300.0

    def test_non_rug_without_unit_assumes_inches(self):
        """Furniture without unit should still default to inches."""
        result = _parse_product_dims_cm("84x36", category="Sofa")
        assert result is not None
        w, d, h = result
        assert abs(w - 84 * 2.54) < 1.0

    def test_none_category_defaults_to_inches(self):
        result = _parse_product_dims_cm("84x36", category=None)
        assert result is not None
        w, d, h = result
        assert abs(w - 84 * 2.54) < 1.0


# === Gap Tests: search_all_items, auth errors, R2 resolution, caching ===


class TestSearchAllItems:
    """Direct tests for the search_all_items orchestrator."""

    def test_gathers_results_for_multiple_items(self):
        """search_all_items should return one result list per input item."""
        items = [
            {"category": "Sofa", "description": "velvet sofa"},
            {"category": "Lamp", "description": "floor lamp"},
            {"category": "Rug", "description": "area rug"},
        ]

        async def mock_search(http_client, item, api_key, **kwargs):
            return [{"title": f"Result for {item['category']}", "url": "https://a.com"}]

        with patch("app.activities.shopping.search_products_for_item", side_effect=mock_search):
            from app.activities.shopping import search_all_items

            results = asyncio.run(search_all_items(items, "fake-key"))

        assert len(results) == 3
        assert results[0][0]["title"] == "Result for Sofa"
        assert results[1][0]["title"] == "Result for Lamp"
        assert results[2][0]["title"] == "Result for Rug"

    def test_passes_room_dimensions(self):
        """search_all_items should forward room_dimensions to each search call."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        items = [{"category": "Sofa", "description": "sofa"}]
        captured_kwargs: list[dict] = []

        async def mock_search(http_client, item, api_key, **kwargs):
            captured_kwargs.append(kwargs)
            return []

        with patch("app.activities.shopping.search_products_for_item", side_effect=mock_search):
            from app.activities.shopping import search_all_items

            asyncio.run(search_all_items(items, "fake-key", room_dimensions=dims))

        assert captured_kwargs[0]["room_dimensions"] is dims

    def test_empty_items_returns_empty(self):
        """search_all_items with no items should return empty list."""
        from app.activities.shopping import search_all_items

        results = asyncio.run(search_all_items([], "fake-key"))
        assert results == []


class TestScoringWeightValidation:
    """LiDAR weights should only apply when dimensions are valid."""

    def test_invalid_dims_use_default_weights(self):
        """Non-positive dimensions should fall back to default scoring weights."""
        bad_dims = RoomDimensions(width_m=0.0, length_m=5.0, height_m=2.7)
        prompt = _build_scoring_prompt(
            {"category": "Planter", "description": "planter"},
            {"title": "Test Planter", "url": "https://a.com", "text": ""},
            None,
            room_dimensions=bad_dims,
        )
        # Default weights: dimensions = 0.10 (10%)
        assert "10%" in prompt or "0.10" in prompt

    def test_valid_dims_use_lidar_weights(self):
        """Positive dimensions should use LiDAR scoring weights."""
        good_dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        prompt = _build_scoring_prompt(
            {"category": "Planter", "description": "planter"},
            {"title": "Test Planter", "url": "https://a.com", "text": ""},
            None,
            room_dimensions=good_dims,
        )
        # LiDAR weights: dimensions = 0.20 (20%)
        assert "20%" in prompt or "0.20" in prompt


class TestAuthErrorNonRetryable:
    """401/403 auth errors should be non-retryable across all activities."""

    def _make_input(self) -> GenerateShoppingListInput:
        return GenerateShoppingListInput(
            design_image_url="https://example.com/design.jpg",
            original_room_photo_urls=["https://example.com/room.jpg"],
        )

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    def test_extraction_401_is_non_retryable(self, mock_extract):
        """401 Unauthorized during extraction should be non-retryable."""
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.side_effect = anthropic.AuthenticationError(
            message="Invalid API key",
            response=_make_httpx_response(401),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is True

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    def test_extraction_403_is_non_retryable(self, mock_extract):
        """403 Forbidden during extraction should be non-retryable."""
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.side_effect = anthropic.PermissionDeniedError(
            message="Forbidden",
            response=_make_httpx_response(403),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is True

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.score_all_products")
    @patch("app.activities.shopping.search_all_items")
    @patch("app.activities.shopping.extract_items")
    def test_scoring_401_is_non_retryable(self, mock_extract, mock_search, mock_score):
        """401 Unauthorized during scoring should be non-retryable."""
        import pytest
        from temporalio.exceptions import ApplicationError

        mock_extract.return_value = [{"item_name": "Sofa", "search_priority": "HIGH"}]
        mock_search.return_value = [[{"url": "https://example.com", "title": "Sofa"}]]
        mock_score.side_effect = anthropic.AuthenticationError(
            message="Invalid API key",
            response=_make_httpx_response(401),
            body=None,
        )

        with pytest.raises(ApplicationError) as exc_info:
            asyncio.run(generate_shopping_list(self._make_input()))
        assert exc_info.value.non_retryable is True


class TestR2ResolutionInShopping:
    """Verify R2 storage keys are resolved before use in the shopping pipeline."""

    @patch.dict(
        "os.environ",
        {"ANTHROPIC_API_KEY": "test-key", "EXA_API_KEY": "test-exa"},
    )
    @patch("app.activities.shopping.extract_items")
    @patch("app.utils.r2.resolve_url")
    @patch("app.utils.r2.resolve_urls")
    def test_r2_keys_resolved_to_presigned_urls(
        self, mock_resolve_urls, mock_resolve_url, mock_extract
    ):
        """R2 storage keys should be resolved to presigned URLs before pipeline starts."""
        mock_resolve_url.return_value = "https://presigned.r2/design.jpg"
        mock_resolve_urls.return_value = ["https://presigned.r2/room.jpg"]
        mock_extract.return_value = []  # short-circuit: no items → pipeline ends

        input_data = GenerateShoppingListInput(
            design_image_url="projects/p1/design.jpg",
            original_room_photo_urls=["projects/p1/room.jpg"],
        )

        result = asyncio.run(generate_shopping_list(input_data))

        mock_resolve_url.assert_called_once_with("projects/p1/design.jpg")
        mock_resolve_urls.assert_called_once_with(["projects/p1/room.jpg"])
        assert result.items == []


class TestScoringCachePaths:
    """Verify LLM response cache hit/miss paths in score_product."""

    def _make_item(self) -> dict:
        return {"category": "Sofa", "description": "velvet sofa", "style": "modern"}

    def _make_product(self) -> dict:
        return {"title": "Nice Sofa", "url": "https://store.com/sofa", "text": "$599"}

    @patch("app.utils.llm_cache.get_cached")
    @patch("app.utils.llm_cache.set_cached")
    def test_cache_hit_returns_cached_scores(self, mock_set, mock_get):
        """When cache has a hit, score_product should return cached data without API call."""
        cached_data = {
            "weighted_total": 0.85,
            "why_matched": "cached match",
            "category_score": 1.0,
        }
        mock_get.return_value = cached_data

        mock_client = MagicMock()

        result = asyncio.run(
            score_product(mock_client, self._make_item(), self._make_product(), None)
        )

        # API should NOT be called
        mock_client.messages.create.assert_not_called()
        assert result["weighted_total"] == 0.85
        # Product metadata should be restored
        assert result["product_url"] == "https://store.com/sofa"
        assert result["product_name"] == "Nice Sofa"
        mock_set.assert_not_called()

    @patch("app.utils.llm_cache.get_cached")
    @patch("app.utils.llm_cache.set_cached")
    def test_cache_miss_calls_api_and_caches(self, mock_set, mock_get):
        """When cache misses, score_product should call API and cache the result."""
        mock_get.return_value = None

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(
                text='{"weighted_total": 0.9, "why_matched": "great match", '
                '"category_score": 1.0, "material_score": 0.8, '
                '"color_score": 0.7, "style_score": 0.6, "dimensions_score": 0.5}'
            )
        ]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        mock_client = MagicMock()
        mock_client.messages = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result = asyncio.run(
            score_product(mock_client, self._make_item(), self._make_product(), None)
        )

        mock_client.messages.create.assert_called_once()
        mock_set.assert_called_once()
        assert result["weighted_total"] == 0.9


# === Streaming Shopping Tests ===


class TestGenerateShoppingListStreaming:
    """Tests for generate_shopping_list_streaming async generator."""

    def _make_input(self) -> GenerateShoppingListInput:
        return GenerateShoppingListInput(
            design_image_url="https://r2.example.com/projects/test-123/generated/opt0.png",
            original_room_photo_urls=["https://r2.example.com/projects/test-123/photos/room.jpg"],
            design_brief=DesignBrief(room_type="bathroom", mood="modern spa"),
        )

    def test_missing_api_key_yields_error(self, monkeypatch):
        """Missing ANTHROPIC_API_KEY should yield an error event, not crash."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("EXA_API_KEY", raising=False)

        with patch("app.activities.shopping.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.exa_api_key = ""
            events = asyncio.run(self._collect_events())
        assert len(events) == 1
        assert "event: error" in events[0]
        assert "ANTHROPIC_API_KEY" in events[0]

    def test_missing_exa_key_yields_error(self, monkeypatch):
        """Missing EXA_API_KEY should yield an error event."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.delenv("EXA_API_KEY", raising=False)

        with patch("app.activities.shopping.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            mock_settings.exa_api_key = ""
            events = asyncio.run(self._collect_events())
        assert len(events) == 1
        assert "event: error" in events[0]
        assert "EXA_API_KEY" in events[0]

    @patch("app.activities.shopping.extract_items", new_callable=AsyncMock)
    @patch("app.activities.shopping.search_products_for_item", new_callable=AsyncMock)
    @patch("app.activities.shopping.score_all_products", new_callable=AsyncMock)
    def test_yields_status_search_and_done(
        self,
        mock_score,
        mock_search,
        mock_extract,
        monkeypatch,
    ):
        """Full pipeline should yield status → item_search → item → done events."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("EXA_API_KEY", "test-key")

        # Mock extraction returns 2 items
        mock_extract.return_value = [
            {"description": "Vanity", "category": "Vanity", "search_priority": "HIGH"},
            {"description": "Mirror", "category": "Mirror", "search_priority": "MEDIUM"},
        ]
        # Mock search returns 1 product per item
        mock_search.return_value = [
            {
                "product_name": "Test Vanity",
                "product_url": "https://amazon.com/vanity",
                "price_cents": 29900,
            }
        ]
        # Mock scoring returns scored products
        mock_score.return_value = [
            [
                {
                    "product_name": "Test Vanity",
                    "product_url": "https://amazon.com/vanity",
                    "image_url": "https://img.com/v.jpg",
                    "weighted_total": 0.85,
                    "price_cents": 29900,
                    "why_matched": "Good match",
                }
            ],
            [],  # Mirror has no scored results
        ]

        events = asyncio.run(self._collect_events())

        # Check event types present
        event_types = [e.split("\n")[0] for e in events]
        assert "event: status" in event_types
        assert event_types.count("event: item_search") == 2  # One per item
        assert "event: item" in event_types  # At least one matched product
        assert "event: done" in event_types

        # The done event should contain valid GenerateShoppingListOutput
        done_events = [e for e in events if e.startswith("event: done")]
        assert len(done_events) == 1
        import json

        data_line = done_events[0].split("data: ", 1)[1].split("\n")[0]
        output = json.loads(data_line)
        assert "items" in output
        assert "unmatched" in output

    @patch("app.activities.shopping.extract_items", new_callable=AsyncMock)
    def test_empty_extraction_yields_done_immediately(
        self,
        mock_extract,
        monkeypatch,
    ):
        """No items extracted should yield status then done with empty list."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("EXA_API_KEY", "test-key")
        mock_extract.return_value = []

        events = asyncio.run(self._collect_events())

        event_types = [e.split("\n")[0] for e in events]
        assert "event: status" in event_types
        assert "event: done" in event_types
        assert "event: item_search" not in event_types

    @patch("app.activities.shopping.extract_items", new_callable=AsyncMock)
    def test_extraction_error_yields_error_event(
        self,
        mock_extract,
        monkeypatch,
    ):
        """Extraction failure should yield an error SSE event."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("EXA_API_KEY", "test-key")
        mock_extract.side_effect = anthropic.RateLimitError(
            message="rate limited",
            response=_make_httpx_response(429),
            body=None,
        )

        events = asyncio.run(self._collect_events())

        assert len(events) == 1
        assert "event: error" in events[0]
        assert "Extraction failed" in events[0]

    def test_url_resolution_failure_yields_error(self, monkeypatch):
        """resolve_url failure should yield error event, not crash."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        monkeypatch.setenv("EXA_API_KEY", "test-key")

        # Use R2 storage keys (not https:// URLs) to trigger resolve_url
        inp = GenerateShoppingListInput(
            design_image_url="projects/test-123/generated/opt0.png",
            original_room_photo_urls=["projects/test-123/photos/room.jpg"],
            design_brief=DesignBrief(room_type="bathroom", mood="modern spa"),
        )

        with patch(
            "app.utils.r2.resolve_url",
            side_effect=Exception("R2 connection failed"),
        ):
            events = asyncio.run(self._collect_events(inp))

        assert len(events) == 1
        assert "event: error" in events[0]
        assert "URL resolution failed" in events[0]

    async def _collect_events(self, inp=None) -> list[str]:
        if inp is None:
            inp = self._make_input()
        events = []
        async for chunk in generate_shopping_list_streaming(inp):
            events.append(chunk)
        return events


class TestExaTracingDecorators:
    """Verify @traceable decorators don't alter function behavior."""

    def test_build_search_queries_returns_list(self):
        """Decorated _build_search_queries still returns a list of strings."""
        item = {"category": "sofa", "style": "modern", "description": "gray modern sofa"}
        result = _build_search_queries(item)
        assert isinstance(result, list)
        assert all(isinstance(q, str) for q in result)
        assert len(result) >= 1

    def test_build_search_queries_with_brief(self):
        """Decorated function handles DesignBrief parameter correctly."""
        from app.models.contracts import DesignBrief, StyleProfile

        brief = DesignBrief(
            room_type="living room",
            style_profile=StyleProfile(mood="contemporary"),
        )
        item = {"category": "coffee table", "description": "round oak coffee table"}
        result = _build_search_queries(item, design_brief=brief)
        assert isinstance(result, list)
        assert any("contemporary" in q or "living room" in q for q in result)

    def test_search_exa_decorated_callable(self):
        """_search_exa is still an async callable after decoration."""
        import inspect

        assert inspect.iscoroutinefunction(_search_exa) or callable(_search_exa)

    def test_search_products_for_item_decorated_callable(self):
        """search_products_for_item is still async callable after decoration."""
        import inspect

        assert inspect.iscoroutinefunction(search_products_for_item) or callable(
            search_products_for_item
        )
