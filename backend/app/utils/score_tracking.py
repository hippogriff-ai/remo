"""Score tracking and regression detection for eval results.

Appends eval results to a JSONL file and detects regressions by comparing
the latest score to a rolling average of recent runs.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pathlib import Path


def append_score(
    history_path: Path,
    scenario: str,
    prompt_version: str,
    fast_eval: dict[str, Any] | None = None,
    deep_eval: dict[str, Any] | None = None,
    model: str = "gemini-3-pro-image-preview",
    duration_ms: int = 0,
) -> dict[str, Any]:
    """Append an eval result to the score history JSONL file.

    Returns the record that was appended.
    """
    record = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "scenario": scenario,
        "prompt_version": prompt_version,
        "fast_eval": fast_eval or {},
        "deep_eval": deep_eval or {},
        "model": model,
        "duration_ms": duration_ms,
    }
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with open(history_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def load_history(
    history_path: Path,
    scenario: str | None = None,
) -> list[dict[str, Any]]:
    """Load score history, optionally filtered by scenario."""
    if not history_path.exists():
        return []
    records = []
    with open(history_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if scenario is None or record.get("scenario") == scenario:
                    records.append(record)
            except json.JSONDecodeError:
                log.warning("score_history_corrupted_line: %s", history_path)
                continue
    return records


def detect_regression(
    history_path: Path,
    scenario: str,
    latest_total: int,
    window: int = 5,
    threshold: int = 10,
) -> dict[str, Any]:
    """Check if the latest score is a regression vs the rolling average.

    Args:
        history_path: Path to score_history.jsonl.
        scenario: Scenario name to filter history.
        latest_total: The latest deep eval total score.
        window: Number of recent runs to average (default 5).
        threshold: Points drop that triggers a regression alert (default 10).

    Returns:
        Dict with:
        - is_regression: bool
        - rolling_avg: float (or None if insufficient history)
        - delta: float (latest_total - rolling_avg)
        - window_size: int (actual number of runs used)
    """
    records = load_history(history_path, scenario=scenario)
    # Get deep eval totals from recent records
    totals = []
    for r in records:
        deep = r.get("deep_eval", {})
        total = deep.get("total")
        if isinstance(total, (int, float)):
            totals.append(total)

    if len(totals) < window:
        return {
            "is_regression": False,
            "rolling_avg": None,
            "delta": 0,
            "window_size": len(totals),
        }

    recent = totals[-window:]
    avg = sum(recent) / len(recent)
    delta = latest_total - avg

    return {
        "is_regression": delta < -threshold,
        "rolling_avg": round(avg, 2),
        "delta": round(delta, 2),
        "window_size": len(recent),
    }
