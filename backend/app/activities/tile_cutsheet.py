"""Generate a multi-page cut-sheet PDF for tile mode.

Activity stub — full implementation in PR 5. Will produce the 6-page PDF
defined in design_handoff_replace_material/README.md §6 (summary, per-surface
diagrams, shopping list), reusing pack_surface output for the diagrams.
"""

from __future__ import annotations

import structlog
from temporalio import activity

from app.models.contracts import GenerateCutSheetInput, GenerateCutSheetOutput

logger = structlog.get_logger()


@activity.defn
async def generate_cut_sheet_pdf(
    pdf_input: GenerateCutSheetInput,
) -> GenerateCutSheetOutput:
    """Produce a contractor-ready multi-page cut-sheet PDF.

    PR 1: stub — workflow wiring + signal flow verified without the real
    PDF generation. Raises NotImplementedError so no empty PDF is ever
    uploaded as a real cut-sheet.
    """
    logger.info(
        "generate_cut_sheet_pdf_stub_called",
        project_id=pdf_input.project_id,
        surface_count=len(pdf_input.surfaces),
    )
    _: type[GenerateCutSheetOutput] = GenerateCutSheetOutput
    raise NotImplementedError(
        "generate_cut_sheet_pdf: wired in PR 5 (reportlab/weasyprint + "
        "pack_surface SVG rendering). See plan PR 5 section."
    )
