"""Tests for score tracking and regression detection."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from app.utils.score_tracking import append_score, detect_regression, load_history


@pytest.fixture
def history_path(tmp_path: Path) -> Path:
    return tmp_path / "score_history.jsonl"


class TestAppendScore:
    def test_creates_file_if_not_exists(self, history_path: Path):
        record = append_score(
            history_path,
            scenario="living_room_mcm",
            prompt_version="v2",
            vlm_eval={"total": 75, "tag": "GOOD"},
        )
        assert history_path.exists()
        assert record["scenario"] == "living_room_mcm"
        assert record["prompt_version"] == "v2"

    def test_appends_to_existing(self, history_path: Path):
        append_score(history_path, "s1", "v1", vlm_eval={"total": 70})
        append_score(history_path, "s2", "v1", vlm_eval={"total": 80})
        lines = history_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_record_has_timestamp(self, history_path: Path):
        record = append_score(history_path, "s1", "v1")
        assert "timestamp" in record
        assert "T" in record["timestamp"]  # ISO format

    def test_record_has_model(self, history_path: Path):
        record = append_score(history_path, "s1", "v1", model="gemini-custom")
        assert record["model"] == "gemini-custom"

    def test_default_model(self, history_path: Path):
        record = append_score(history_path, "s1", "v1")
        assert record["model"] == "gemini-3-pro-image-preview"

    def test_vlm_eval_field(self, history_path: Path):
        record = append_score(
            history_path,
            "s1",
            "v5",
            vlm_eval={"total": 87, "tag": "EXCELLENT", "photorealism": 13},
        )
        assert record["vlm_eval"]["total"] == 87
        assert record["vlm_eval"]["tag"] == "EXCELLENT"

    def test_artifact_check_field(self, history_path: Path):
        record = append_score(
            history_path,
            "s1",
            "v5",
            artifact_check={"has_artifacts": False, "artifact_count": 0},
        )
        assert record["artifact_check"]["has_artifacts"] is False

    def test_backward_compat_fast_eval(self, history_path: Path):
        """Old-format fast_eval param still works."""
        record = append_score(
            history_path,
            "s1",
            "v2",
            fast_eval={"composite_score": 0.5},
        )
        assert record["fast_eval"]["composite_score"] == 0.5

    def test_backward_compat_deep_eval(self, history_path: Path):
        """Old-format deep_eval param still works."""
        record = append_score(
            history_path,
            "s1",
            "v2",
            deep_eval={"total": 75, "tag": "GOOD"},
        )
        assert record["deep_eval"]["total"] == 75

    def test_new_record_format(self, history_path: Path):
        """Full new-format record with vlm_eval + artifact_check."""
        append_score(
            history_path,
            "generation_option_0",
            "v5",
            vlm_eval={
                "total": 87,
                "tag": "EXCELLENT",
                "photorealism": 13,
                "instruction_adherence": 8,
                "spatial_accuracy": 4,
            },
            artifact_check={"has_artifacts": False, "artifact_count": 0},
        )
        # Verify serialized correctly
        line = history_path.read_text().strip()
        parsed = json.loads(line)
        assert parsed["vlm_eval"]["instruction_adherence"] == 8
        assert parsed["artifact_check"]["artifact_count"] == 0


class TestLoadHistory:
    def test_empty_file(self, history_path: Path):
        assert load_history(history_path) == []

    def test_loads_all_records(self, history_path: Path):
        append_score(history_path, "s1", "v1", vlm_eval={"total": 70})
        append_score(history_path, "s2", "v1", vlm_eval={"total": 80})
        records = load_history(history_path)
        assert len(records) == 2

    def test_filters_by_scenario(self, history_path: Path):
        append_score(history_path, "s1", "v1", vlm_eval={"total": 70})
        append_score(history_path, "s2", "v1", vlm_eval={"total": 80})
        append_score(history_path, "s1", "v1", vlm_eval={"total": 75})
        records = load_history(history_path, scenario="s1")
        assert len(records) == 2
        assert all(r["scenario"] == "s1" for r in records)

    def test_handles_corrupt_lines(self, history_path: Path):
        history_path.write_text('{"valid": true}\nnot json\n{"also": "valid"}\n')
        records = load_history(history_path)
        assert len(records) == 2


class TestDetectRegression:
    def test_insufficient_history(self, history_path: Path):
        append_score(history_path, "s1", "v1", vlm_eval={"total": 70})
        result = detect_regression(history_path, "s1", latest_total=60)
        assert result["is_regression"] is False
        assert result["rolling_avg"] is None

    def test_no_regression(self, history_path: Path):
        for total in [70, 72, 74, 71, 73]:
            append_score(history_path, "s1", "v1", vlm_eval={"total": total})
        result = detect_regression(history_path, "s1", latest_total=68)
        assert result["is_regression"] is False  # 72 - 68 = 4 < 10

    def test_detects_regression(self, history_path: Path):
        for total in [70, 72, 74, 71, 73]:
            append_score(history_path, "s1", "v1", vlm_eval={"total": total})
        # avg = 72, latest = 55, delta = -17 > threshold 10
        result = detect_regression(history_path, "s1", latest_total=55)
        assert result["is_regression"] is True
        assert result["delta"] < -10

    def test_custom_window_and_threshold(self, history_path: Path):
        for total in [80, 82, 78]:
            append_score(history_path, "s1", "v1", vlm_eval={"total": total})
        # avg = 80, latest = 74, delta = -6 < -5 → regression
        result = detect_regression(history_path, "s1", latest_total=74, window=3, threshold=5)
        assert result["is_regression"] is True

    def test_filters_by_scenario(self, history_path: Path):
        for total in [70, 72, 74, 71, 73]:
            append_score(history_path, "s1", "v1", vlm_eval={"total": total})
        for total in [50, 52, 54, 51, 53]:
            append_score(history_path, "s2", "v1", vlm_eval={"total": total})
        result = detect_regression(history_path, "s1", latest_total=60)
        # s1 avg = 72, 72-60 = 12 > 10 → regression
        assert result["is_regression"] is True

    def test_rolling_avg_value(self, history_path: Path):
        for total in [70, 72, 74, 71, 73]:
            append_score(history_path, "s1", "v1", vlm_eval={"total": total})
        result = detect_regression(history_path, "s1", latest_total=72)
        assert result["rolling_avg"] == 72.0

    def test_backward_compat_reads_deep_eval(self, history_path: Path):
        """detect_regression reads from old deep_eval.total when vlm_eval is missing."""
        for total in [70, 72, 74, 71, 73]:
            append_score(history_path, "s1", "v1", deep_eval={"total": total})
        result = detect_regression(history_path, "s1", latest_total=55)
        assert result["is_regression"] is True
        assert result["rolling_avg"] == 72.0

    def test_prefers_vlm_eval_over_deep_eval(self, history_path: Path):
        """When both vlm_eval and deep_eval exist, prefer vlm_eval."""
        for total in [70, 72, 74, 71, 73]:
            append_score(
                history_path,
                "s1",
                "v1",
                vlm_eval={"total": total + 10},
                deep_eval={"total": total},
            )
        # vlm_eval totals: 80, 82, 84, 81, 83 → avg = 82
        result = detect_regression(history_path, "s1", latest_total=82)
        assert result["rolling_avg"] == 82.0
