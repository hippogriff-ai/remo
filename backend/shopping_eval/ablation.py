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
) -> dict[str, dict[str, dict[str, float]]]:
    """Load ablation log and compute per-(category, component) success rates.

    Returns:
        {
            "sofa": {
                "description": {
                    "total": 10, "link_rate": 0.8, "product_rate": 0.6, "dim_rate": 0.4,
                },
                "material": {"total": 5, "link_rate": 0.9, ...},
            },
            ...
        }
    """
    if not log_path.exists():
        return {}

    acc: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(
            lambda: {"total": 0, "link_sum": 0.0, "product_sum": 0.0, "dim_sum": 0.0}
        )
    )

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
            link_rate = entry.get("link_alive_rate", 0.0)
            product_rate = entry.get("product_match_rate", 0.0)
            dim_rate = entry.get("dimension_match_rate", 0.0)

            for component in components:
                bucket = acc[category][component]
                bucket["total"] += 1
                bucket["link_sum"] += link_rate
                bucket["product_sum"] += product_rate
                bucket["dim_sum"] += dim_rate

    report: dict[str, dict[str, dict[str, float]]] = {}
    for category, components in acc.items():
        report[category] = {}
        for component, bucket in components.items():
            total = bucket["total"]
            report[category][component] = {
                "total": total,
                "link_rate": round(bucket["link_sum"] / total, 3) if total else 0.0,
                "product_rate": round(bucket["product_sum"] / total, 3) if total else 0.0,
                "dim_rate": round(bucket["dim_sum"] / total, 3) if total else 0.0,
            }

    return report
