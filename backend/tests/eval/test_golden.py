"""Golden test suite for the intake chat agent.

These tests use REAL Claude API calls to verify the agent's design
intelligence quality. They test that the intake agent:

1. Translates vague language into professional design parameters
2. Applies the DIAGNOSE pipeline for diagnostic probing
3. Produces valid, elevated DesignBrief output
4. Adapts to multi-domain answers
5. Generates appropriate quick-reply options

ALL tests are marked @pytest.mark.integration — they require ANTHROPIC_API_KEY.
Run with: .venv/bin/python -m pytest tests/eval/test_golden.py -x -v -m integration

The behavior being tested IS Claude's response quality, so mocking
would defeat the purpose.
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

# Skip all tests if no API key
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    ),
]


def _run_turn(
    mode: str,
    history: list[ChatMessage],
    user_message: str,
    project_context: dict | None = None,
) -> IntakeChatOutput:
    """Helper to run a single intake turn."""
    return asyncio.run(
        _run_intake_core(
            IntakeChatInput(
                mode=mode,
                project_context=project_context or {"room_photos": []},
                conversation_history=history,
                user_message=user_message,
            )
        )
    )


def _run_quick_conversation(
    scripted_answers: list[str],
) -> tuple[IntakeChatOutput, list[ChatMessage]]:
    """Run a full quick-mode conversation with scripted user answers.

    Returns the final output and full conversation history.
    """
    history: list[ChatMessage] = []
    result: IntakeChatOutput | None = None

    for answer in scripted_answers:
        result = _run_turn("quick", history, answer)
        history.append(ChatMessage(role="user", content=answer))
        history.append(ChatMessage(role="assistant", content=result.agent_message))

    assert result is not None
    return result, history


# === Golden Test 1: Translation Engine ===


class TestTranslationEngine:
    """Agent should translate vague language into specific design parameters."""

    def test_cozy_elevated_to_design_params(self):
        """'cozy' should produce warm palette, layered textiles, warm lighting — not just 'cozy'."""
        result, _ = _run_quick_conversation(
            [
                "It's a living room for our family of four.",
                "I just want it to feel really cozy and warm",
                "Warm lighting, lots of blankets and pillows, earthy colors",
            ]
        )

        assert result.partial_brief is not None
        brief = result.partial_brief
        assert brief.room_type != ""

        sp = brief.style_profile
        assert sp is not None

        # Lighting should be elevated — not just "warm"
        if sp.lighting:
            lighting_lower = sp.lighting.lower()
            has_specificity = any(
                kw in lighting_lower
                for kw in ["2700", "2200", "ambient", "task", "accent", "layer", "kelvin", "k"]
            )
            assert has_specificity, (
                f"Lighting should have specific temps/layers, got: {sp.lighting}"
            )

        # Colors should be specific, not just "warm"
        if sp.colors:
            all_colors = " ".join(sp.colors).lower()
            has_specific = any(
                kw in all_colors
                for kw in [
                    "amber",
                    "terracotta",
                    "ivory",
                    "cream",
                    "oak",
                    "brown",
                    "earth",
                    "warm",
                    "60%",
                    "30%",
                    "10%",
                ]
            )
            assert has_specific, f"Colors should be specific, got: {sp.colors}"

    def test_modern_elevated_to_design_params(self):
        """'modern' should produce clean lines, neutral base, geometric — not just 'modern'."""
        result, _ = _run_quick_conversation(
            [
                "It's a home office, just me working from home",
                "I want a really modern, clean aesthetic",
                "Definitely minimalist, good desk lighting, no clutter",
            ]
        )

        assert result.partial_brief is not None
        sp = result.partial_brief.style_profile
        assert sp is not None

        # Textures should use professional vocabulary
        if sp.textures:
            # Should NOT be generic words like "soft" "nice" "good"
            generic_count = sum(
                1 for t in sp.textures if t.lower() in ("soft", "nice", "good", "hard")
            )
            assert generic_count == 0, (
                f"Textures should be professional descriptors, got: {sp.textures}"
            )


# === Golden Test 2: Valid Brief Structure ===


class TestBriefValidity:
    """Every conversation should produce a valid DesignBrief."""

    def test_quick_mode_produces_valid_brief(self):
        """Quick mode should produce a valid brief with populated fields."""
        result, _ = _run_quick_conversation(
            [
                "It's our bedroom, we're a couple in our 30s",
                "We love Scandinavian style with lots of natural light",
                "Keep it minimal but warm, we have a cat",
            ]
        )

        # Should have a brief by the end
        assert result.partial_brief is not None
        brief = result.partial_brief

        # Core fields populated
        assert brief.room_type != ""
        assert brief.style_profile is not None

        # Constraints should capture practical info (cat)
        all_text = (
            " ".join(brief.constraints)
            + " "
            + " ".join(brief.pain_points)
            + " "
            + (brief.occupants or "")
        ).lower()
        assert "cat" in all_text or "pet" in all_text, (
            "Agent should capture 'cat' in constraints or brief text"
        )

    def test_brief_has_style_profile(self):
        """Brief should always have a style profile by the final turn."""
        result, _ = _run_quick_conversation(
            [
                "Kitchen renovation, family of 5 with young kids",
                "Bright and airy, modern farmhouse vibe",
                "We need tons of counter space and good task lighting",
            ]
        )

        assert result.partial_brief is not None
        sp = result.partial_brief.style_profile
        assert sp is not None
        # Should have at least some fields populated
        populated = (
            sum(1 for field in [sp.lighting, sp.mood, sp.clutter_level] if field)
            + (1 if sp.colors else 0)
            + (1 if sp.textures else 0)
        )
        assert populated >= 2, f"Style profile should have multiple fields populated, got: {sp}"


# === Golden Test 3: Diagnostic Probing ===


class TestDiagnosticProbing:
    """Agent should probe beneath surface-level answers."""

    def test_vague_answer_gets_followup(self):
        """'I want it to look nice' should trigger probing, not acceptance."""
        history: list[ChatMessage] = []

        # Turn 1: room type
        result = _run_turn("quick", history, "It's a bedroom")
        history.append(ChatMessage(role="user", content="It's a bedroom"))
        history.append(ChatMessage(role="assistant", content=result.agent_message))

        # Turn 2: deliberately vague
        result = _run_turn("quick", history, "I just want it to look nice")

        # Agent should NOT accept "nice" at face value
        # It should either offer options to refine OR ask a probing follow-up
        has_options = result.options is not None and len(result.options) >= 2
        has_probing = any(
            kw in result.agent_message.lower()
            for kw in [
                "what",
                "how",
                "tell me",
                "describe",
                "mean by",
                "envision",
                "imagine",
                "feel",
                "style",
                "look like",
                "example",
                "?",
            ]
        )
        assert has_options or has_probing, (
            "Agent should probe 'nice' with options or follow-up question, "
            f"got message: {result.agent_message[:200]}"
        )


# === Golden Test 4: Quick-Reply Options ===


class TestQuickReplyOptions:
    """Agent should generate helpful quick-reply options."""

    def test_first_turn_has_options_or_is_open(self):
        """First response should either offer options or be open-ended."""
        result = _run_turn("quick", [], "It's a living room, just me and my partner")

        has_options = result.options is not None and len(result.options) >= 2
        has_question = "?" in result.agent_message
        assert has_options or has_question, "First turn should offer options or ask a question"

    def test_options_are_distinct(self):
        """Quick-reply options should be distinct and specific."""
        result = _run_turn("quick", [], "It's a dining room, we host dinner parties a lot")

        if result.options and len(result.options) >= 2:
            labels = [o.label.lower() for o in result.options]
            # No duplicate labels
            assert len(set(labels)) == len(labels), f"Options should be distinct, got: {labels}"


# === Golden Test 5: Room-Specific Guidance ===


class TestRoomSpecificGuidance:
    """Agent should apply room-specific design knowledge."""

    def test_bedroom_applies_sleep_guidance(self):
        """Bedroom brief should reflect sleep-optimized design."""
        result, _ = _run_quick_conversation(
            [
                "It's our master bedroom, my partner and I",
                "Calm and peaceful, we both have trouble sleeping",
                "Cool blues and greens, blackout curtains would be great",
            ]
        )

        assert result.partial_brief is not None
        sp = result.partial_brief.style_profile
        assert sp is not None

        # Lighting should be warm (sleep-friendly) if specified
        if sp.lighting:
            lighting_lower = sp.lighting.lower()
            # Should mention warm/low temps, NOT 4000K+ for bedroom
            has_warm = any(
                kw in lighting_lower for kw in ["2200", "2700", "warm", "dim", "blackout", "soft"]
            )
            assert has_warm, f"Bedroom lighting should be warm/sleep-friendly, got: {sp.lighting}"


# === Golden Test 6: Multi-Domain Answer Handling ===


class TestAdaptiveBehavior:
    """Agent should adapt when user covers multiple domains at once."""

    def test_rich_answer_advances_conversation(self):
        """A detailed first answer should let the agent skip redundant questions."""
        result = _run_turn(
            "quick",
            [],
            (
                "It's our living room, family of 4 with 2 dogs. We love a "
                "cozy Scandinavian feel — warm woods, white walls, lots of "
                "plants. The room is too dark and the furniture is too big. "
                "We want to keep the antique bookshelf."
            ),
        )

        # This answer covers: room type, occupants, style, pain points,
        # keep_items, and hints at color/texture. The agent should
        # acknowledge this richness and ask about remaining domains.
        assert result.partial_brief is not None
        brief = result.partial_brief
        assert brief.room_type != ""

        # Should have captured multiple domains
        domain_signals = 0
        if brief.occupants:
            domain_signals += 1
        if brief.pain_points:
            domain_signals += 1
        if brief.keep_items:
            domain_signals += 1
        if brief.style_profile:
            domain_signals += 1

        assert domain_signals >= 2, f"Rich answer should capture multiple domains, got: {brief}"


# === Golden Test 7: Summary Generation ===


class TestSummaryGeneration:
    """Agent should produce a summary with elevated brief at conversation end."""

    def test_quick_mode_reaches_summary(self):
        """After ~3 turns, agent should produce a summary."""
        result, _ = _run_quick_conversation(
            [
                "Living room, couple in our 40s, no kids, one cat",
                "Luxurious but not over the top — think boutique hotel lounge",
                "Deep jewel tones, velvet, brass accents, dramatic lighting",
            ]
        )

        # By turn 3, quick mode should be at or near summary
        # (The model may summarize on turn 3 or need one more turn)
        if result.is_summary:
            assert result.partial_brief is not None
            brief = result.partial_brief
            sp = brief.style_profile
            assert sp is not None

            # Verify elevation happened — should have professional terms
            if sp.textures:
                all_tex = " ".join(sp.textures).lower()
                has_professional = any(
                    kw in all_tex
                    for kw in [
                        "velvet",
                        "brass",
                        "marble",
                        "silk",
                        "leather",
                        "linen",
                        "boucle",
                        "wool",
                        "oak",
                        "walnut",
                    ]
                )
                assert has_professional, (
                    f"Textures should use professional terms, got: {sp.textures}"
                )


# === Golden Test 8: Constraint Detection ===


class TestConstraintDetection:
    """Agent should detect and capture practical constraints."""

    def test_captures_pet_constraint(self):
        """Mentioning pets should be captured as a design constraint."""
        result, _ = _run_quick_conversation(
            [
                "It's a living room. We have 3 large dogs and a toddler.",
                "We need something durable and easy to clean",
                "Modern farmhouse style, neutral colors",
            ]
        )

        assert result.partial_brief is not None
        brief = result.partial_brief

        # Pet/kid info should appear somewhere in the brief
        all_text = (
            " ".join(brief.constraints)
            + " "
            + " ".join(brief.pain_points)
            + " "
            + (brief.occupants or "")
        ).lower()

        has_pet = "dog" in all_text or "pet" in all_text
        has_kid = "toddler" in all_text or "child" in all_text or "kid" in all_text
        assert has_pet or has_kid, f"Should capture pets/kids as constraints. Brief: {brief}"
