"""Tests for ablation logger."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shopping_eval.ablation import append_ablation, load_ablation_report
from shopping_eval.models import CheckResult, TrialResult

if TYPE_CHECKING:
    from pathlib import Path


def _make_trial(
    query_components: list[str],
    category: str = "sofa",
    link_rate: float = 1.0,
    product_rate: float = 0.5,
) -> tuple[TrialResult, dict]:
    """Helper to create a TrialResult + item for testing."""
    return (
        TrialResult(
            query="test query",
            query_components=query_components,
            search_type="auto",
            results_count=2,
            check_results=[
                CheckResult(url="https://a.com", link_loads=True, product_matches=True),
                CheckResult(url="https://b.com", link_loads=True, product_matches=False),
            ],
            link_alive_rate=link_rate,
            product_match_rate=product_rate,
            dimension_match_rate=0.0,
        ),
        {"category": category},
    )


def test_append_and_load(tmp_path: Path):
    """Append entries, then load and verify report structure."""
    log_path = tmp_path / "ablation.jsonl"

    trial, item = _make_trial(["description", "material"], "sofa")
    append_ablation(log_path, trial, item)

    trial2, item2 = _make_trial(["description", "style_context"], "sofa")
    append_ablation(log_path, trial2, item2)

    report = load_ablation_report(log_path)
    assert "sofa" in report
    # "description" appeared in both trials
    assert report["sofa"]["description"]["total"] == 2
    # "material" appeared in 1 trial
    assert report["sofa"]["material"]["total"] == 1
    # "style_context" appeared in 1 trial
    assert report["sofa"]["style_context"]["total"] == 1


def test_empty_log(tmp_path: Path):
    """Empty log → empty report."""
    log_path = tmp_path / "ablation.jsonl"
    report = load_ablation_report(log_path)
    assert report == {}
