"""Tests for benchmark runner."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from shopping_eval.benchmark import load_benchmark_cases, run_benchmark
from shopping_eval.models import BenchmarkCase, BenchmarkReport, CheckResult, TrialResult

if TYPE_CHECKING:
    from pathlib import Path


def test_load_benchmark_cases():
    """Load fixtures from the benchmark_items.json file."""
    cases = load_benchmark_cases()
    assert len(cases) == 10
    assert all(isinstance(c, BenchmarkCase) for c in cases)
    assert cases[0].item_id == "bench_sofa_01"
    assert cases[0].item["category"] == "sofa"


@pytest.mark.asyncio
async def test_run_benchmark_produces_report(tmp_path: Path):
    """Full benchmark run (mocked Exa) produces BenchmarkReport with per-category data."""
    fake_trial = TrialResult(
        query="test",
        query_components=["description"],
        search_type="auto",
        results_count=1,
        check_results=[
            CheckResult(url="https://example.com", link_loads=True, product_matches=True),
        ],
        link_alive_rate=1.0,
        product_match_rate=1.0,
        dimension_match_rate=0.0,
    )

    with patch("shopping_eval.benchmark.run_trial", new_callable=AsyncMock) as mock_trial:
        mock_trial.return_value = fake_trial

        report = await run_benchmark(
            exa_api_key="test-key",
            ablation_log_path=tmp_path / "ablation.jsonl",
            cases=load_benchmark_cases()[:2],  # Only run 2 for speed
        )

    assert isinstance(report, BenchmarkReport)
    assert report.num_cases == 2
    assert report.avg_link_alive_rate == 1.0
    assert report.avg_product_match_rate == 1.0
