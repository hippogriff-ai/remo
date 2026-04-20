"""Integration test: full optimize loop with mocked Exa."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from shopping_eval.ablation import load_ablation_report
from shopping_eval.benchmark import load_benchmark_cases, run_benchmark
from shopping_eval.models import BenchmarkReport

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_full_loop_mocked(tmp_path: Path):
    """Run benchmark → check ablation log exists → verify report structure."""
    ablation_path = tmp_path / "ablation.jsonl"

    # Mock Exa to return one result per query
    fake_results = [
        {
            "url": "https://wayfair.com/product",
            "title": "Test Product",
            "text": "A sofa made of boucle fabric, 84 inches wide",
            "summary": {
                "product_name": "Test Sofa",
                "dimensions": "84x36x32 inches",
                "material": "boucle",
            },
        },
    ]

    with (
        patch("shopping_eval.trial_runner._search_exa", new_callable=AsyncMock) as mock_exa,
        patch("shopping_eval.trial_runner.check_link_loads", new_callable=AsyncMock) as mock_link,
    ):
        mock_exa.return_value = fake_results
        mock_link.return_value = True

        cases = load_benchmark_cases()[:3]  # 3 cases for speed
        report = await run_benchmark(
            exa_api_key="test-key",
            ablation_log_path=ablation_path,
            cases=cases,
        )

    # Report structure
    assert isinstance(report, BenchmarkReport)
    assert report.num_cases == 3
    assert report.total_queries > 0
    assert 0.0 <= report.avg_link_alive_rate <= 1.0
    assert 0.0 <= report.avg_product_match_rate <= 1.0

    # Ablation log was written
    assert ablation_path.exists()
    ablation = load_ablation_report(ablation_path)
    assert len(ablation) > 0  # At least one category has data

    # Per-category data present
    assert len(report.per_category) > 0
