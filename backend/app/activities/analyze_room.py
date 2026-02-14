"""Room photo analysis activity — the designer's "read the room" skill.

Sends room photos to Claude Opus 4.6 for structured analysis before
the intake conversation begins. The analysis provides a hypothesis about
the room that enhances (not replaces) the intake agent's reasoning.

Stateless: receives photo URLs, returns RoomAnalysis. All intelligence
lives in the system prompt (backend/prompts/read_the_room.txt).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import anthropic
import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.contracts import (
    AnalyzeRoomPhotosInput,
    AnalyzeRoomPhotosOutput,
    BehavioralSignal,
    FurnitureObservation,
    LightingAssessment,
    RoomAnalysis,
)

log = structlog.get_logger("analyze_room")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

MODEL = "claude-opus-4-6"
MAX_TOKENS = 4096

# Tool schema for structured output — mirrors RoomAnalysis contract
ANALYZE_ROOM_TOOL: dict[str, Any] = {
    "name": "analyze_room",
    "description": (
        "Record your structured analysis of the room photos. "
        "Fill every field you can determine; leave others as null/empty."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "room_type": {
                "type": "string",
                "description": "Room type (living room, bedroom, kitchen, etc.)",
            },
            "room_type_confidence": {
                "type": "number",
                "description": "Confidence in room type identification (0.0-1.0)",
            },
            "estimated_dimensions": {
                "type": "string",
                "description": (
                    "Visual estimate of room dimensions (e.g., 'approximately 12x15 feet')"
                ),
            },
            "layout_pattern": {
                "type": "string",
                "description": "Spatial layout pattern (open plan, L-shaped, galley, etc.)",
            },
            "lighting": {
                "type": "object",
                "properties": {
                    "natural_light_direction": {
                        "type": "string",
                        "description": (
                            "Direction of natural light entry (e.g., 'south-facing windows')"
                        ),
                    },
                    "natural_light_intensity": {
                        "type": "string",
                        "description": "Light level: abundant, moderate, or limited",
                    },
                    "window_coverage": {
                        "type": "string",
                        "description": "Window coverage (full wall, single window, etc.)",
                    },
                    "existing_artificial": {
                        "type": "string",
                        "description": (
                            "Artificial lighting assessment (layered, single overhead, etc.)"
                        ),
                    },
                    "lighting_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Areas lacking adequate lighting",
                    },
                },
            },
            "furniture": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item": {"type": "string", "description": "Furniture piece description"},
                        "condition": {"type": "string", "description": "Condition assessment"},
                        "placement_note": {
                            "type": "string",
                            "description": "Note about placement or arrangement",
                        },
                        "keep_candidate": {
                            "type": "boolean",
                            "description": "Whether this piece is worth keeping",
                        },
                    },
                    "required": ["item"],
                },
            },
            "architectural_features": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Notable architectural features (crown molding, bay window, etc.)",
            },
            "flooring": {
                "type": "string",
                "description": "Flooring type and condition",
            },
            "existing_palette": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Current color palette observations",
            },
            "overall_warmth": {
                "type": "string",
                "description": "Overall color temperature: cool, warm, neutral, or mixed",
            },
            "circulation_issues": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Movement/flow problems in the space",
            },
            "style_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Style indicators visible in the room",
            },
            "behavioral_signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "observation": {"type": "string"},
                        "inference": {"type": "string"},
                        "design_implication": {"type": "string"},
                    },
                    "required": ["observation", "inference"],
                },
            },
            "tensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Conflicts or contradictions in the space",
            },
            "hypothesis": {
                "type": "string",
                "description": (
                    "2-3 sentence synthesis: what this room is, could be, biggest opportunity"
                ),
            },
            "strengths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "What's working well in the space",
            },
            "opportunities": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Design opportunities identified",
            },
            "uncertain_aspects": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Things that can't be determined from photos alone",
            },
        },
    },
}

_system_prompt_cache: str | None = None


def load_prompt() -> str:
    """Load the read_the_room system prompt."""
    global _system_prompt_cache  # noqa: PLW0603
    if _system_prompt_cache is None:
        _system_prompt_cache = (PROMPTS_DIR / "read_the_room.txt").read_text()
    return _system_prompt_cache


def build_messages(input: AnalyzeRoomPhotosInput) -> list[dict[str, Any]]:
    """Build Anthropic API message with room and inspiration photos."""
    content: list[dict[str, Any]] = []

    for url in input.room_photo_urls:
        content.append({"type": "image", "source": {"type": "url", "url": url}})

    if input.inspiration_photo_urls:
        content.append(
            {
                "type": "text",
                "text": "[The following are inspiration photos the client shared:]",
            }
        )
        for i, url in enumerate(input.inspiration_photo_urls):
            content.append({"type": "image", "source": {"type": "url", "url": url}})
            # Attach note if available
            note = next(
                (n.note for n in input.inspiration_notes if n.photo_index == i and n.note),
                None,
            )
            if note:
                content.append({"type": "text", "text": f"[Note: {note}]"})

    content.append(
        {
            "type": "text",
            "text": "Analyze these room photos following your observational protocol.",
        }
    )

    return [{"role": "user", "content": content}]


def extract_analysis(response: anthropic.types.Message) -> dict[str, Any]:
    """Extract the analyze_room tool call from the response."""
    for block in response.content:
        if block.type == "tool_use" and block.name == "analyze_room":
            return block.input  # type: ignore[return-value]
    return {}


def build_room_analysis(data: dict[str, Any], photo_count: int) -> RoomAnalysis:
    """Convert raw tool call data into a RoomAnalysis model."""
    lighting_data = data.get("lighting")
    lighting = None
    if isinstance(lighting_data, dict):
        lighting = LightingAssessment(
            natural_light_direction=lighting_data.get("natural_light_direction"),
            natural_light_intensity=lighting_data.get("natural_light_intensity"),
            window_coverage=lighting_data.get("window_coverage"),
            existing_artificial=lighting_data.get("existing_artificial"),
            lighting_gaps=lighting_data.get("lighting_gaps") or [],
        )

    furniture = []
    for item_data in data.get("furniture") or []:
        if isinstance(item_data, dict) and "item" in item_data:
            furniture.append(
                FurnitureObservation(
                    item=item_data["item"],
                    condition=item_data.get("condition"),
                    placement_note=item_data.get("placement_note"),
                    keep_candidate=item_data.get("keep_candidate", False),
                )
            )
        else:
            log.warning("skipped_malformed_furniture", data=repr(item_data)[:200])

    behavioral_signals = []
    for sig_data in data.get("behavioral_signals") or []:
        if isinstance(sig_data, dict) and "observation" in sig_data and "inference" in sig_data:
            behavioral_signals.append(
                BehavioralSignal(
                    observation=sig_data["observation"],
                    inference=sig_data["inference"],
                    design_implication=sig_data.get("design_implication"),
                )
            )
        else:
            log.warning("skipped_malformed_behavioral_signal", data=repr(sig_data)[:200])

    return RoomAnalysis(
        room_type=data.get("room_type"),
        room_type_confidence=data.get("room_type_confidence", 0.5),
        estimated_dimensions=data.get("estimated_dimensions"),
        layout_pattern=data.get("layout_pattern"),
        lighting=lighting,
        furniture=furniture,
        architectural_features=data.get("architectural_features", []),
        flooring=data.get("flooring"),
        existing_palette=data.get("existing_palette", []),
        overall_warmth=data.get("overall_warmth"),
        circulation_issues=data.get("circulation_issues", []),
        style_signals=data.get("style_signals", []),
        behavioral_signals=behavioral_signals,
        tensions=data.get("tensions", []),
        hypothesis=data.get("hypothesis"),
        strengths=data.get("strengths", []),
        opportunities=data.get("opportunities", []),
        uncertain_aspects=data.get("uncertain_aspects", []),
        photo_count=photo_count,
    )


@activity.defn
async def analyze_room_photos(
    input: AnalyzeRoomPhotosInput,
) -> AnalyzeRoomPhotosOutput:
    """Analyze room photos using Claude Opus 4.6.

    This is the "read the room" skill — forms a structured hypothesis about
    the room before the intake conversation begins. Called eagerly by the
    workflow after 2+ room photos are uploaded.

    On failure, the workflow degrades gracefully (intake starts from blank slate).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ApplicationError("ANTHROPIC_API_KEY not set", non_retryable=True)

    if not input.room_photo_urls:
        raise ApplicationError("No room photos provided", non_retryable=True)

    # Resolve R2 storage keys to presigned URLs (same pattern as generate.py)
    from app.utils.r2 import resolve_urls

    room_urls = await asyncio.to_thread(resolve_urls, input.room_photo_urls)
    inspiration_urls = await asyncio.to_thread(resolve_urls, input.inspiration_photo_urls)
    resolved_input = AnalyzeRoomPhotosInput(
        room_photo_urls=room_urls,
        inspiration_photo_urls=inspiration_urls,
        inspiration_notes=input.inspiration_notes,
    )

    system_prompt = load_prompt()
    messages = build_messages(resolved_input)

    log.info(
        "analyze_room_start",
        room_photo_count=len(input.room_photo_urls),
        inspiration_photo_count=len(input.inspiration_photo_urls),
    )

    client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        response = await client.messages.create(  # type: ignore[call-overload]
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=[ANALYZE_ROOM_TOOL],
            tool_choice={"type": "tool", "name": "analyze_room"},
            messages=messages,
        )
    except anthropic.RateLimitError as e:
        log.warning("analyze_room_rate_limited")
        raise ApplicationError(f"Claude rate limited: {e}", non_retryable=False) from e
    except anthropic.APIStatusError as e:
        log.error("analyze_room_api_error", status=e.status_code)
        non_retryable = 400 <= e.status_code < 500
        raise ApplicationError(
            f"Claude API error ({e.status_code}): {e}", non_retryable=non_retryable
        ) from e

    data = extract_analysis(response)
    if not data:
        log.warning("analyze_room_no_tool_call")
        raise ApplicationError("Claude did not return analyze_room tool call", non_retryable=False)

    analysis = build_room_analysis(data, photo_count=len(input.room_photo_urls))

    log.info(
        "analyze_room_complete",
        room_type=analysis.room_type,
        confidence=analysis.room_type_confidence,
        furniture_count=len(analysis.furniture),
        has_hypothesis=analysis.hypothesis is not None,
    )

    return AnalyzeRoomPhotosOutput(analysis=analysis)
