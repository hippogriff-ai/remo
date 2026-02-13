"""Mock activity stubs for P0 skeleton testing.

These return realistic stub data so the Temporal workflow can be tested
end-to-end without real AI APIs. T2/T3 will build real implementations
in their owned files; these stubs get swapped out during P2 integration.
"""

import tempfile
from pathlib import Path
from uuid import uuid4

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.contracts import (
    CostBreakdown,
    DesignOption,
    EditDesignInput,
    EditDesignOutput,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    GenerateShoppingListInput,
    GenerateShoppingListOutput,
    LoadSkillInput,
    LoadSkillOutput,
    ProductMatch,
    StyleSkillPack,
)

# Cross-process one-shot error injection for E2E-11 testing.
# The API writes this sentinel file; the worker activity checks + deletes it.
FORCE_FAILURE_SENTINEL = Path(tempfile.gettempdir()) / "remo-force-failure"


@activity.defn
async def generate_designs(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    if FORCE_FAILURE_SENTINEL.exists():
        FORCE_FAILURE_SENTINEL.unlink(missing_ok=True)
        raise ApplicationError("Injected failure for E2E testing", non_retryable=True)
    return GenerateDesignsOutput(
        options=[
            DesignOption(image_url="https://r2.example.com/mock/option_0.png", caption="Mock A"),
            DesignOption(image_url="https://r2.example.com/mock/option_1.png", caption="Mock B"),
        ]
    )


@activity.defn
async def edit_design(input: EditDesignInput) -> EditDesignOutput:
    return EditDesignOutput(
        revised_image_url=f"https://r2.example.com/mock/edit_{uuid4().hex[:8]}.png",
        chat_history_key=f"chat/{input.project_id}/history.json",
    )


@activity.defn
async def generate_shopping_list(
    input: GenerateShoppingListInput,
) -> GenerateShoppingListOutput:
    return GenerateShoppingListOutput(
        items=[
            ProductMatch(
                category_group="Furniture",
                product_name="Mock Chair",
                retailer="Mock Store",
                price_cents=9999,
                product_url="https://example.com/chair",
                confidence_score=0.9,
                why_matched="Mock match",
            )
        ],
        total_estimated_cost_cents=9999,
        cost_breakdown=CostBreakdown(
            materials_cents=9999,
            total_low_cents=9999,
            total_high_cents=12000,
        ),
    )


_MOCK_SKILLS: dict[str, StyleSkillPack] = {
    "mid-century-modern": StyleSkillPack(
        skill_id="mid-century-modern",
        name="Mid-Century Modern",
        description="Clean lines, organic curves, and a love of different materials",
        style_tags=["retro", "organic", "minimal"],
        knowledge={"principles": ["form follows function", "less is more"]},
    ),
    "japandi": StyleSkillPack(
        skill_id="japandi",
        name="Japandi",
        description="Japanese minimalism meets Scandinavian warmth",
        style_tags=["minimal", "natural", "warm"],
        knowledge={"principles": ["wabi-sabi", "hygge", "natural materials"]},
    ),
}


@activity.defn
async def load_style_skill(input: LoadSkillInput) -> LoadSkillOutput:
    """Mock skill loader — returns sample skill packs for known IDs."""
    packs = []
    not_found = []
    for skill_id in input.skill_ids:
        if skill_id in _MOCK_SKILLS:
            packs.append(_MOCK_SKILLS[skill_id])
        else:
            not_found.append(skill_id)
    return LoadSkillOutput(skill_packs=packs, not_found=not_found)


@activity.defn
async def purge_project_data(project_id: str) -> None:
    """No-op purge for testing. The worker uses real purge.py instead."""
    pass
