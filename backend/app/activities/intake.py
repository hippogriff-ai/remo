"""Intake chat activity — runs a design-consultant conversation with Claude.

Stateless: receives full conversation history + new user message,
returns next response with brief update. All intelligence lives
in the system prompt (backend/prompts/intake_system.txt).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import anthropic
import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.activities import skill_loader
from app.models.contracts import (
    ChatMessage,
    DesignBrief,
    InspirationNote,
    IntakeChatInput,
    IntakeChatOutput,
    QuickReplyOption,
    StyleProfile,
)

log = structlog.get_logger("intake")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

MODEL = "claude-opus-4-6"
MAX_TOKENS = 4096

# Shared brief property schemas (used by both skill tools)
_BRIEF_PROPERTIES: dict[str, Any] = {
    "room_type": {
        "type": "string",
        "description": "Room type (living room, bedroom, kitchen, etc.)",
    },
    "occupants": {
        "type": "string",
        "description": "Who uses the room (e.g., 'couple with toddler and cat')",
    },
    "pain_points": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Functional failures identified (root causes, not symptoms)",
    },
    "keep_items": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Items the user wants to keep (must integrate into design)",
    },
    "style_profile": {
        "type": "object",
        "additionalProperties": True,
        "properties": {
            "lighting": {
                "type": "string",
                "description": (
                    "All three layers (ambient, task, accent) with Kelvin temps "
                    "and placement. E.g., 'Ambient: warm 2700K recessed, "
                    "Task: 3500K under-cabinet, Accent: picture lights on art'"
                ),
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Colors with 60/30/10 proportions and application. "
                    "E.g., ['warm ivory walls (60%)', "
                    "'walnut wood tones (30%)', 'terracotta accents (10%)']"
                ),
            },
            "textures": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Professional material descriptors (min 3). "
                    "E.g., 'weathered oak', 'brushed brass', "
                    "'boucle upholstery' — never generic 'wood' or 'fabric'"
                ),
            },
            "clutter_level": {
                "type": "string",
                "description": (
                    "One of: minimal / curated / layered. Include storage strategy context"
                ),
            },
            "mood": {
                "type": "string",
                "description": (
                    "Spatial and sensory terms. E.g., "
                    "'Intimate refuge — deep-seated furniture, "
                    "layered warm textiles, soft pools of light'"
                ),
            },
        },
    },
    "lifestyle": {
        "type": "string",
        "description": (
            "How the user uses the space: activities, frequency, daily routines. "
            "E.g., 'Morning yoga, evening reading, weekend hosting for 6-8 guests'"
        ),
    },
    "constraints": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Budget, pets, kids, mobility, rental, timeline, etc.",
    },
    "inspiration_notes": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "photo_index": {"type": "integer"},
                "note": {"type": "string"},
                "agent_clarification": {"type": "string"},
            },
        },
        "description": "Agent's interpretation of inspiration photo elements",
    },
    "domains_covered": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Design domains covered so far from the 11-domain notepad: "
            "room_purpose, pain_points, style, color, lighting, "
            "texture, furniture, clutter, mood, lifestyle, constraints"
        ),
    },
    "emotional_drivers": {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Why this project now — emotional motivations. "
            "E.g., 'started WFH, room feels oppressive'"
        ),
    },
    "usage_patterns": {
        "type": "string",
        "description": (
            "Detailed who/when/what usage patterns. "
            "E.g., 'couple WFH Mon-Fri, host dinners monthly'"
        ),
    },
    "renovation_willingness": {
        "type": "string",
        "description": (
            "Scope signals for what the user will change. "
            "E.g., 'repaint yes, fixtures maybe, tile no'"
        ),
    },
    "room_analysis_hypothesis": {
        "type": "string",
        "description": (
            "Preserved or updated hypothesis from photo analysis. "
            "Track how your understanding evolved during the conversation."
        ),
    },
}

_OPTIONS_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "number": {"type": "integer"},
            "label": {
                "type": "string",
                "description": "Short label (2-5 words)",
            },
            "value": {
                "type": "string",
                "description": "Design-relevant detail beyond the label",
            },
        },
        "required": ["number", "label", "value"],
    },
    "description": (
        "Quick-reply options (2-4). Use when the answer is "
        "classifiable. Each must be specific and distinct. "
        "For summary: 'That captures it perfectly' / "
        "'A few adjustments' / 'Start fresh'"
    ),
}

# Skill tool definitions — the agent picks EXACTLY ONE per turn
SKILL_NAMES = frozenset({"interview_client", "draft_design_brief"})

INTAKE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "interview_client",
        "description": (
            "Ask the user a design-informed question to uncover uncovered domains, "
            "probe vague answers, or resolve contradictions. Use the diagnostic question "
            "bank and translation engine. Optionally include a partial_brief_update to "
            "track what you've learned so far."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Your response to the user. Reference specific things "
                        "you observed. Show design translations so users can "
                        "correct them."
                    ),
                },
                "options": _OPTIONS_SCHEMA,
                "is_open_ended": {
                    "type": "boolean",
                    "description": (
                        "True when probing pain points, lifestyle, or emotions. "
                        "False when offering classifiable options."
                    ),
                },
                "partial_brief_update": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Optional incremental brief update — track what you've "
                        "learned so far in elevated design language. Include all "
                        "previously gathered information plus new information."
                    ),
                    "properties": _BRIEF_PROPERTIES,
                },
                "domains_covered": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Design domains covered so far from the 11-domain notepad: "
                        "room_purpose, pain_points, style, color, lighting, "
                        "texture, furniture, clutter, mood, lifestyle, constraints"
                    ),
                },
                "requested_skills": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Style skill IDs to load for the NEXT turn. Request when you "
                        "detect the user's style direction. Max 2. Check the skill "
                        "summary table for valid IDs."
                    ),
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "draft_design_brief",
        "description": (
            "Produce the final elevated design brief. Use when you have sufficient "
            "information across key domains (6+ of 11 with depth). Apply the 20-rule "
            "validation checklist and elevation rules. The design_brief field is REQUIRED."
        ),
        "input_schema": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "message": {
                    "type": "string",
                    "description": (
                        "Summary text showing translated design parameters. "
                        "Frame as 'Here's how I'd bring that to life...' "
                        "Highlight inferred preferences clearly."
                    ),
                },
                "options": _OPTIONS_SCHEMA,
                "design_brief": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "Complete, elevated DesignBrief with all gathered information. "
                        "Every field should use professional design language."
                    ),
                    "properties": _BRIEF_PROPERTIES,
                    "required": ["room_type"],
                },
            },
            "required": ["message", "design_brief"],
        },
    },
]

# Mode-specific behavioral instructions injected into the system prompt
MODE_INSTRUCTIONS = {
    "quick": (
        "### Quick Mode (~3 turns)\n"
        "You have a notepad of 10 design domains. Select the 3 most impactful for "
        "this room type (consult the room-specific guidance above). Pre-plan 3 questions "
        "using diagnostic alternatives from the question bank. Adapt — if the user's "
        "answer covers multiple domains, skip ahead. Apply the translation engine to "
        "every response. Target ~3 turns, then synthesize an elevated brief.\n\n"
        "Skill selection: use `interview_client` for ~2-3 turns, then `draft_design_brief`. "
        "If the user's first answer covers 5+ domains, you may draft after 1-2 turns.\n\n"
        "Turn budget: {remaining_turns} turns remaining (including this one). "
        "When 1 turn remains, this IS your last chance — you MUST use `draft_design_brief` "
        "to produce the final elevated brief. Do NOT use `interview_client` on your last turn. "
        "Even with incomplete information, draft the best brief you can from what you have."
    ),
    "full": (
        "### Full Mode (~10 turns)\n"
        "You have a notepad of 10 design domains. Pre-plan 10 questions in priority "
        "order using the diagnostic question bank. After each response, run the DIAGNOSE "
        "pipeline: detect contradictions, interpret vague terms via translation engine, "
        "analyze root causes with the three-why technique. Reorder remaining questions "
        "based on what you've learned. Skip domains already covered. The notepad keeps "
        "you on track; your design intelligence picks the best next question and probes "
        "deeper when answers are surface-level.\n\n"
        "Skill selection: use `interview_client` until 6+ domains are covered with depth, "
        "then `draft_design_brief`. Expect ~7-9 interview turns.\n\n"
        "Turn budget: {remaining_turns} turns remaining (including this one). "
        "When 1 turn remains, this IS your last chance — you MUST use `draft_design_brief` "
        "to produce the final elevated brief. Do NOT use `interview_client` on your last turn. "
        "Even with incomplete information, draft the best brief you can from what you have."
    ),
    "open": (
        "### Open Conversation Mode (~15 turns)\n"
        "Begin with an open prompt: 'Tell us about this room — what's on your mind, "
        "what you love, what you'd change, anything.' Follow the user's lead. Apply "
        "the DIAGNOSE pipeline continuously. Track domains on your notepad internally.\n\n"
        "**Anchor Points**: In your first response, include a brief list of the design "
        "domains you plan to explore (room_purpose, pain_points, style, color, lighting, "
        "texture, furniture, clutter, mood, lifestyle, constraints). Frame it as: "
        "'As we chat, I'll make sure we cover areas like [top 5 uncovered domains]. "
        "But let's start with whatever's on your mind.' This anchors the conversation "
        "and lets the user see the roadmap without forcing structure.\n\n"
        "When conversation energy slows, reference the anchor list and probe uncovered "
        "domains using diagnostic questions.\n\n"
        "Skill selection: use `interview_client` until you have rich coverage across most "
        "domains, then `draft_design_brief`. Expect ~10-14 interview turns.\n\n"
        "Turn budget: {remaining_turns} turns remaining (including this one). "
        "When 1 turn remains, this IS your last chance — you MUST use `draft_design_brief` "
        "to produce the final elevated brief. Do NOT use `interview_client` on your last turn. "
        "Even with incomplete information, draft the best brief you can from what you have."
    ),
}

MAX_TURNS = {"quick": 4, "full": 11, "open": 16}


_system_prompt_cache: str | None = None


def load_system_prompt(
    mode: str,
    turn_number: int,
    previous_brief: dict[str, Any] | None = None,
    room_analysis: dict[str, Any] | None = None,
    loaded_skill_ids: list[str] | None = None,
) -> str:
    """Load and customize the system prompt for the given mode and turn.

    If previous_brief is provided (from an earlier turn), it's injected as
    structured context so the model never loses gathered information.
    If room_analysis is provided (from eager photo analysis), it's injected
    as pre-observation context so the agent starts with a hypothesis.
    If loaded_skill_ids is provided, the corresponding style guide content
    is injected into the prompt for deep style knowledge.
    """
    global _system_prompt_cache  # noqa: PLW0603
    if _system_prompt_cache is None:
        _system_prompt_cache = (PROMPTS_DIR / "intake_system.txt").read_text()
    template = _system_prompt_cache

    max_turns = MAX_TURNS.get(mode, 4)
    remaining = max(0, max_turns - turn_number)

    mode_text = MODE_INSTRUCTIONS.get(mode, MODE_INSTRUCTIONS["quick"])
    mode_text = mode_text.format(remaining_turns=remaining)

    prompt = template.replace("{mode_instructions}", mode_text)

    # Inject room analysis context if available
    analysis_section = _format_room_analysis_section(room_analysis)
    prompt = prompt.replace("{room_analysis_section}", analysis_section)

    # Inject skill system: compact summary table + loaded skill content
    manifest = skill_loader.load_manifest()
    prompt = prompt.replace(
        "{skill_summary_table}",
        skill_loader.build_skill_summary_block(manifest),
    )
    prompt = prompt.replace(
        "{loaded_skills_section}",
        skill_loader.build_loaded_skills_block(loaded_skill_ids or []),
    )

    # Inject previous brief context so the model builds on prior turns
    if previous_brief and turn_number > 1:
        brief_context = _format_brief_context(previous_brief)
        prompt += (
            "\n\n---\n\n## GATHERED SO FAR (from previous turns)\n\n"
            f"{brief_context}\n\n"
            "Build on this information. Do NOT drop any fields — only add or refine."
        )

    return prompt


def _format_brief_context(brief: dict[str, Any]) -> str:
    """Format a previous brief dict into readable context for the system prompt."""
    lines = []
    if brief.get("room_type"):
        lines.append(f"- Room type: {brief['room_type']}")
    if brief.get("occupants"):
        lines.append(f"- Occupants: {brief['occupants']}")
    if brief.get("pain_points"):
        lines.append(f"- Pain points: {', '.join(brief['pain_points'])}")
    if brief.get("keep_items"):
        lines.append(f"- Keep items: {', '.join(brief['keep_items'])}")
    if brief.get("lifestyle"):
        lines.append(f"- Lifestyle: {brief['lifestyle']}")
    if brief.get("constraints"):
        lines.append(f"- Constraints: {', '.join(brief['constraints'])}")

    sp = brief.get("style_profile")
    if isinstance(sp, dict):
        if sp.get("mood"):
            lines.append(f"- Mood: {sp['mood']}")
        if sp.get("lighting"):
            lines.append(f"- Lighting: {sp['lighting']}")
        if sp.get("colors"):
            lines.append(f"- Colors: {', '.join(sp['colors'])}")
        if sp.get("textures"):
            lines.append(f"- Textures: {', '.join(sp['textures'])}")
        if sp.get("clutter_level"):
            lines.append(f"- Clutter level: {sp['clutter_level']}")

    if brief.get("domains_covered"):
        lines.append(f"- Domains covered: {', '.join(brief['domains_covered'])}")

    return "\n".join(lines) if lines else "No information gathered yet."


def _format_room_analysis_section(analysis: dict[str, Any] | None) -> str:
    """Format room analysis into a prompt section for the intake agent.

    When analysis is available, injects the hypothesis, observations, and
    uncertain aspects so the agent starts with pre-formed understanding.
    When absent, provides a minimal fallback instruction.
    """
    if not analysis:
        return (
            "No pre-analysis available for this room. Start from your "
            "own observations of any room photos provided."
        )

    lines = [
        "Your design team has already analyzed the room photos before "
        "this conversation. Here is what was observed:\n"
    ]

    if analysis.get("room_type"):
        conf = analysis.get("room_type_confidence", 0.5)
        lines.append(f"**Room type**: {analysis['room_type']} (confidence: {conf:.0%})")

    if analysis.get("hypothesis"):
        lines.append(f"\n**Hypothesis**: {analysis['hypothesis']}")

    if analysis.get("estimated_dimensions"):
        lines.append(f"**Dimensions**: {analysis['estimated_dimensions']}")

    if analysis.get("layout_pattern"):
        lines.append(f"**Layout**: {analysis['layout_pattern']}")

    lighting = analysis.get("lighting")
    if isinstance(lighting, dict):
        parts = []
        if lighting.get("natural_light_intensity"):
            parts.append(lighting["natural_light_intensity"])
        if lighting.get("natural_light_direction"):
            parts.append(lighting["natural_light_direction"])
        if lighting.get("existing_artificial"):
            parts.append(f"artificial: {lighting['existing_artificial']}")
        if parts:
            lines.append(f"**Lighting**: {', '.join(parts)}")
        gaps = lighting.get("lighting_gaps", [])
        if gaps:
            lines.append(f"**Lighting gaps**: {', '.join(gaps)}")

    furniture = analysis.get("furniture", [])
    if furniture:
        items = []
        for f in furniture:
            if isinstance(f, dict) and f.get("item"):
                desc = f["item"]
                if f.get("condition"):
                    desc += f" ({f['condition']})"
                if f.get("keep_candidate"):
                    desc += " [keep]"
                items.append(desc)
        if items:
            lines.append(f"**Furniture**: {'; '.join(items)}")

    if analysis.get("architectural_features"):
        lines.append("**Architecture**: " + ", ".join(analysis["architectural_features"]))

    if analysis.get("style_signals"):
        lines.append("**Style signals**: " + ", ".join(analysis["style_signals"]))

    behavioral = analysis.get("behavioral_signals", [])
    if behavioral:
        for sig in behavioral:
            if isinstance(sig, dict):
                obs = sig.get("observation", "")
                inf = sig.get("inference", "")
                lines.append(f"**Behavioral signal**: {obs} → {inf}")

    if analysis.get("tensions"):
        lines.append("**Tensions**: " + "; ".join(analysis["tensions"]))

    if analysis.get("strengths"):
        lines.append("**Strengths**: " + ", ".join(analysis["strengths"]))

    if analysis.get("opportunities"):
        lines.append("**Opportunities**: " + ", ".join(analysis["opportunities"]))

    uncertain = analysis.get("uncertain_aspects", [])
    if uncertain:
        lines.append(f"\n**Uncertain (probe these)**: {', '.join(uncertain)}")

    lines.append(
        "\n### Using This Analysis\n"
        "- You already know the room type and basic layout. "
        "Do NOT re-ask these.\n"
        "- Start by confirming your understanding and probing "
        "the highest-uncertainty aspects.\n"
        "- Pre-populate room_type, lighting, and furniture domains "
        "from the analysis.\n"
        "- Reference specific observations: 'I noticed the sofa "
        "faces away from the window — is that intentional?'\n"
        "- The hypothesis is your starting point, not gospel. "
        "Update it as the user provides corrections."
    )

    lines.append(
        "\n### HYPOTHESIS CORRECTIONS\n"
        "When the user contradicts your room analysis:\n"
        "- Acknowledge warmly: 'Good to know — photos can be "
        "misleading about [aspect]'\n"
        "- Update your mental model immediately — do not carry "
        "forward invalidated assumptions\n"
        "- Use the correction as a learning signal: if they "
        "corrected lighting, they care deeply about it — "
        "probe deeper\n"
        "- NEVER say 'but the photos show...' or imply the user "
        "is wrong about their own space"
    )

    return "\n".join(lines)


def build_messages(
    conversation_history: list[ChatMessage],
    user_message: str,
    room_photo_urls: list[str] | None = None,
    inspiration_photo_urls: list[str] | None = None,
    inspiration_notes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert contract ChatMessages into Anthropic API message format.

    On the first turn (empty history), room and inspiration photos are injected
    as image content blocks alongside the user's text so Claude can see them.
    Photos are only sent on turn 1 — subsequent turns already have them in history.
    """
    messages: list[dict[str, Any]] = []
    for msg in conversation_history:
        messages.append({"role": msg.role, "content": msg.content})

    # First turn with photos: build multimodal content
    is_first_turn = len(conversation_history) == 0
    has_photos = bool(room_photo_urls) or bool(inspiration_photo_urls)

    if is_first_turn and has_photos:
        content: list[dict[str, Any]] = []

        # Room photos first
        for url in room_photo_urls or []:
            content.append({"type": "image", "source": {"type": "url", "url": url}})

        # Inspiration photos with user notes as context
        for i, url in enumerate(inspiration_photo_urls or []):
            content.append({"type": "image", "source": {"type": "url", "url": url}})
            # Attach user note if available
            note = _get_inspiration_note(i, inspiration_notes)
            if note:
                content.append(
                    {
                        "type": "text",
                        "text": f"[Inspiration photo {i + 1} note: {note}]",
                    }
                )

        content.append({"type": "text", "text": user_message})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_message})

    return messages


