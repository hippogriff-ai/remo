"""VLM eval layer — Claude Vision judge for generation, edit, and shopping quality.

The single authoritative eval layer. CLIP/SSIM metrics were removed (false positives).

Three rubric-based evaluators using Claude Sonnet as a multimodal judge:
1. Generation rubric (100 points, 9 criteria + 2 diagnostic scores)
2. Edit rubric (50 points, 5 criteria)
3. Shopping visual rubric (30 points, 3 criteria)

This is NOT a Temporal activity — it's a testing/eval utility called async
from the eval pipeline. Results are observability-only (never block workflow).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import structlog

from app.activities.shopping import _extract_json

if TYPE_CHECKING:
    from app.models.contracts import DesignBrief

log = structlog.get_logger("design_eval")

EVAL_MODEL = "claude-sonnet-4-5-20250929"
EVAL_MAX_TOKENS = 2048


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class CriterionScore:
    """Single criterion score with notes."""

    name: str
    score: int
    max_score: int
    notes: str = ""


@dataclass
class GenerationEvalResult:
    """Result of the generation VLM eval (100 points, 9 criteria + diagnostics)."""

    criteria: list[CriterionScore]
    total: int  # 0-100 (existing 9 criteria only)
    tag: str  # EXCELLENT, GOOD, ACCEPTABLE, WEAK, FAIL
    notes: str = ""
    diagnostics: dict[str, int] = field(default_factory=dict)
    artifact_check: dict[str, Any] = field(default_factory=dict)


@dataclass
class EditEvalResult:
    """Result of the edit VLM eval (50 points, 5 criteria)."""

    criteria: list[CriterionScore]
    total: int
    tag: str
    notes: str = ""
    artifact_check: dict[str, Any] = field(default_factory=dict)


@dataclass
class ShoppingVisualEvalResult:
    """Result of the shopping visual deep eval (30 points, 3 criteria)."""

    criteria: list[CriterionScore]
    total: int
    tag: str
    notes: str = ""


# ---------------------------------------------------------------------------
# Tag assignment
# ---------------------------------------------------------------------------


def _generation_tag(total: int) -> str:
    if total >= 85:
        return "EXCELLENT"
    if total >= 70:
        return "GOOD"
    if total >= 55:
        return "ACCEPTABLE"
    if total >= 40:
        return "WEAK"
    return "FAIL"


def _edit_tag(total: int) -> str:
    if total >= 42:
        return "EXCELLENT"
    if total >= 35:
        return "GOOD"
    if total >= 27:
        return "ACCEPTABLE"
    if total >= 20:
        return "WEAK"
    return "FAIL"


def _shopping_tag(total: int) -> str:
    if total >= 25:
        return "EXCELLENT"
    if total >= 20:
        return "GOOD"
    if total >= 15:
        return "ACCEPTABLE"
    if total >= 10:
        return "WEAK"
    return "FAIL"


# ---------------------------------------------------------------------------
# C1a: Generation Rubric (100 points, 9 criteria)
# ---------------------------------------------------------------------------

_GENERATION_RUBRIC = """\
Score this AI-generated room redesign against the rubric below. You are given:
1. The original room photo (before redesign)
2. The AI-generated redesign
3. The DesignBrief describing the requested style

## Rubric (100 points total):

1. **Photorealism** (0-15): 15: indistinguishable from a real photo. \
10: good but minor AI tells. 5: clearly AI-generated. 0: obvious artifacts.

2. **Style Adherence** (0-15): 15: nails the requested style perfectly. \
10: mostly right style. 5: generic/bland. 0: wrong style entirely.

3. **Color Palette** (0-10): 10: matches brief colors with 60/30/10 proportions. \
7: right color family. 3: clashing colors. 0: completely wrong palette.

4. **Room Preservation** (0-20): 20: walls/windows/doors/ceiling identical to original. \
15: minor geometric drift. 5: noticeable structural changes. 0: different room.

5. **Furniture Scale** (0-10): 10: all furniture proportional to room. \
7: mostly right. 3: some items obviously wrong scale. 0: clearly impossible sizes.

6. **Lighting** (0-10): 10: realistic shadows + consistent light sources. \
7: minor inconsistencies. 3: flat/unrealistic. 0: physically impossible lighting.

