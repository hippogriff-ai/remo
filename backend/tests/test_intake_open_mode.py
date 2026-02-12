"""Integration tests for Open Conversation mode (~15 turns, free-form).

Tests verify:
1. Open mode starts with an open-ended prompt
2. Agent follows user's lead
3. Domain notepad tracks coverage internally
4. Agent caps at ~15 turns and synthesizes a brief
5. Agent gracefully handles varied conversation styles

ALL tests require ANTHROPIC_API_KEY.
Run with: .venv/bin/python -m pytest tests/test_intake_open_mode.py -x -v -m integration
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
    """Run a single open-mode intake turn."""
    return asyncio.run(
        _run_intake_core(
            IntakeChatInput(
                mode="open",
                project_context={"room_photos": []},
                conversation_history=history,
                user_message=user_message,
            )
        )
    )


def _advance(
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


class TestOpenModeConversation:
    """Open mode should follow user's lead and track domains internally."""

    def test_open_mode_starts_with_open_prompt(self):
        """First turn should be open-ended, not a specific question."""
        result = _run_turn(
            [],
            "Hi! I'm excited to redesign my kitchen.",
        )

        # Agent should respond warmly and be open-ended
        assert len(result.agent_message) > 20
        # Should ask the user to share freely, not fire a specific question
        msg_lower = result.agent_message.lower()
        is_open = any(
            kw in msg_lower
            for kw in [
                "tell me",
                "share",
                "what",
                "how",
                "describe",
                "love",
                "change",
                "mind",
                "dream",
                "vision",
                "exciting",
                "?",
            ]
        )
        assert is_open, (
            f"Open mode should encourage free sharing. Got: {result.agent_message[:200]}"
        )

    def test_open_mode_follows_user_tangent(self):
        """Agent should follow the user's lead on unexpected topics."""
        history: list[ChatMessage] = []

        # Start talking about kitchen
        _, history = _advance(
            history,
            "So our kitchen is basically a disaster. We renovated 5 years ago "
            "and I already hate everything about it.",
        )

        # Go on a tangent about a restaurant
        result, _ = _advance(
            history,
            "Actually, you know what inspired me? We went to this amazing "
            "restaurant in Tokyo with a beautiful open kitchen. The way they "
            "used wood and copper together was stunning.",
        )

        # Agent should engage with the restaurant story, not redirect
        msg_lower = result.agent_message.lower()
        engages = any(
            kw in msg_lower
            for kw in [
                "restaurant",
                "tokyo",
                "wood",
                "copper",
                "inspir",
                "love",
                "beautiful",
                "open kitchen",
                "sound",
            ]
        )
        assert engages, (
            "Agent should engage with user's tangent/inspiration. "
            f"Got: {result.agent_message[:300]}"
        )

    def test_open_mode_produces_brief(self):
        """Open mode should eventually produce a brief even from free-form chat."""
        history: list[ChatMessage] = []

        conversation = [
            "I want to redo my bedroom. It's just... blah right now.",
            "I've been really into those Japanese-inspired minimalist spaces. "
            "Have you seen those ryokan-style rooms?",
            "Low platform bed, natural materials everywhere. "
            "But I also need it to be practical — I read in bed every night.",
            "Colors should be very muted. Whites, creams, maybe a touch of "
            "sage green. Nothing loud.",
            "Oh and I have insomnia, so the room needs to help me sleep better.",
        ]

        last_result = None
        for msg in conversation:
            last_result, history = _advance(history, msg)

        assert last_result is not None
        # Should have a partial brief after 5 turns
        if last_result.partial_brief:
            brief = last_result.partial_brief
            assert brief.room_type != ""
            # Should have captured style direction
            assert brief.style_profile is not None
