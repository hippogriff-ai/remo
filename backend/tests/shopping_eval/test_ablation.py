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


def test_inconclusive_metrics_report_none_not_zero(tmp_path: Path):
    """A component where every trial had dimension_match_rate=None reports dim_rate=None.

    Regression test: the ablation report must distinguish "never evaluated"
    from "evaluated, always failed".
    """
    log_path = tmp_path / "ablation.jsonl"

    trial = TrialResult(
        query="test query",
        query_components=["description"],
        search_type="auto",
        results_count=1,
        check_results=[CheckResult(url="https://a.com", link_loads=True, product_matches=True)],
        link_alive_rate=1.0,
        product_match_rate=1.0,
        dimension_match_rate=None,
    )
    append_ablation(log_path, trial, {"category": "mirror"})
    append_ablation(log_path, trial, {"category": "mirror"})

    report = load_ablation_report(log_path)
    bucket = report["mirror"]["description"]
    assert bucket["total"] == 2
    assert bucket["link_rate"] == 1.0
    assert bucket["link_n"] == 2
    assert bucket["dim_rate"] is None
    assert bucket["dim_n"] == 0