7. **Design Coherence** (0-10): 10: unified design vision. \
7: mostly cohesive. 3: mismatched elements. 0: chaotic/incoherent.

8. **Brief Compliance** (0-5): 5: all brief constraints met. \
3: most constraints met. 1: few constraints met. 0: brief ignored.

9. **Keep Items** (0-5): 5: all keep_items preserved exactly. \
3: most preserved. 0: kept items were replaced.

DIAGNOSTIC SCORES (reported separately, not part of the 100-point total):
D1. **Instruction Adherence** (0-10): 10: all generation prompt directives followed exactly. \
7: most followed. 3: partially followed. 0: prompt ignored or contradicted.
D2. **Spatial Accuracy** (0-5): 5: furniture sizes, room proportions, and spatial \
relationships match the provided room context. 3: mostly correct. 0: impossible \
spatial relationships or wildly wrong proportions."""

_GENERATION_RESPONSE_FORMAT = """\
Respond with EXACTLY this JSON (no markdown fences):
{{
  "photorealism": <0-15>,
  "style_adherence": <0-15>,
  "color_palette": <0-10>,
  "room_preservation": <0-20>,
  "furniture_scale": <0-10>,
  "lighting": <0-10>,
  "design_coherence": <0-10>,
  "brief_compliance": <0-5>,
  "keep_items": <0-5>,
  "total": <sum of above 9 criteria, 0-100>,
  "instruction_adherence": <0-10>,
  "spatial_accuracy": <0-5>,
  "notes": "<1-2 sentences>"
}}"""

_GENERATION_CRITERIA_MAX = {
    "photorealism": 15,
    "style_adherence": 15,
    "color_palette": 10,
    "room_preservation": 20,
    "furniture_scale": 10,
    "lighting": 10,
    "design_coherence": 10,
    "brief_compliance": 5,
    "keep_items": 5,
}


# ---------------------------------------------------------------------------
# C1b: Edit Rubric (50 points, 5 criteria)
# ---------------------------------------------------------------------------

_EDIT_RUBRIC = """\
Score this AI-edited room image against the rubric below. You are given:
1. The original design image (before edit)
2. The edited image (after edit)
3. The edit instruction describing what should change and where

## Rubric (50 points total):

1. **Edit Fidelity** (0-15): 15: targeted areas changed exactly as instructed. \
10: mostly correct changes. 5: partially correct. 0: wrong changes or no change.

2. **Preservation Fidelity** (0-15): 15: non-targeted areas completely unchanged. \
10: minor unintended changes. 5: noticeable drift. 0: significant unwanted changes.

3. **Artifact Cleanliness** (0-10): 10: clean photorealistic output with no visual \
artifacts, overlays, or markers. 7: minor artifacts. 3: clearly visible artifacts \
or non-photorealistic elements. 0: prominent artifacts or annotation-like shapes.

4. **Seamless Blending** (0-5): 5: edited regions blend naturally with surroundings. \
3: minor seam artifacts. 0: obvious cut-paste boundaries.

5. **Instruction Accuracy** (0-5): 5: edit matches user's text instruction perfectly. \
3: partially matches. 0: doesn't match instruction."""

_EDIT_RESPONSE_FORMAT = """\
Respond with EXACTLY this JSON (no markdown fences):
{{
  "edit_fidelity": <0-15>,
  "preservation_fidelity": <0-15>,
  "artifact_cleanliness": <0-10>,
  "seamless_blending": <0-5>,
  "instruction_accuracy": <0-5>,
  "total": <sum, 0-50>,
  "notes": "<1-2 sentences>"
}}"""

_EDIT_CRITERIA_MAX = {
    "edit_fidelity": 15,
    "preservation_fidelity": 15,
    "artifact_cleanliness": 10,
    "seamless_blending": 5,
    "instruction_accuracy": 5,
}


# ---------------------------------------------------------------------------
# C1c: Shopping Visual Rubric (30 points, 3 criteria)
# ---------------------------------------------------------------------------

_SHOPPING_RUBRIC = """\
Score how well this product matches the room design. You are given:
1. The redesigned room image
2. The product image
3. A description of the product's role in the design

## Rubric (30 points total):

