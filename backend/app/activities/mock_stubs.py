"""Mock activity stubs for P0 skeleton testing.

These return realistic stub data so the Temporal workflow can be tested
end-to-end without real AI APIs. T2/T3 will build real implementations
in their owned files; these stubs get swapped out during P2 integration.
"""

from temporalio import activity

from app.models.contracts import (
    DesignOption,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    GenerateInpaintInput,
    GenerateInpaintOutput,
    GenerateRegenInput,
    GenerateRegenOutput,
    GenerateShoppingListInput,
    GenerateShoppingListOutput,
    ProductMatch,
)


@activity.defn
async def generate_designs(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    return GenerateDesignsOutput(
        options=[
            DesignOption(image_url="https://r2.example.com/mock/option_0.png", caption="Mock A"),
            DesignOption(image_url="https://r2.example.com/mock/option_1.png", caption="Mock B"),
        ]
    )


@activity.defn
async def generate_inpaint(input: GenerateInpaintInput) -> GenerateInpaintOutput:
    return GenerateInpaintOutput(revised_image_url="https://r2.example.com/mock/inpaint.png")


@activity.defn
async def generate_regen(input: GenerateRegenInput) -> GenerateRegenOutput:
    return GenerateRegenOutput(revised_image_url="https://r2.example.com/mock/regen.png")


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
    )


@activity.defn
async def purge_project_data(project_id: str) -> None:
    pass
