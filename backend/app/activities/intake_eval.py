"""Intake eval harness — automated DesignBrief quality scoring.

Uses a separate Claude call to score a DesignBrief against a rubric.
This is NOT a Temporal activity — it's a testing/eval utility used by
the golden test suite to measure prompt quality.

Rubric (100 points total):
  Style Coherence:          10
  Color Strategy:           15
  Lighting Design:          15
  Material & Texture:       15
  Design Intelligence:      10
  Diagnostic Depth:          5
  Actionability:            15
  Completeness:             10
  User Fidelity:             5
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import anthropic

from app.activities.shopping import _extract_json

if TYPE_CHECKING:
    from app.models.contracts import DesignBrief

EVAL_MODEL = "claude-opus-4-6"
EVAL_MAX_TOKENS = 2048

_RUBRIC_CRITERIA = """\
1. **Style Coherence** (0-10): Named style with 2-3 defining \
characteristics. No contradictions. Brief reads as unified narrative. \
0 = conflicting styles or vague.

2. **Color Strategy** (0-15): Named palette with 60/30/10 proportions \
and application context. Consistent undertones throughout. \
0 = just color names with no relationship or proportions.

3. **Lighting Design** (0-15): All three layers (ambient, task, accent) \
with Kelvin temperatures and placement. \
0 = missing layers or just "warm lighting."

4. **Material & Texture Specificity** (0-15): Precise professional \
descriptors: "weathered oak," "brushed brass," "boucle upholstery." \
Min 3 distinct texture types. 0 = generic ("wood," "metal," "fabric").

5. **Design Intelligence** (0-10): Three-layer stack applied: spatial \
awareness, human-centered filter, emotional layer. Includes biophilic \
element and prospect-refuge. 0 = no design reasoning.

6. **Diagnostic Depth** (0-5): Probed beneath surface requests. Pain \
points reveal root causes. Detected/resolved contradictions. \
0 = accepted all surface answers.

7. **Actionability** (0-15): Every field translates to image gen prompt \
language. Zero guesswork needed. Textures/colors/lighting render-ready. \
0 = abstract feelings with no visual anchor.

8. **Completeness** (0-10): Covers room purpose, style, colors (with \
proportions), lighting (with layers), textures (3+ types), furniture, \
constraints, keep_items. Room-specific rules applied. 0 = 1-2 domains.

9. **User Fidelity** (0-5): Every preference traces to user statement. \
Inferred preferences clearly marked. Translations shown for correction. \
0 = hallucinated preferences."""

_RESPONSE_FORMAT = """\
You MUST respond with EXACTLY this JSON format (no markdown, just JSON):
{{
  "style_coherence": <0-10>,
  "color_strategy": <0-15>,
  "lighting_design": <0-15>,
  "material_texture": <0-15>,
  "design_intelligence": <0-10>,
  "diagnostic_depth": <0-5>,
  "actionability": <0-15>,
  "completeness": <0-10>,
  "user_fidelity": <0-5>,
  "total": <sum of above, 0-100>,
  "tag": "<PASS:EXCELLENT|PASS|FAIL:WEAK|FAIL:POOR>",
  "notes": "<1-2 sentences explaining the score>"
}}"""

RUBRIC_PROMPT = (
    "You are an expert interior design evaluator. "
    "Score this DesignBrief against the rubric below.\n\n"
    "## DesignBrief to evaluate:\n{brief_json}\n\n"
    "## Conversation transcript:\n{transcript}\n\n"
    f"## Rubric:\n{_RUBRIC_CRITERIA}\n\n"
    f"## Response format:\n{_RESPONSE_FORMAT}"
)


def format_transcript(conversation: list[dict[str, str]]) -> str:
    """Format a conversation into a readable transcript."""
    lines = []
    for msg in conversation:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def evaluate_brief(
    brief: DesignBrief,
    conversation: list[dict[str, str]],
) -> dict[str, Any]:
    """Score a DesignBrief against the quality rubric using Claude.

    Args:
        brief: The DesignBrief to evaluate.
        conversation: List of {"role": ..., "content": ...} dicts.

    Returns:
        Dict with per-criterion scores, total, tag, and notes.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    brief_json = brief.model_dump_json(indent=2)
    transcript = format_transcript(conversation)

    prompt = RUBRIC_PROMPT.format(
        brief_json=brief_json,
        transcript=transcript,
    )

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=EVAL_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    result = _extract_json(text)
    if not result:
        raise ValueError(f"Could not extract JSON from eval response: {text[:200]}")
    return result


_CONVERSATION_CRITERIA = """\
1. **Probing Quality** (0-5): Did the agent probe beneath surface answers? \
Did follow-ups diagnose root causes rather than accepting vague responses? \
0 = accepted all surface answers. 5 = every vague answer got a probing follow-up.

2. **Acknowledgment** (0-5): Did the agent reference specific things the user said? \
Did it show it was listening, not running a script? \
0 = generic responses ignoring user input. 5 = every response referenced user's words.

3. **Adaptation** (0-5): Did the agent skip domains already covered? \
Did it adjust question order based on what was learned? \
0 = rigid scripted order. 5 = fully adaptive to user's answers.

4. **Translation Visibility** (0-5): Did the agent show design translations \
so the user could correct them? E.g., "I'm interpreting 'cozy' as warm 2700K \
lighting with layered textiles — does that match?" \
0 = no translations shown. 5 = translations shown and confirmed.

5. **Conversational Flow** (0-5): Did the conversation feel natural? \
Was pacing appropriate? Did transitions between topics feel smooth? \
0 = robotic or jarring. 5 = natural and engaging."""

_CONVERSATION_RESPONSE_FORMAT = """\
You MUST respond with EXACTLY this JSON format (no markdown, just JSON):
{{
  "probing_quality": <0-5>,
  "acknowledgment": <0-5>,
  "adaptation": <0-5>,
  "translation_visibility": <0-5>,
  "conversational_flow": <0-5>,
  "total": <sum of above, 0-25>,
  "notes": "<1-2 sentences explaining the score>"
}}"""

_CONVERSATION_PROMPT = (
    "You are an expert evaluator of design consultation conversations. "
    "Score this conversation's quality against the rubric below.\n\n"
    "## Conversation transcript:\n{transcript}\n\n"
    f"## Rubric:\n{_CONVERSATION_CRITERIA}\n\n"
    f"## Response format:\n{_CONVERSATION_RESPONSE_FORMAT}"
)


def evaluate_conversation_quality(
    conversation: list[dict[str, str]],
) -> dict[str, Any]:
    """Score conversation quality (supplementary signal, not pass/fail gate).

    5 criteria x 5 pts = 25 total:
    - Probing Quality, Acknowledgment, Adaptation,
      Translation Visibility, Conversational Flow

    Returns:
        Dict with per-criterion scores, total, and notes.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    transcript = format_transcript(conversation)
    prompt = _CONVERSATION_PROMPT.format(transcript=transcript)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=EVAL_MODEL,
        max_tokens=EVAL_MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    result = _extract_json(text)
    if not result:
        raise ValueError(f"Could not extract JSON from conversation eval: {text[:200]}")
    return result


def score_tag(total: int) -> str:
    """Return the quality tag for a given total score."""
    if total >= 85:
        return "PASS:EXCELLENT"
    if total >= 70:
        return "PASS"
    if total >= 50:
        return "FAIL:WEAK"
    return "FAIL:POOR"