1. **Visual Match** (0-15): 15: product looks exactly like what was described \
and shown in the room render. 10: close match. 5: vaguely similar. 0: wrong product.

2. **Style Consistency** (0-10): 10: product fits the room's aesthetic perfectly. \
7: mostly fits. 3: somewhat clashing. 0: completely wrong style for the room.

3. **Scale Appropriateness** (0-5): 5: product would physically fit in the space. \
3: slightly oversized/undersized. 0: obviously wrong dimensions for the room."""

_SHOPPING_RESPONSE_FORMAT = """\
Respond with EXACTLY this JSON (no markdown fences):
{{
  "visual_match": <0-15>,
  "style_consistency": <0-10>,
  "scale_appropriateness": <0-5>,
  "total": <sum, 0-30>,
  "notes": "<1-2 sentences>"
}}"""

_SHOPPING_CRITERIA_MAX = {
    "visual_match": 15,
    "style_consistency": 10,
    "scale_appropriateness": 5,
}


# ---------------------------------------------------------------------------
# Image loading helpers
# ---------------------------------------------------------------------------


async def _load_image_base64(url: str) -> tuple[str, str]:
    """Download an image from URL and return (base64-encoded bytes, media_type)."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            log.error("eval_image_download_failed", url=url, status=e.response.status_code)
            raise
        except httpx.TimeoutException:
            log.error("eval_image_download_timeout", url=url)
            raise
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        if content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            content_type = "image/png" if url.lower().endswith(".png") else "image/jpeg"
        return base64.standard_b64encode(resp.content).decode("ascii"), content_type


def _image_content_block(base64_data: str, media_type: str = "image/jpeg") -> dict[str, Any]:
    """Build an Anthropic image content block from base64 data."""
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": base64_data},
    }


def _text_block(text: str) -> dict[str, Any]:
    """Build an Anthropic text content block."""
    return {"type": "text", "text": text}


# ---------------------------------------------------------------------------
# Core eval function
# ---------------------------------------------------------------------------


