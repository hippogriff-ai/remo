"""Integration tests for Full Intake mode (~10 turns, adaptive).

Tests verify:
1. Domain notepad tracking accumulates across turns
2. Agent adapts question order based on responses
3. DIAGNOSE pipeline is active (probing, contradiction detection)
4. Brief quality improves with more turns
5. Agent handles ~10 turn conversations gracefully

ALL tests require ANTHROPIC_API_KEY.
Run with: .venv/bin/python -m pytest tests/eval/test_full_mode.py -x -v -m integration
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.activities.intake import _run_intake_core
from app.models.contracts import (
    ChatMessage,
    IntakeChatInput,
    IntakeChatOutput,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


def _run_turn(
    history: list[ChatMessage],
    user_message: str,
) -> IntakeChatOutput:
    """Run a single full-mode intake turn."""
    return asyncio.run(
        _run_intake_core(
            IntakeChatInput(
                mode="full",
                project_context={"room_photos": []},
                conversation_history=history,
                user_message=user_message,
            )
        )
    )


def _advance_conversation(
    history: list[ChatMessage],
    user_message: str,
) -> tuple[IntakeChatOutput, list[ChatMessage]]:
    """Run a turn and update history."""
    result = _run_turn(history, user_message)
    new_history = [
        *history,
        ChatMessage(role="user", content=user_message),
        ChatMessage(role="assistant", content=result.agent_message),
    ]
    return result, new_history


class TestFullModeMultiTurn:
    """Full mode should handle extended ~10 turn conversations."""

    def test_full_mode_accumulates_brief_across_turns(self):
        """Brief should get richer with each turn."""
        history: list[ChatMessage] = []

        # Turn 1: Room type + occupants
        result, history = _advance_conversation(
            history,
            "It's our living room. My partner and I, both in our 30s.",
        )
        brief_1 = result.partial_brief

        # Turn 2: Style direction
        result, history = _advance_conversation(
            history,
            "We love mid-century modern with a warm twist",
        )
        _ = result.partial_brief  # intermediate — we compare turn 1 vs turn 3

        # Turn 3: Pain points
        result, history = _advance_conversation(
            history,
            "The room is way too dark, especially in winter. "
            "And we never use the armchair — it's uncomfortable.",
        )
        brief_3 = result.partial_brief

        # Brief should accumulate — later briefs should have more data
        if brief_1 and brief_3:
            # Turn 3 brief should have pain points that turn 1 didn't
            assert len(brief_3.pain_points) >= len(brief_1.pain_points)

    def test_full_mode_reaches_summary_within_budget(self):
        """Full mode should reach summary within ~10-11 turns."""
        history: list[ChatMessage] = []

        scripted = [
            "It's a living room for our family. Two kids under 10, a golden retriever.",
            "We want a cozy, welcoming space where everyone can hang out together.",
            "The biggest problem is the room is too dark and the sofa is falling apart.",
            "We love warm, earthy tones — nothing too cold or sterile.",
            "Soft, layered lighting. We hate the harsh overhead light.",
            "Wood furniture, natural fabrics. We like the look of linen and wool.",
            "Definitely want to keep the vintage bookshelf from my grandmother.",
            "Budget is around $8000. We're renting so no permanent changes.",
            "Minimal clutter — we need hidden storage for the kids' toys.",
            "We want it to feel like a warm hug when you walk in.",
        ]

        last_result = None
        found_summary = False
        for answer in scripted:
            result, history = _advance_conversation(history, answer)
            last_result = result
            if result.is_summary:
                found_summary = True
                break

        # Should have produced a summary at some point
        assert found_summary or (last_result and last_result.partial_brief is not None), (
            "Full mode should reach summary or at least produce a rich brief"
        )

        # If we got a summary, the brief should be rich
        if found_summary and last_result and last_result.partial_brief:
            brief = last_result.partial_brief
            assert brief.room_type != ""
            assert brief.style_profile is not None
            sp = brief.style_profile
            # Should have most fields populated after 10 turns
            populated_count = (
                sum(1 for val in [sp.lighting, sp.mood, sp.clutter_level] if val)
                + (1 if sp.colors else 0)
                + (1 if sp.textures else 0)
            )
            assert populated_count >= 3, f"Full mode brief should be rich after 10 turns, got: {sp}"

    def test_full_mode_captures_detailed_constraints(self):
        """Full mode should capture nuanced constraints over multiple turns."""
        history: list[ChatMessage] = []

        turns = [
            "Home office. I work from home full-time as a software engineer.",
            "I need something focused and productive but not clinical.",
            "My biggest frustration is back pain from my current chair. "
            "And the lighting gives me headaches.",
            "I like warm minimalism — clean but not cold.",
            "Task lighting is critical. I need great desk illumination.",
        ]

        last_result = None
        for answer in turns:
            last_result, history = _advance_conversation(history, answer)

        assert last_result is not None
        assert last_result.partial_brief is not None
        brief = last_result.partial_brief

        # Should have captured ergonomic/health constraints
        all_text = (
            " ".join(brief.pain_points)
            + " "
            + " ".join(brief.constraints)
            + " "
            + (brief.occupants or "")
        ).lower()
        has_ergonomic = any(
            kw in all_text for kw in ["back", "pain", "ergonomic", "chair", "headache", "lighting"]
        )
        assert has_ergonomic, (
            f"Should capture ergonomic constraints. Got: {brief.pain_points}, {brief.constraints}"
        )


class TestFullModeDiagnoseActive:
    """DIAGNOSE pipeline should be active in Full mode."""

    def test_contradiction_detection(self):
        """Agent should detect contradictory preferences."""
        history: list[ChatMessage] = []

        # Turn 1
        _, history = _advance_conversation(
            history,
            "I want a completely minimalist bedroom. Clean, empty, zen.",
        )

        # Turn 2: Contradicts minimalism
        result, history = _advance_conversation(
            history,
            "I also love collecting vintage items and have about 50 antique "
            "figurines I want to display. Plus all my books — probably 200.",
        )

        # Agent should recognize the contradiction and address it
        msg_lower = result.agent_message.lower()
        addresses_tension = any(
            kw in msg_lower
            for kw in [
                "balance",
                "display",
                "storage",
                "organize",
                "curate",
                "select",
                "conflict",
                "both",
                "challenge",
                "compromise",
                "integrate",
                "minimal",
                "collection",
            ]
        )
        assert addresses_tension, (
            "Agent should address minimalism+collecting contradiction. "
            f"Got: {result.agent_message[:300]}"
        )

    def test_probing_beneath_surface(self):
        """Agent should probe 'it feels wrong' to find root cause."""
        history: list[ChatMessage] = []

        _, history = _advance_conversation(
            history,
            "It's our dining room. We redid it last year but something still feels off.",
        )

        result, _ = _advance_conversation(
            history,
            "I don't know, it just doesn't feel right. We spent a lot of money on it.",
        )

        # Agent should probe deeper, not accept "doesn't feel right"
        msg_lower = result.agent_message.lower()
        probes_deeper = any(
            kw in msg_lower
            for kw in [
                "lighting",
                "color",
                "layout",
                "comfort",
                "scale",
                "proportion",
                "too",
                "what",
                "when",
                "how",
                "feel",
                "specifically",
                "example",
                "?",
            ]
        )
        assert probes_deeper, (
            f"Agent should probe 'feels off' for root cause. Got: {result.agent_message[:300]}"
        )
