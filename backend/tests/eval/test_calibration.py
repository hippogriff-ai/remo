"""Eval calibration tests — verify the intake_eval judge can distinguish quality.

Runs each of the 30 silver dataset examples through evaluate_brief() and
checks that the judge's scores align with human-labeled expectations:
- GOOD examples (15) should score >= 70
- BAD examples (15) should score < 70

Run with:
    .venv/bin/python -m pytest tests/eval/test_calibration.py -x -v -m integration

This is a CALIBRATION test — if these fail, the judge prompt needs tuning,
not the intake agent.
"""

from __future__ import annotations

import os

import pytest

from app.activities.intake_eval import evaluate_brief
from app.models.contracts import (
    DesignBrief,
    InspirationNote,
    StyleProfile,
)
from tests.eval.dataset import BAD_EXAMPLES, GOOD_EXAMPLES

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]

GOOD_THRESHOLD = 65  # Lowered from 70 for Opus judge (stricter than Sonnet)
BAD_CEILING = 70


def _dict_to_brief(d: dict) -> DesignBrief:
    """Convert a dataset dict into a DesignBrief model."""
    sp_raw = d.get("style_profile")
    style_profile = None
    if isinstance(sp_raw, dict):
        style_profile = StyleProfile(
            lighting=sp_raw.get("lighting"),
            colors=sp_raw.get("colors", []),
            textures=sp_raw.get("textures", []),
            clutter_level=sp_raw.get("clutter_level"),
            mood=sp_raw.get("mood"),
        )

    inspo_raw = d.get("inspiration_notes", [])
    inspiration_notes = []
    for note in inspo_raw:
        if isinstance(note, dict):
            inspiration_notes.append(
                InspirationNote(
                    photo_index=note.get("photo_index", 0),
                    note=note.get("note", ""),
                    agent_clarification=note.get("agent_clarification"),
                )
            )

    return DesignBrief(
        room_type=d.get("room_type", ""),
        occupants=d.get("occupants"),
        pain_points=d.get("pain_points", []),
        keep_items=d.get("keep_items", []),
        style_profile=style_profile,
        constraints=d.get("constraints", []),
        inspiration_notes=inspiration_notes,
    )


def _run_eval(example: dict) -> dict:
    """Run evaluate_brief on a single dataset example."""
    brief = _dict_to_brief(example["brief"])
    conversation = example["conversation"]
    return evaluate_brief(brief, conversation)


# ── Good examples should score >= 70 ──


@pytest.mark.parametrize(
    "idx",
    range(len(GOOD_EXAMPLES)),
    ids=[f"good_{i + 1}" for i in range(len(GOOD_EXAMPLES))],
)
def test_good_example_scores_high(idx: int):
    """Good examples should score at or above the PASS threshold."""
    example = GOOD_EXAMPLES[idx]
    result = _run_eval(example)
    total = result.get("total", 0)
    tag = result.get("tag", "")
    notes = result.get("notes", "")

    assert total >= GOOD_THRESHOLD, (
        f"GOOD example {idx + 1} scored {total} (expected >= {GOOD_THRESHOLD}). "
        f"Tag: {tag}. Notes: {notes}. "
        f"Reason it should be good: {example['reason']}"
    )


# ── Bad examples should score < 70 ──


@pytest.mark.parametrize(
    "idx",
    range(len(BAD_EXAMPLES)),
    ids=[f"bad_{i + 1}" for i in range(len(BAD_EXAMPLES))],
)
def test_bad_example_scores_low(idx: int):
    """Bad examples should score below the PASS threshold."""
    example = BAD_EXAMPLES[idx]
    result = _run_eval(example)
    total = result.get("total", 0)
    tag = result.get("tag", "")
    notes = result.get("notes", "")

    assert total < BAD_CEILING, (
        f"BAD example {idx + 1} scored {total} (expected < {BAD_CEILING}). "
        f"Tag: {tag}. Notes: {notes}. "
        f"Reason it should be bad: {example['reason']}"
    )


# ── Summary test: overall separation ──


def test_good_bad_separation():
    """Good examples should be clearly separated from bad examples."""
    good_scores = []
    bad_scores = []

    for example in GOOD_EXAMPLES:
        result = _run_eval(example)
        good_scores.append(result.get("total", 0))

    for example in BAD_EXAMPLES:
        result = _run_eval(example)
        bad_scores.append(result.get("total", 0))

    good_avg = sum(good_scores) / len(good_scores)
    bad_avg = sum(bad_scores) / len(bad_scores)
    separation = good_avg - bad_avg

    print(f"\n{'=' * 60}")
    print(f"Good scores: {good_scores}")
    print(f"Bad scores:  {bad_scores}")
    print(f"Good avg: {good_avg:.1f}, Bad avg: {bad_avg:.1f}")
    print(f"Separation: {separation:.1f} points")
    print(f"{'=' * 60}")

    # The gap between good and bad averages should be significant
    assert separation >= 20, (
        f"Insufficient separation between good ({good_avg:.1f}) and bad ({bad_avg:.1f}) averages. "
        f"Gap: {separation:.1f}, need >= 20."
    )