async def _run_eval(
    content_blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Send multimodal content to Claude Sonnet and parse JSON response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    async with anthropic.AsyncAnthropic(api_key=api_key) as client:
        try:
            response = await client.messages.create(
                model=EVAL_MODEL,
                max_tokens=EVAL_MAX_TOKENS,
                messages=[{"role": "user", "content": content_blocks}],  # type: ignore[typeddict-item]
            )
        except anthropic.RateLimitError:
            log.warning("eval_claude_rate_limited", model=EVAL_MODEL)
            raise
        except anthropic.APIStatusError as e:
            log.error("eval_claude_api_error", model=EVAL_MODEL, status=e.status_code)
            raise

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    result = _extract_json(text)
    if not result:
        log.warning("eval_json_parse_failed", text=text[:200])
        raise ValueError(f"Could not extract JSON from eval response: {text[:200]}")
    return result


def _parse_criteria(raw: dict[str, Any], criteria_max: dict[str, int]) -> list[CriterionScore]:
    """Parse raw JSON scores into CriterionScore objects."""
    criteria = []
    for name, max_score in criteria_max.items():
        score = raw.get(name, 0)
        if not isinstance(score, int):
            try:
                score = int(score) if score else 0
            except (ValueError, TypeError):
                score = 0
        score = max(0, min(score, max_score))
        criteria.append(CriterionScore(name=name, score=score, max_score=max_score))
    return criteria


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def evaluate_generation(
    original_photo_url: str,
    generated_image_url: str,
    brief: DesignBrief,
    generation_prompt: str = "",
    room_context: str = "",
    artifact_check: dict | None = None,
) -> GenerationEvalResult:
    """Run the generation VLM eval (100 points, 9 criteria + diagnostics).

    Downloads both images, sends them to Claude Sonnet with the brief,
    optional generation prompt and room context, and rubric. Returns
    per-criterion scores plus diagnostic scores (instruction_adherence,
    spatial_accuracy) that are NOT part of the 100-point total.
    """
    orig_b64, orig_mime = await _load_image_base64(original_photo_url)
    gen_b64, gen_mime = await _load_image_base64(generated_image_url)

    brief_json = brief.model_dump_json(indent=2)

    # Build prompt text with optional generation prompt and room context
    sections = [
        f"{_GENERATION_RUBRIC}\n\n",
        f"## DesignBrief:\n```json\n{brief_json}\n```\n\n",
    ]
    if generation_prompt:
        sections.append(f"## Generation Prompt:\n```\n{generation_prompt}\n```\n\n")
    if room_context:
        sections.append(f"## Room Context (LiDAR):\n{room_context}\n\n")
    sections.append(_GENERATION_RESPONSE_FORMAT)
    prompt_text = "".join(sections)

    content = [
        _text_block("Original room photo:"),
        _image_content_block(orig_b64, orig_mime),
        _text_block("AI-generated redesign:"),
        _image_content_block(gen_b64, gen_mime),
        _text_block(prompt_text),
    ]

    raw = await _run_eval(content)
    criteria = _parse_criteria(raw, _GENERATION_CRITERIA_MAX)
    total = sum(c.score for c in criteria)
    tag = _generation_tag(total)

    # Extract diagnostic scores (not part of the 100-point total)
    diagnostics: dict[str, int] = {}
    for diag_key, diag_max in [("instruction_adherence", 10), ("spatial_accuracy", 5)]:
        val = raw.get(diag_key, 0)
        if not isinstance(val, int):
            try:
                val = int(val) if val else 0
            except (ValueError, TypeError):
                val = 0
        diagnostics[diag_key] = max(0, min(val, diag_max))

    return GenerationEvalResult(
        criteria=criteria,
        total=total,
        tag=tag,
        notes=raw.get("notes", ""),
        diagnostics=diagnostics,
        artifact_check=artifact_check or {},
    )


async def evaluate_edit(
    original_image_url: str,
    edited_image_url: str,
    edit_instruction: str,
    artifact_check: dict | None = None,
) -> EditEvalResult:
    """Run the edit VLM eval (50 points, 5 criteria).

    Downloads both images, sends them to Claude Sonnet with the edit
    instruction and rubric, and returns per-criterion scores.
    """
    orig_b64, orig_mime = await _load_image_base64(original_image_url)
    edit_b64, edit_mime = await _load_image_base64(edited_image_url)

    prompt_text = (
        f"{_EDIT_RUBRIC}\n\n## Edit instruction:\n{edit_instruction}\n\n{_EDIT_RESPONSE_FORMAT}"
    )

    content = [
        _text_block("Original image (before edit):"),
        _image_content_block(orig_b64, orig_mime),
        _text_block("Edited image (after edit):"),
        _image_content_block(edit_b64, edit_mime),
        _text_block(prompt_text),
    ]

    raw = await _run_eval(content)
    criteria = _parse_criteria(raw, _EDIT_CRITERIA_MAX)
    total = sum(c.score for c in criteria)
    tag = _edit_tag(total)

    return EditEvalResult(
        criteria=criteria,
        total=total,
        tag=tag,
        notes=raw.get("notes", ""),
        artifact_check=artifact_check or {},
    )


async def evaluate_shopping_visual(
    room_image_url: str,
    product_image_url: str,
    product_description: str,
) -> ShoppingVisualEvalResult:
    """Run the shopping visual deep eval (30 points, 3 criteria).

    Downloads the room and product images, sends them to Claude Sonnet
    with the product description and rubric.
    """
    room_b64, room_mime = await _load_image_base64(room_image_url)
    product_b64, product_mime = await _load_image_base64(product_image_url)

    prompt_text = (
        f"{_SHOPPING_RUBRIC}\n\n"
        f"## Product description:\n{product_description}\n\n"
        f"{_SHOPPING_RESPONSE_FORMAT}"
    )

    content = [
        _text_block("Redesigned room:"),
        _image_content_block(room_b64, room_mime),
        _text_block("Product image:"),
        _image_content_block(product_b64, product_mime),
        _text_block(prompt_text),
    ]

    raw = await _run_eval(content)
    criteria = _parse_criteria(raw, _SHOPPING_CRITERIA_MAX)
    total = sum(c.score for c in criteria)
    tag = _shopping_tag(total)

    return ShoppingVisualEvalResult(
        criteria=criteria,
        total=total,
        tag=tag,
        notes=raw.get("notes", ""),
    )
