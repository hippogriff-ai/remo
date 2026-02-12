"""Scripted conversation scenarios for live eval of the intake agent.

Each scenario defines a sequence of user messages that test a specific
quality dimension. The agent runs through the full conversation, then
the judge evaluates the resulting brief + transcript.

6 scenarios, all quick mode (~3-4 turns):
  A: Normal clear user    — baseline brief quality
  B: Vague user           — probing quality
  C: Contradictory user   — contradiction detection
  D: Rich first answer    — adaptive behavior
  E: Bedroom/insomnia     — room-specific guidance
  F: Budget-constrained   — constraint handling
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalScenario:
    """A scripted conversation scenario for live eval."""

    id: str
    name: str
    description: str
    mode: str
    messages: list[str]
    min_scores: dict[str, int] = field(default_factory=dict)


SCENARIOS: list[EvalScenario] = [
    EvalScenario(
        id="A_normal_user",
        name="Normal clear user",
        description="Clear preferences — baseline for brief quality",
        mode="quick",
        messages=[
            "It's a living room for me and my partner, both in our 30s. "
            "We have a golden retriever.",
            "We love mid-century modern with a warm twist — think walnut furniture, warm tones, "
            "nothing too cold or sterile. The room gets good morning light but is dark by evening.",
            "We want to keep our vintage Turkish rug. Good reading lighting is a must — "
            "we both read on the sofa every evening. The dog sheds a lot so fabrics "
            "need to be practical.",
        ],
    ),
    EvalScenario(
        id="B_vague_user",
        name="Vague user",
        description="Every answer is vague — tests probing quality",
        mode="quick",
        messages=[
            "It's a bedroom.",
            "I just want it to look nice, you know? Comfortable.",
            "Yeah, pretty and comfortable. I don't really know about design stuff.",
        ],
        min_scores={"diagnostic_depth": 2},
    ),
    EvalScenario(
        id="C_contradictory_user",
        name="Contradictory user",
        description="Contradictory preferences — tests detection + resolution",
        mode="quick",
        messages=[
            "We want a super minimalist living room. Clean, empty, zen-like.",
            "But we have about 200 books and 50 vintage figurines from our travels "
            "that we absolutely need to display. They tell our story.",
            "Both — I want minimalist but with all my stuff visible. "
            "Also I want bold colors but keep it calm.",
        ],
        min_scores={"diagnostic_depth": 3, "style_coherence": 5},
    ),
    EvalScenario(
        id="D_rich_first_answer",
        name="Rich first answer",
        description="Multi-domain first answer — tests adaptive behavior",
        mode="quick",
        messages=[
            "It's our living room, family of 4 with 2 dogs. We love a cozy "
            "Scandinavian feel — warm woods, white walls, lots of plants. "
            "The room is too dark and the furniture is way too big for the space. "
            "We definitely want to keep the antique bookshelf from my grandmother. "
            "Budget is around $5000 and we're renting.",
            "Yes, that sounds right. We also use the room for family movie nights "
            "and the kids do homework on the coffee table.",
            "Warm and soft lighting — nothing harsh. We like things tidy but "
            "the kids' toys are always everywhere.",
        ],
    ),
    EvalScenario(
        id="E_bedroom_insomnia",
        name="Bedroom for insomnia",
        description="Sleep-optimized bedroom — tests room-specific guidance",
        mode="quick",
        messages=[
            "It's our master bedroom. My partner and I both struggle with insomnia — "
            "we've tried everything and the bedroom just doesn't help.",
            "Cool, calming, like a cave but beautiful. No stimulation. "
            "We want to feel like we're in a luxury sleep retreat.",
            "Blues and greens, blackout curtains, no electronics visible. "
            "We read in bed so we need some kind of reading light that "
            "doesn't wake the other person.",
        ],
        min_scores={"design_intelligence": 6},
    ),
    EvalScenario(
        id="F_budget_constrained",
        name="Budget-constrained kitchen",
        description="Tight budget + rental + kids — tests constraint handling",
        mode="quick",
        messages=[
            "Kitchen, family of 5 with three young kids under 8. "
            "We rent so nothing permanent — no painting, no drilling.",
            "We're on a tight budget, around $3000. I want it to feel bright "
            "and airy — right now it's dark and depressing. Modern farmhouse vibe.",
            "Good task lighting is critical — I cook every night and can barely "
            "see what I'm doing. The kids need a homework spot too.",
        ],
        min_scores={"completeness": 6},
    ),
]


# Maps weak criteria to the system prompt section that needs tuning.
CRITERION_TO_PROMPT_SECTION: dict[str, list[str]] = {
    "style_coherence": [
        "TRANSLATION ENGINE table (style translations)",
        "DIAGNOSE pipeline: NARRATE step (unified narrative)",
    ],
    "color_strategy": [
        "TRANSLATION ENGINE table (color entries with 60/30/10)",
        "Elevation Rules: 'colors' field instructions",
        "Color Psychology reference section",
    ],
    "lighting_design": [
        "Room-Specific Guidance (lighting specs per room)",
        "Elevation Rules: 'lighting' field instructions",
        "20-Rule Validation: rules 1, 3, 7, 8",
    ],
    "material_texture": [
        "TRANSLATION ENGINE table (texture entries)",
        "Elevation Rules: 'textures' field instructions",
    ],
    "design_intelligence": [
        "Three-layer design stack (spatial, human-centered, emotional)",
        "Biophilic element and prospect-refuge instructions",
        "DIAGNOSE pipeline: GENERATE step",
    ],
    "diagnostic_depth": [
        "DIAGNOSE pipeline: DETECT + ANALYZE steps",
        "Diagnostic Question Bank",
        "Adaptive Behavior: 'probe deeper' instructions",
    ],
    "actionability": [
        "OUTPUT FORMAT: elevation rules for all fields",
        "DIAGNOSE pipeline: SPECIFY step",
        "20-Rule Validation",
    ],
    "completeness": [
        "Design Domain Notepad (all 11 domains)",
        "Mode Instructions (domain coverage targets)",
        "Room-Specific Guidance",
    ],
    "user_fidelity": [
        "Summary Turn instructions (show translations)",
        "DIAGNOSE pipeline: EVALUATE step",
    ],
}

# Maximum possible score for each criterion (for percentage calculations).
CRITERION_MAX: dict[str, int] = {
    "style_coherence": 10,
    "color_strategy": 15,
    "lighting_design": 15,
    "material_texture": 15,
    "design_intelligence": 10,
    "diagnostic_depth": 5,
    "actionability": 15,
    "completeness": 10,
    "user_fidelity": 5,
}
