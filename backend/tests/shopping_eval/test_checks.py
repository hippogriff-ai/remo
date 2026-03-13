"""Tests for deterministic shopping search checks."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shopping_eval.checks import check_link_loads


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

from shopping_eval.checks import check_product_matches


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
