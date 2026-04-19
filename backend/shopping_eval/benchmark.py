"""Benchmark runner — loads fixtures, runs SearchTrialRunner, aggregates scores.

Usage:
    cases = load_benchmark_cases()
    report = await run_benchmark(exa_api_key="...", cases=cases)
    print(f"Link alive: {report.avg_link_alive_rate:.0%}")
    print(f"Product match: {report.avg_product_match_rate:.0%}")
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from app.activities.shopping import _RETAILER_DOMAINS, _build_search_queries_tagged
from app.models.contracts import DesignBrief, RoomDimensions

from .ablation import append_ablation
from .models import BenchmarkCase, BenchmarkReport, TrialResult
from .synthetic import generate_synthetic_listing
from .trial_runner import run_trial

FIXTURES_DIR = Path(__file__).parent / "fixtures"

Strategy = Literal["tagged", "synthetic", "both"]


def load_benchmark_cases(
    fixture_path: Path | None = None,
) -> list[BenchmarkCase]:
    """Load benchmark cases from JSON fixture file."""
    path = fixture_path or FIXTURES_DIR / "benchmark_items.json"
    raw = json.loads(path.read_text())
    return [
        BenchmarkCase(
            item_id=c["item_id"],
            item=c["item"],
            expected_category=c.get("expected_category", ""),
            expected_material=c.get("expected_material", ""),
            expected_dimensions=c.get("expected_dimensions", ""),
            gold_url=c.get("gold_url"),
            design_brief_json=c.get("design_brief_json"),
            room_dimensions_json=c.get("room_dimensions_json"),
        )
        for c in raw
    ]


def _deserialize_brief(data: dict | None) -> DesignBrief | None:
    if data is None:
        return None
    return DesignBrief.model_validate(data)


def _deserialize_dims(data: dict | None) -> RoomDimensions | None:
    if data is None:
        return None
    return RoomDimensions.model_validate(data)


async def run_benchmark(
    exa_api_key: str,
    ablation_log_path: Path | None = None,
    cases: list[BenchmarkCase] | None = None,
    *,
    skip_link_check: bool = False,
    strategy: Strategy = "tagged",
    anthropic_api_key: str | None = None,
) -> BenchmarkReport:
    """Run the full benchmark suite and produce an aggregate report.

    strategy controls which query builders are exercised:
        - "tagged"    — only _build_search_queries_tagged (default, no Claude calls)
        - "synthetic" — only Score-Then-Search via generate_synthetic_listing
        - "both"      — both, so tagged and synthetic queries can be A/B'd in one run

    For strategies that include "synthetic", anthropic_api_key is required. Set
    SYNTHETIC_LISTING_CACHE_DIR to make Claude calls cheap on re-run.
    """
    if strategy in ("synthetic", "both") and not anthropic_api_key:
        raise ValueError(
            f"strategy={strategy!r} requires anthropic_api_key (set ANTHROPIC_API_KEY)"
        )

    if cases is None:
        cases = load_benchmark_cases()

    all_trials: list[TrialResult] = []
    per_category: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"link": [], "product": [], "dim": []}
    )

    for case in cases:
        brief = _deserialize_brief(case.design_brief_json)
        dims = _deserialize_dims(case.room_dimensions_json)

        queries: list[tuple[str, list[str]]] = []
        if strategy in ("tagged", "both"):
            queries.extend(
                _build_search_queries_tagged(case.item, room_dimensions=dims, design_brief=brief)
            )
        if strategy in ("synthetic", "both"):
            assert anthropic_api_key is not None
            listing = await generate_synthetic_listing(
                case.item, anthropic_api_key=anthropic_api_key, design_brief=brief
            )
            if listing:
                queries.append((listing, ["synthetic_listing"]))

        for query, components in queries:
            trial = await run_trial(
                query=query,
                query_components=components,
                target_item=case.item,
                exa_api_key=exa_api_key,
                search_type="deep" if case.item.get("search_priority") == "HIGH" else "auto",
                room_dimensions=dims,
                include_domains=_RETAILER_DOMAINS,
                include_text=["add to cart"],
                skip_link_check=skip_link_check,
            )

            all_trials.append(trial)
            category = (case.item.get("category") or "unknown").lower()
            per_category[category]["link"].append(trial.link_alive_rate)
            per_category[category]["product"].append(trial.product_match_rate)
            per_category[category]["dim"].append(trial.dimension_match_rate)

            if ablation_log_path:
                append_ablation(ablation_log_path, trial, case.item)

    n_cases = len(cases)

    def _avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 3) if vals else 0.0

    all_link = [t.link_alive_rate for t in all_trials]
    all_product = [t.product_match_rate for t in all_trials]
    all_dim = [t.dimension_match_rate for t in all_trials]

    cat_report: dict[str, dict[str, float]] = {}
    for cat, rates in per_category.items():
        cat_report[cat] = {
            "link_rate": _avg(rates["link"]),
            "product_rate": _avg(rates["product"]),
            "dim_rate": _avg(rates["dim"]),
            "num_queries": len(rates["link"]),
        }

    return BenchmarkReport(
        num_cases=n_cases,
        total_queries=len(all_trials),
        avg_link_alive_rate=_avg(all_link),
        avg_product_match_rate=_avg(all_product),
        avg_dimension_match_rate=_avg(all_dim),
        per_category=cat_report,
        trial_results=all_trials,
    )