def _get_inspiration_note(
    photo_index: int,
    notes: list[dict[str, Any]] | None,
) -> str | None:
    """Get the user's note for an inspiration photo by index."""
    if not notes:
        return None
    for note in notes:
        if note.get("photo_index") == photo_index and note.get("note"):
            return str(note["note"])
    return None


def extract_skill_call(
    response: anthropic.types.Message,
) -> tuple[str | None, dict[str, Any]]:
    """Extract the chosen skill tool call from response.

    Returns (skill_name, skill_data). The agent should call exactly one
    skill per turn — we take the first matching skill tool.
    """
    for block in response.content:
        if block.type == "tool_use" and block.name in SKILL_NAMES:
            return block.name, block.input  # type: ignore[return-value]
    return None, {}


def build_brief(brief_data: dict[str, Any]) -> DesignBrief:
    """Construct a DesignBrief from tool call data."""
    style_raw = brief_data.get("style_profile")
    style_profile = None
    if isinstance(style_raw, dict):
        style_profile = StyleProfile(
            lighting=style_raw.get("lighting"),
            colors=style_raw.get("colors", []),
            textures=style_raw.get("textures", []),
            clutter_level=style_raw.get("clutter_level"),
            mood=style_raw.get("mood"),
        )

    inspiration_raw = brief_data.get("inspiration_notes", [])
    inspiration_notes = []
    for note in inspiration_raw:
        if isinstance(note, dict):
            inspiration_notes.append(
                InspirationNote(
                    photo_index=note.get("photo_index", 0),
                    note=note.get("note", ""),
                    agent_clarification=note.get("agent_clarification"),
                )
            )

    # Merge lifestyle into occupants for downstream compat: iOS Models.swift
    # doesn't have the lifestyle field yet, so it drops it on round-trip.
    # Keep both the merged occupants and the separate lifestyle field.
    occupants = brief_data.get("occupants")
    lifestyle = brief_data.get("lifestyle")
    if lifestyle and occupants:
        occupants = f"{occupants} — {lifestyle}"
    elif lifestyle:
        occupants = lifestyle

    return DesignBrief(
        room_type=brief_data.get("room_type", ""),
        occupants=occupants,
        lifestyle=lifestyle,
        pain_points=brief_data.get("pain_points", []),
        keep_items=brief_data.get("keep_items", []),
        style_profile=style_profile,
        constraints=brief_data.get("constraints", []),
        inspiration_notes=inspiration_notes,
        emotional_drivers=brief_data.get("emotional_drivers", []),
        usage_patterns=brief_data.get("usage_patterns"),
        renovation_willingness=brief_data.get("renovation_willingness"),
        room_analysis_hypothesis=brief_data.get("room_analysis_hypothesis"),
    )


