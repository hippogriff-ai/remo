"""Tests for deterministic shopping search checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.models.contracts import RoomDimensions
from shopping_eval.checks import check_dimension_matches, check_link_loads, check_product_matches


@pytest.mark.asyncio
async def test_link_loads_success():
    """HEAD 200 → True."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/product")
    assert result is True


@pytest.mark.asyncio
async def test_link_loads_404():
    """HEAD 404 → False."""
    mock_resp = AsyncMock()
    mock_resp.status_code = 404
    mock_client = AsyncMock()
    mock_client.head = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/gone")
    assert result is False


@pytest.mark.asyncio
async def test_link_loads_timeout():
    """Timeout → False."""
    import httpx as httpx_mod

    mock_client = AsyncMock()
    mock_client.head = AsyncMock(side_effect=httpx_mod.TimeoutException("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("shopping_eval.checks.httpx.AsyncClient", return_value=mock_client):
        result = await check_link_loads("https://example.com/slow")
    assert result is False


@pytest.mark.asyncio
async def test_link_loads_empty_url():
    """Empty URL → False."""
    result = await check_link_loads("")
    assert result is False


# --- Check 2: Product match ---


class TestProductMatches:
    def test_category_match(self):
        """Exa summary category contains target category."""
        result = check_product_matches(
            exa_summary={"product_name": "Modern Walnut Coffee Table", "material": "walnut"},
            exa_text="Beautiful mid-century coffee table made from solid walnut.",
            target_item={"category": "coffee table", "material": "walnut"},
        )
        assert result.matches is True

    def test_category_mismatch(self):
        """Product name doesn't match target category at all."""
        result = check_product_matches(
            exa_summary={"product_name": "Scented Candle Set", "material": "wax"},
            exa_text="Luxury candle gift set",
            target_item={"category": "coffee table", "material": "walnut"},
        )
        assert result.matches is False

    def test_empty_summary(self):
        """No summary data → falls back to text matching."""
        result = check_product_matches(
            exa_summary={},
            exa_text="Walnut coffee table with tapered legs, 48 inches wide",
            target_item={"category": "coffee table", "material": "walnut"},
        )
        assert result.matches is True

    def test_no_data(self):
        """No summary or text → False."""
        result = check_product_matches(
            exa_summary={},
            exa_text="",
            target_item={"category": "sofa", "material": "leather"},
        )
        assert result.matches is False

    def test_wall_art_does_not_match_cart(self):
        """Regression: "art" keyword must not match "cart" in "add to cart".

        run_benchmark filters Exa results with include_text=["add to cart"],
        so every corpus contains that phrase. Substring matching used to
        spuriously accept any page for wall art queries.
        """
        result = check_product_matches(
            exa_summary={"product_name": "Scented Candle Set"},
            exa_text="Luxury candle gift set. Add to cart to purchase.",
            target_item={"category": "wall art"},
        )
        assert result.matches is False

    def test_wall_art_matches_actual_art(self):
        """Sanity: real art product still matches after the word-boundary fix."""
        result = check_product_matches(
            exa_summary={"product_name": "Framed Canvas Art Print"},
            exa_text="Modern abstract art print, 24x36 inches.",
            target_item={"category": "wall art"},
        )
        assert result.matches is True


# --- Check 3: Dimension match ---


class TestDimensionMatches:
    def test_sofa_fits(self):
        """Sofa within room constraint → matches."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        result = check_dimension_matches(
            exa_dimensions_str="84x36x32 inches",
            target_item={"category": "sofa"},
            room_dimensions=dims,
        )
        assert result.matches is True

    def test_sofa_too_large(self):
        """Sofa exceeds room constraint → does not match."""
        dims = RoomDimensions(width_m=2.5, length_m=3.0, height_m=2.7)
        result = check_dimension_matches(
            exa_dimensions_str="120x40x32 inches",
            target_item={"category": "sofa"},
            room_dimensions=dims,
        )
        assert result.matches is False

    def test_no_dimensions(self):
        """No dimension string → None (inconclusive, not False)."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        result = check_dimension_matches(
            exa_dimensions_str=None,
            target_item={"category": "sofa"},
            room_dimensions=dims,
        )
        assert result.matches is None

    def test_no_room_dimensions(self):
        """No room dimensions → None (can't check)."""
        result = check_dimension_matches(
            exa_dimensions_str="84x36 inches",
            target_item={"category": "sofa"},
            room_dimensions=None,
        )
        assert result.matches is None

    def test_unconstrained_category(self):
        """Category without size constraints (e.g., planter) → None."""
        dims = RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7)
        result = check_dimension_matches(
            exa_dimensions_str="12x12x18 inches",
            target_item={"category": "planter"},
            room_dimensions=dims,
        )
        assert result.matches is None
