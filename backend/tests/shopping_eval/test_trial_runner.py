"""Tests for SearchTrialRunner."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from shopping_eval.models import TrialResult
from shopping_eval.trial_runner import run_trial


@pytest.mark.asyncio
async def test_run_trial_basic():
    """Run a single query, get back a TrialResult with check results."""
    fake_exa_results = [
        {
            "url": "https://wayfair.com/product/123",
            "title": "Modern Walnut Coffee Table",
            "text": "Beautiful solid walnut coffee table, 48x24x18 inches",
            "summary": {
                "product_name": "Modern Walnut Coffee Table",
                "dimensions": "48x24x18 inches",
                "material": "walnut",
            },
        },
    ]

    with (
        patch("shopping_eval.trial_runner._search_exa", new_callable=AsyncMock) as mock_search,
        patch("shopping_eval.trial_runner.check_link_loads", new_callable=AsyncMock) as mock_link,
    ):
        mock_search.return_value = fake_exa_results
        mock_link.return_value = True

        result = await run_trial(
            query="walnut coffee table",
            query_components=["description"],
            target_item={"category": "coffee table", "material": "walnut"},
            exa_api_key="test-key",
            search_type="auto",
        )

    assert isinstance(result, TrialResult)
    assert result.results_count == 1
    assert result.link_alive_rate == 1.0
    assert result.product_match_rate == 1.0
    assert len(result.check_results) == 1


@pytest.mark.asyncio
async def test_run_trial_no_results():
    """Exa returns nothing -> rates are 0."""
    with patch("shopping_eval.trial_runner._search_exa", new_callable=AsyncMock) as mock_search:
        mock_search.return_value = []

        result = await run_trial(
            query="nonexistent product xyz",
            query_components=["description"],
            target_item={"category": "sofa"},
            exa_api_key="test-key",
        )

    assert result.results_count == 0
    # No results means "not evaluated", not "every check failed" — all rates None
    assert result.link_alive_rate is None
    assert result.product_match_rate is None
    assert result.dimension_match_rate is None
