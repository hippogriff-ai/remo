"""Live eval pipeline — runs intake agent through scripted scenarios, then judges.

Runs each EvalScenario through _run_intake_core() (real Claude calls), then
evaluates the resulting brief + transcript with evaluate_brief() and
evaluate_conversation_quality().

Prints a rich dashboard with per-scenario and per-criterion scores, then
appends results to score_history.jsonl for tracking across iterations.

ALL tests are marked @pytest.mark.integration — they require ANTHROPIC_API_KEY.
Run with:
  .venv/bin/python -m pytest tests/eval/test_live_eval.py -x -v -m integration -s
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.activities.intake import _run_intake_core
from app.activities.intake_eval import (
    evaluate_brief,
    evaluate_conversation_quality,
    score_tag,
)
from app.models.contracts import (
    ChatMessage,
    IntakeChatInput,
    IntakeChatOutput,
)
from tests.eval.scenarios import CRITERION_MAX, SCENARIOS, EvalScenario

SCORE_HISTORY_FILE = Path(__file__).parent / "score_history.jsonl"

# Skip all tests if no API key
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


def _run_scenario(
    scenario: EvalScenario,
) -> tuple[
    IntakeChatOutput | None,
    list[dict[str, str]],
]:
    """Run a full conversation for a scenario, returning final output + transcript.

    Passes previous_brief in project_context to match real workflow behavior.
    """
    history: list[ChatMessage] = []
    result: IntakeChatOutput | None = None
    previous_brief: dict[str, Any] | None = None

    for user_msg in scenario.messages:
        project_context: dict[str, Any] = {"room_photos": []}
        if previous_brief is not None:
            project_context["previous_brief"] = previous_brief

        result = asyncio.run(
            _run_intake_core(
                IntakeChatInput(
                    mode=scenario.mode,
                    project_context=project_context,
                    conversation_history=history,
                    user_message=user_msg,
                )
            )
        )

        history.append(ChatMessage(role="user", content=user_msg))
        history.append(ChatMessage(role="assistant", content=result.agent_message))

        # Accumulate brief for next turn
        if result.partial_brief is not None:
            previous_brief = result.partial_brief.model_dump()

    # Build transcript as list of dicts for the judge
    transcript = [{"role": m.role, "content": m.content} for m in history]
    return result, transcript


def _print_dashboard(
    all_results: list[dict[str, Any]],
    convo_results: list[dict[str, Any]],
) -> None:
    """Print a rich dashboard summarizing eval results."""
    print("\n" + "=" * 72)
    print("  LIVE EVAL DASHBOARD")
    print("=" * 72)

    # Per-scenario brief scores
    print("\n--- Brief Quality (per scenario) ---")
    print(f"{'Scenario':<30} {'Total':>6} {'Tag':<16} {'Notes'}")
    print("-" * 72)
    for r in all_results:
        tag = score_tag(int(r.get("total", 0)))
        notes = r.get("notes", "")[:60]
        print(f"{r['scenario_id']:<30} {r.get('total', 0):>6} {tag:<16} {notes}")

    # Per-criterion averages
    print("\n--- Brief Quality (per criterion avg) ---")
    criterion_sums: dict[str, float] = {}
    criterion_counts: dict[str, int] = {}
    for r in all_results:
        for key in CRITERION_MAX:
            if key in r:
                criterion_sums[key] = criterion_sums.get(key, 0) + float(r[key])
                criterion_counts[key] = criterion_counts.get(key, 0) + 1

    print(f"{'Criterion':<25} {'Avg':>6} {'Max':>6} {'%':>6}")
    print("-" * 50)
    weakest_criterion = ""
    weakest_pct = 100.0
    for key in CRITERION_MAX:
        if key in criterion_sums and criterion_counts[key] > 0:
            avg = criterion_sums[key] / criterion_counts[key]
            max_val = CRITERION_MAX[key]
            pct = (avg / max_val) * 100
            print(f"{key:<25} {avg:>6.1f} {max_val:>6} {pct:>5.0f}%")
            if pct < weakest_pct:
                weakest_pct = pct
                weakest_criterion = key

    if weakest_criterion:
        print(f"\n  >> Weakest criterion: {weakest_criterion} ({weakest_pct:.0f}%)")

    # Overall averages
    totals = [float(r.get("total", 0)) for r in all_results]
    avg_total = sum(totals) / len(totals) if totals else 0
    min_total = min(totals) if totals else 0
    max_total = max(totals) if totals else 0
    print(f"\n  Overall: avg={avg_total:.1f}  min={min_total:.0f}  max={max_total:.0f}")

    # Conversation quality (supplementary)
    if convo_results:
        print("\n--- Conversation Quality (supplementary) ---")
        convo_criteria = [
            "probing_quality",
            "acknowledgment",
            "adaptation",
            "translation_visibility",
            "conversational_flow",
        ]
        print(f"{'Scenario':<30} ", end="")
        for c in convo_criteria:
            print(f"{c[:8]:>9}", end="")
        print(f"{'total':>7}")
        print("-" * 72)
        for r in convo_results:
            print(f"{r['scenario_id']:<30} ", end="")
            for c in convo_criteria:
                print(f"{r.get(c, 0):>9}", end="")
            print(f"{r.get('total', 0):>7}")

    # Weakest scenario
    if all_results:
        weakest_scenario = min(all_results, key=lambda r: float(r.get("total", 0)))
        print(
            f"\n  >> Weakest scenario: {weakest_scenario['scenario_id']}"
            f" ({weakest_scenario.get('total', 0)})"
        )

    print("\n" + "=" * 72)


def _append_score_history(
    all_results: list[dict[str, Any]],
    convo_results: list[dict[str, Any]],
) -> None:
    """Append results to score_history.jsonl."""
    entry = {
        "timestamp": datetime.now(UTC).isoformat(),
        "brief_scores": all_results,
        "conversation_scores": convo_results,
        "brief_avg": (
            sum(float(r.get("total", 0)) for r in all_results) / len(all_results)
            if all_results
            else 0
        ),
    }
    with open(SCORE_HISTORY_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


class TestLiveEval:
    """Run all 6 scenarios and evaluate."""

    def test_all_scenarios(self):
        """Run all scenarios, evaluate, print dashboard, assert thresholds."""
        all_brief_results: list[dict[str, Any]] = []
        all_convo_results: list[dict[str, Any]] = []

        for scenario in SCENARIOS:
            print(f"\n>>> Running scenario: {scenario.id} ({scenario.name})")
            result, transcript = _run_scenario(scenario)

            assert result is not None, f"Scenario {scenario.id} returned no result"

            # Evaluate brief quality
            brief_scores: dict[str, Any] = {}
            if result.partial_brief is not None:
                brief_scores = evaluate_brief(result.partial_brief, transcript)
            else:
                # No brief produced — score 0
                brief_scores = {k: 0 for k in CRITERION_MAX}
                brief_scores["total"] = 0
                brief_scores["notes"] = "No brief produced"
            brief_scores["scenario_id"] = scenario.id
            all_brief_results.append(brief_scores)

            # Evaluate conversation quality (supplementary)
            try:
                convo_scores = evaluate_conversation_quality(transcript)
                convo_scores["scenario_id"] = scenario.id
                all_convo_results.append(convo_scores)
            except Exception as e:
                print(f"  Conversation eval failed for {scenario.id}: {e}")

            print(
                f"  Brief: {brief_scores.get('total', 0)}"
                f" [{score_tag(int(brief_scores.get('total', 0)))}]"
            )

        # Print dashboard
        _print_dashboard(all_brief_results, all_convo_results)

        # Append to score history
        _append_score_history(all_brief_results, all_convo_results)

        # Assertions
        for r in all_brief_results:
            total = float(r.get("total", 0))
            assert total >= 70, (
                f"Scenario {r['scenario_id']} scored {total} < 70: {r.get('notes', '')}"
            )

            # Check per-scenario min_scores if defined
            scenario = next(
                (s for s in SCENARIOS if s.id == r["scenario_id"]),
                None,
            )
            if scenario and scenario.min_scores:
                for criterion, min_val in scenario.min_scores.items():
                    actual = float(r.get(criterion, 0))
                    assert actual >= min_val, (
                        f"Scenario {scenario.id}: {criterion}={actual} < {min_val}"
                    )

        # Overall average
        avg = sum(float(r.get("total", 0)) for r in all_brief_results) / len(all_brief_results)
        print(f"\n  OVERALL AVG: {avg:.1f}")
        assert avg >= 70, f"Overall average {avg:.1f} < 70"
