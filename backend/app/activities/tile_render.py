"""Render a photoreal bathroom with the new tile applied.

Activity stub — full implementation in PR 5. Will call the existing Gemini
image-edit pipeline (backend/app/activities/generate.py) with a geometry-
locked, material-swap prompt derived from SurfacePatch + MaterialSpec, and
upload the result to R2.
"""

from __future__ import annotations

import structlog
from temporalio import activity

from app.models.contracts import RenderTileInput, RenderTileOutput

logger = structlog.get_logger()


@activity.defn
async def render_tile_design(tile_input: RenderTileInput) -> RenderTileOutput:
    """Produce a photoreal render of the user's actual bathroom with new tile.

    PR 1: stub — workflow wiring + signal flow verified without the real
    Gemini call. Raises NotImplementedError so no placeholder image is ever
    treated as a real render.
    """
    logger.info(
        "render_tile_design_stub_called",
        project_id=tile_input.project_id,
        surface_count=len(tile_input.surfaces),
    )
    # Reference RenderTileOutput so the activity signature is fully materialized
    # at import time (Temporal uses it for (de)serialization).
    _: type[RenderTileOutput] = RenderTileOutput
    raise NotImplementedError(
        "render_tile_design: wired in PR 5 (Gemini mask-edit with "
        "material-swap prompt). See plan PR 5 section."
    )
