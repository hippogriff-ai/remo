"""Ablation logging — tracks which query components correlate with search success.

Appends one JSONL row per TrialResult. The report aggregates by
(category, component) to show which components have the highest success rate
for each furniture type.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from .models import TrialResult


def append_ablation(
    log_path: Path,
    trial: TrialResult,
    item: dict[str, Any],
) -> None:
    """Append one ablation entry per query component in the trial."""
    category = (item.get("category") or "unknown").lower()
    entry = {
        "category": category,
        "components": trial.query_components,
        "query": trial.query,
        "search_type": trial.search_type,
        "results_count": trial.results_count,
        "link_alive_rate": trial.link_alive_rate,
        "product_match_rate": trial.product_match_rate,
        "dimension_match_rate": trial.dimension_match_rate,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


def load_ablation_report(
    log_path: Path,
) -> dict[str, dict[str, dict[str, float | int | None]]]:
    """Load ablation log and compute per-(category, component) success rates.

    Each metric (link/product/dim) is averaged only over trials where that
    metric was actually evaluated. A ``None`` rate means the metric was
    inconclusive for every logged trial of this (category, component) pair —
    it is NOT the same as ``0.0``.

    Returns:
        {
            "sofa": {
                "description": {
                    "total": 10,
                    "link_rate": 0.8, "link_n": 10,
                    "product_rate": 0.6, "product_n": 10,
                    "dim_rate": None, "dim_n": 0,
                },
                ...
            },
        }
    """
    if not log_path.exists():
        return {}

    def _new_bucket() -> dict[str, Any]:
        return {
            "total": 0,
            "link_sum": 0.0,
            "link_n": 0,
            "product_sum": 0.0,
            "product_n": 0,
            "dim_sum": 0.0,
            "dim_n": 0,
        }

    acc: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(_new_bucket))

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            category = entry.get("category", "unknown")
            components = entry.get("components", [])
            # Each metric may be a float, explicit null (new schema), or
            # missing (old schema written before None support).
            link_rate = entry.get("link_alive_rate")
            product_rate = entry.get("product_match_rate")
            dim_rate = entry.get("dimension_match_rate")

            for component in components:
                bucket = acc[category][component]
                bucket["total"] += 1
                if link_rate is not None:
                    bucket["link_sum"] += link_rate
                    bucket["link_n"] += 1
                if product_rate is not None:
                    bucket["product_sum"] += product_rate
                    bucket["product_n"] += 1
                if dim_rate is not None:
                    bucket["dim_sum"] += dim_rate
                    bucket["dim_n"] += 1

    def _rate(sum_: float, n: int) -> float | None:
        return round(sum_ / n, 3) if n else None

    report: dict[str, dict[str, dict[str, float | int | None]]] = {}
    for category, components in acc.items():
        report[category] = {}
        for component, bucket in components.items():
            report[category][component] = {
                "total": bucket["total"],
                "link_rate": _rate(bucket["link_sum"], bucket["link_n"]),
                "link_n": bucket["link_n"],
                "product_rate": _rate(bucket["product_sum"], bucket["product_n"]),
                "product_n": bucket["product_n"],
                "dim_rate": _rate(bucket["dim_sum"], bucket["dim_n"]),
                "dim_n": bucket["dim_n"],
            }

    return report