def build_options(options_raw: list[dict[str, Any]] | None) -> list[QuickReplyOption] | None:
    """Convert raw option dicts to QuickReplyOption models."""
    if not options_raw:
        return None
    return [
        QuickReplyOption(
            number=opt.get("number", i + 1),
            label=opt.get("label", ""),
            value=opt.get("value", ""),
        )
        for i, opt in enumerate(options_raw)
        if isinstance(opt, dict)
    ]


async def _run_intake_core(input: IntakeChatInput) -> IntakeChatOutput:
    """Core intake logic — callable from both the Temporal activity and tests.

    Server-side turn counter prevents the model from miscounting.
    The model calls exactly one skill tool per turn: interview_client or draft_design_brief.
    """
    if not input.user_message.strip():
        raise ApplicationError("User message cannot be empty", non_retryable=True)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ApplicationError("ANTHROPIC_API_KEY not set", non_retryable=True)

    # Server-side turn counter (count user messages in history + this one)
    turn_number = len([m for m in input.conversation_history if m.role == "user"]) + 1

    # Extract previous brief and room analysis from project context
    previous_brief: dict[str, Any] | None = input.project_context.get("previous_brief")
    room_analysis: dict[str, Any] | None = input.project_context.get("room_analysis")
    loaded_skill_ids: list[str] = input.project_context.get("loaded_skill_ids", [])
    system_prompt = load_system_prompt(
        input.mode, turn_number, previous_brief, room_analysis, loaded_skill_ids
    )

    # Skip room photos when room analysis exists: the analyze_room activity (same
    # model) already extracted structured observations, so re-sending raw images
    # would duplicate work and add ~3-6k tokens/turn of cognitive load. Fallback
    # to raw photos if analysis is absent. May revisit if intake needs visual detail
    # beyond what the analysis schema captures.
    room_photo_urls: list[str] = (
        input.project_context.get("room_photos", []) if not room_analysis else []
    )
    inspo_photo_urls: list[str] = input.project_context.get("inspiration_photos", [])
    inspo_notes: list[dict[str, Any]] | None = input.project_context.get("inspiration_notes")
    messages = build_messages(
        input.conversation_history,
        input.user_message,
        room_photo_urls,
        inspo_photo_urls,
        inspo_notes,
    )

    log.info(
        "intake_turn_start",
        mode=input.mode,
        turn=turn_number,
        has_room_photos=len(room_photo_urls) > 0,
        has_inspiration_photos=len(inspo_photo_urls) > 0,
        has_previous_brief=previous_brief is not None,
        loaded_skill_ids=loaded_skill_ids,
        history_len=len(input.conversation_history),
    )

    from app.utils.tracing import wrap_anthropic

    client = wrap_anthropic(anthropic.AsyncAnthropic(api_key=api_key))

    try:
        response = await client.messages.create(  # type: ignore[call-overload]
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=INTAKE_TOOLS,
            tool_choice={"type": "any"},
            messages=messages,
        )
    except anthropic.RateLimitError as e:
        log.warning("intake_rate_limited", turn=turn_number, mode=input.mode)
        raise ApplicationError(f"Claude rate limited: {e}", non_retryable=False) from e
    except anthropic.APIStatusError as e:
        if e.status_code == 400 and "content policy" in str(e).lower():
            log.error("intake_content_policy", turn=turn_number)
            raise ApplicationError(f"Content policy violation: {e}", non_retryable=True) from e
        log.error("intake_api_error", status=e.status_code, turn=turn_number)
        non_retryable = 400 <= e.status_code < 500
        raise ApplicationError(
            f"Claude API error ({e.status_code}): {e}", non_retryable=non_retryable
        ) from e

    skill_name, skill_data = extract_skill_call(response)

    # Fallback: if the model didn't call a skill tool, extract text from content blocks
    if skill_name is None:
        log.warning("intake_no_skill_called", turn=turn_number, mode=input.mode)
        text_parts = [b.text for b in response.content if hasattr(b, "text")]
        fallback = "I'm here to help with your room design. Tell me about the room?"
        agent_message = " ".join(text_parts) if text_parts else fallback
        skill_data = {"message": agent_message}

    # Extract requested_skills from interview_client calls
    requested_skills: list[str] = []
    if skill_name == "interview_client":
        raw_skills = skill_data.get("requested_skills", [])
        if not isinstance(raw_skills, list):
            log.warning("intake_requested_skills_bad_type", got=type(raw_skills).__name__)
            raw_skills = []
        manifest = skill_loader.load_manifest()
        valid_ids = {s.skill_id for s in manifest.skills}
        validated = [s for s in raw_skills if isinstance(s, str) and s in valid_ids]
        invalid = [s for s in raw_skills if not isinstance(s, str) or s not in valid_ids]
        if invalid:
            log.warning("intake_requested_skills_invalid", invalid=invalid, valid=validated)
        requested_skills = skill_loader.cap_skills(validated)

    # Build output based on which skill was chosen
    max_turns = MAX_TURNS.get(input.mode, 4)
    total_domains = 11

    if skill_name == "draft_design_brief":
        # Draft skill: complete brief required, this is the summary turn
        is_summary = True
        brief_data = skill_data.get("design_brief")
        domains_covered = brief_data.get("domains_covered", []) if brief_data else []
    elif skill_name == "interview_client":
        # Interview skill: optional partial brief, not a summary
        is_summary = False
        brief_data = skill_data.get("partial_brief_update")
        domains_covered = skill_data.get("domains_covered", [])
    else:
        # No skill called (fallback path)
        is_summary = False
        brief_data = None
        domains_covered = []

    # Server-side enforcement: force summary on final turn even if model chose interview
    if turn_number >= max_turns and not is_summary:
        log.warning("intake_forced_summary", turn=turn_number, max_turns=max_turns)
        is_summary = True

    # Safety net: if forced summary but no brief data, use accumulated previous_brief
    if is_summary and brief_data is None and previous_brief is not None:
        log.info("intake_using_previous_brief", turn=turn_number)
        brief_data = previous_brief

    log.info(
        "intake_turn_complete",
        mode=input.mode,
        turn=turn_number,
        skill=skill_name,
        domains_covered=len(domains_covered),
        has_brief=brief_data is not None,
        is_summary=is_summary,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    # Build partial/complete brief and stamp loaded skills
    partial_brief = build_brief(brief_data) if brief_data else None
    if partial_brief:
        if loaded_skill_ids:
            partial_brief.style_skills_used = loaded_skill_ids
        elif brief_data and isinstance(brief_data.get("style_skills_used"), list):
            partial_brief.style_skills_used = brief_data["style_skills_used"]

    return IntakeChatOutput(
        agent_message=skill_data.get("message", ""),
        options=build_options(skill_data.get("options")),
        is_open_ended=skill_data.get("is_open_ended", False),
        progress=(
            f"Turn {turn_number} of ~{max_turns}"
            f" — {len(domains_covered)}/{total_domains} domains covered"
        ),
        is_summary=is_summary,
        partial_brief=partial_brief,
        requested_skills=requested_skills,
    )


@activity.defn
async def run_intake_chat(input: IntakeChatInput) -> IntakeChatOutput:
    """Temporal activity wrapper — delegates to _run_intake_core."""
    return await _run_intake_core(input)
