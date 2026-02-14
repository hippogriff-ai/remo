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
    EXA_MAX_RETRIES,
    MAX_CONCURRENT_SCORES,
    _build_extraction_messages,
    _build_fit_detail,
    _build_scoring_prompt,
    _build_search_queries,
    _compute_room_constraints,
    _extract_json,
    _extract_price_text,
    _extract_retailer,
    _format_room_constraints_for_prompt,
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

    def test_queries_include_shopping_intent(self):
        """Queries should include 'buy' or 'shop' to steer toward product pages."""
        item = {
            "source_tag": "BRIEF_ANCHORED",
            "source_reference": "velvet sofa",
            "category": "Seating",
            "style": "modern",
            "material": "velvet",
        }
        queries = _build_search_queries(item)
        has_intent = any("buy" in q or "shop" in q for q in queries)
        assert has_intent, f"Queries should include shopping intent. Got: {queries}"

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
        # "buy warm walnut coffee table" appears once from source_ref,
        # description query is skipped because it matches source_ref
        buy_ref_count = sum(1 for q in queries if "warm walnut coffee table" in q)
        assert buy_ref_count == 1

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
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=None)
        assert "weight: 0.3" in prompt  # category default
        assert "weight: 0.1" in prompt  # dimensions default

    def test_lidar_weights_with_room_dims(self):
        """With room_dimensions, dimensions weight increases to 0.2."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=dims)
        assert "weight: 0.25" in prompt  # category lidar
        assert "weight: 0.2)" in prompt  # dimensions lidar
        assert "LiDAR-measured" in prompt
        assert "4.0m" in prompt

    def test_no_room_dimensions_section_without_lidar(self):
        """Without room_dimensions, no room dims section in prompt."""
        item = {"category": "Sofa", "description": "velvet sofa"}
        product = {"title": "Sofa", "url": "https://example.com"}
        prompt = _build_scoring_prompt(item, product, None, room_dimensions=None)
        assert "LiDAR-measured" not in prompt


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
            pytest.raises(ApplicationError, match="ANTHROPIC_API_KEY"),
        ):
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
            pytest.raises(ApplicationError, match="EXA_API_KEY"),
        ):
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

        async def mock_search_alternating(http_client, query, api_key, num_results):
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
            {"category": "Sofa", "description": "sofa"},
            {"title": "Test Sofa", "url": "https://a.com", "text": ""},
            None,
            room_dimensions=bad_dims,
        )
        # Default weights: dimensions = 0.10 (10%)
        assert "10%" in prompt or "0.10" in prompt

    def test_valid_dims_use_lidar_weights(self):
        """Positive dimensions should use LiDAR scoring weights."""
        good_dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        prompt = _build_scoring_prompt(
            {"category": "Sofa", "description": "sofa"},
            {"title": "Test Sofa", "url": "https://a.com", "text": ""},
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
