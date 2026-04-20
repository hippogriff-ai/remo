"""Tests for Score-Then-Search synthetic listing wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shopping_eval.benchmark import load_benchmark_cases, run_benchmark
from shopping_eval.models import CheckResult, TrialResult
from shopping_eval.synthetic import generate_synthetic_listing

if TYPE_CHECKING:
    from pathlib import Path


def _fake_claude_response(text: str):
    """Shape a mocked anthropic.messages.create response."""
    resp = MagicMock()
    block = MagicMock()
    block.text = text
    resp.content = [block]
    return resp


@pytest.mark.asyncio
async def test_generate_synthetic_listing_calls_claude_when_cache_miss(tmp_path: Path):
    """No cache dir → always calls Claude, returns trimmed listing text."""
    with (
        patch.dict("os.environ", {"SYNTHETIC_LISTING_CACHE_DIR": ""}, clear=False),
        patch("shopping_eval.synthetic.anthropic.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_fake_claude_response(
                '  West Elm Walnut Coffee Table — 42" round top with tapered legs.  '
            )
        )
        mock_anthropic_cls.return_value = mock_client

        listing = await generate_synthetic_listing(
            item={"category": "coffee table", "material": "walnut"},
            anthropic_api_key="test-key",
        )

    assert listing.startswith("West Elm")
    assert listing.endswith("tapered legs.")
    mock_client.messages.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_synthetic_listing_hits_cache_on_second_call(tmp_path: Path):
    """With SYNTHETIC_LISTING_CACHE_DIR set, second call skips Claude."""
    import shopping_eval.synthetic as synth_mod

    with (
        patch.object(synth_mod, "_SYNTHETIC_CACHE_DIR", str(tmp_path)),
        patch("shopping_eval.synthetic.anthropic.AsyncAnthropic") as mock_anthropic_cls,
    ):
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=_fake_claude_response("Test Listing"))
        mock_anthropic_cls.return_value = mock_client

        item = {"category": "sofa", "material": "boucle"}
        first = await generate_synthetic_listing(item=item, anthropic_api_key="k")
        second = await generate_synthetic_listing(item=item, anthropic_api_key="k")

    assert first == second == "Test Listing"
    assert mock_client.messages.create.await_count == 1


@pytest.mark.asyncio
async def test_run_benchmark_synthetic_requires_anthropic_key():
    """strategy='synthetic' without anthropic_api_key raises before any work."""
    with pytest.raises(ValueError, match="requires anthropic_api_key"):
        await run_benchmark(
            exa_api_key="exa",
            cases=load_benchmark_cases()[:1],
            strategy="synthetic",
        )


@pytest.mark.asyncio
async def test_run_benchmark_both_strategy_emits_synthetic_query(tmp_path: Path):
    """strategy='both' runs tagged queries AND one synthetic_listing query per case."""
    fake_trial = TrialResult(
        query="placeholder",
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

    with (
        patch(
            "shopping_eval.benchmark.generate_synthetic_listing", new_callable=AsyncMock
        ) as mock_gen,
        patch("shopping_eval.benchmark.run_trial", new_callable=AsyncMock) as mock_trial,
    ):
        mock_gen.return_value = "Synthetic listing text for test item"
        mock_trial.return_value = fake_trial

        cases = load_benchmark_cases()[:2]
        await run_benchmark(
            exa_api_key="exa",
            ablation_log_path=tmp_path / "ablation.jsonl",
            cases=cases,
            strategy="both",
            anthropic_api_key="claude-key",
        )

    assert mock_gen.await_count == 2

    synthetic_calls = [
        call
        for call in mock_trial.await_args_list
        if call.kwargs.get("query_components") == ["synthetic_listing"]
    ]
    assert len(synthetic_calls) == 2
    assert all(
        call.kwargs.get("query") == "Synthetic listing text for test item"
        for call in synthetic_calls
    )
    # Score-Then-Search must hit Exa's neural retrieval, not whatever "auto" routes to.
    assert all(call.kwargs.get("search_type") == "neural" for call in synthetic_calls)

    tagged_calls = [
        call
        for call in mock_trial.await_args_list
        if call.kwargs.get("query_components") != ["synthetic_listing"]
    ]
    # Tagged queries keep the existing priority-based search_type.
    assert tagged_calls
    assert all(call.kwargs.get("search_type") in ("auto", "deep") for call in tagged_calls)
