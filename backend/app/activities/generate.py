"""generate_designs activity — initial room redesign generation.

Takes room photos + design brief, generates 2 design options via
two parallel standalone Gemini calls (no chat session). Uploads
results to R2 and returns DesignOption objects.
"""

from __future__ import annotations

import asyncio
import io
import os
import random
import re
from pathlib import Path

import structlog
from google.genai import types
from PIL import Image
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.contracts import (
    DesignBrief,
    DesignOption,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    InspirationNote,
    RoomDimensions,
)
from app.utils.gemini_chat import (
    GEMINI_MODEL,
    IMAGE_CONFIG,
    MAX_INPUT_IMAGES,
    extract_image,
    extract_text,
    get_client,
)
from app.utils.http import download_images
from app.utils.prompt_versioning import get_active_version, load_versioned_prompt

logger = structlog.get_logger()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# Gemini-supported aspect ratios and their numeric values (width/height)
_SUPPORTED_RATIOS: list[tuple[str, float]] = [
    ("1:1", 1.0),
    ("3:4", 3 / 4),
    ("4:3", 4 / 3),
    ("9:16", 9 / 16),
    ("16:9", 16 / 9),
]


_VALID_RATIOS = {label for label, _ in _SUPPORTED_RATIOS}


def _detect_aspect_ratio(image: Image.Image) -> str:
    """Snap an image's aspect ratio to the nearest Gemini-supported value.

    Gemini supports: 1:1, 3:4, 4:3, 9:16, 16:9. We compute the input's
    width/height ratio and pick the closest match to avoid distortion.
    """
    w, h = image.size
    if h == 0 or w == 0:
        logger.warning("aspect_ratio_degenerate_image", width=w, height=h)
        return "1:1"
    ratio = w / h
    best_label = "1:1"
    best_diff = float("inf")
    for label, target in _SUPPORTED_RATIOS:
        diff = abs(ratio - target)
        if diff < best_diff:
            best_diff = diff
            best_label = label
    return best_label


def _make_image_config(aspect_ratio: str | None = None) -> types.GenerateContentConfig:
    """Build a per-call GenerateContentConfig, optionally overriding aspect ratio.

    Starts from the global IMAGE_CONFIG (2K resolution) and adds
    aspect_ratio when provided.
    """
    if aspect_ratio is None:
        return IMAGE_CONFIG
    if aspect_ratio not in _VALID_RATIOS:
        logger.warning("unsupported_aspect_ratio", aspect_ratio=aspect_ratio)
        return IMAGE_CONFIG
    return types.GenerateContentConfig(
        response_modalities=["TEXT", "IMAGE"],
        image_config=types.ImageConfig(image_size="2K", aspect_ratio=aspect_ratio),
    )


def _load_prompt(name: str) -> str:
    """Load a prompt template file, raising non-retryable error if missing."""
    path = PROMPTS_DIR / name
    try:
        return path.read_text()
    except FileNotFoundError as exc:
        raise ApplicationError(
            f"Prompt template not found: {name}",
            non_retryable=True,
        ) from exc


def _format_room_context(dims: RoomDimensions | None) -> str:
    """Format room dimensions into a human-readable context block for the prompt.

    Returns empty string when no dimensions are available so the prompt
    template's {room_context} placeholder collapses cleanly.
    """
    if dims is None:
        return ""

    parts = [
        f"\nRoom dimensions: {dims.width_m:.1f}m × {dims.length_m:.1f}m, "
        f"ceiling height {dims.height_m:.1f}m"
    ]
    if dims.floor_area_sqm is not None:
        parts.append(f"Floor area: {dims.floor_area_sqm:.1f} m²")
    if dims.openings:
        opening_types = [
            str(o.get("type", "opening")) for o in dims.openings if isinstance(o, dict)
        ]
        if opening_types:
            parts.append(f"Openings: {', '.join(opening_types)}")
    if dims.furniture:
        furniture_types = [
            str(f.get("type", "item")) for f in dims.furniture if isinstance(f, dict)
        ]
        if furniture_types:
            parts.append(f"Existing furniture detected: {', '.join(furniture_types)}")
    if dims.surfaces:
        surface_descs = [
            f"{s.get('type', 'surface')}: {s.get('material', 'unknown')}"
            for s in dims.surfaces
            if isinstance(s, dict)
        ]
        if surface_descs:
            parts.append(f"Surfaces: {', '.join(surface_descs)}")

    return "\n".join(parts)


_OPTION_VARIANTS: tuple[str, str] = (
    "Design Direction: Lean into the primary style elements from the brief. "
    "Emphasize the dominant mood and color palette. If pain points were mentioned, "
    "prioritize addressing the first one with a clean, polished solution.",
    "Design Direction: Explore a complementary variation. If the brief mentions "
    "multiple styles or pain points, lean toward the secondary elements. Try a "
    "bolder accent color, a different furniture arrangement, or an unexpected "
    "texture contrast — while staying true to the overall aesthetic.",
)


def _build_generation_prompt(
    brief: DesignBrief | None,
    inspiration_notes: list[InspirationNote],
    room_dimensions: RoomDimensions | None = None,
    option_variant: str = "",
) -> str:
    """Build the generation prompt from templates and brief data."""
    template = load_versioned_prompt("generation")
    preservation = load_versioned_prompt("room_preservation")

    brief_text = "Create a beautiful, modern interior design."
    keep_items_text = ""

    if brief:
        parts = [f"Room type: {brief.room_type}"]
        if brief.occupants:
            parts.append(f"Occupants: {brief.occupants}")
        if brief.lifestyle:
            parts.append(f"Lifestyle: {brief.lifestyle}")
        if brief.style_profile:
            sp = brief.style_profile
            if sp.mood:
                parts.append(f"Mood: {sp.mood}")
            if sp.colors:
                parts.append(f"Colors: {', '.join(sp.colors)}")
            if sp.textures:
                parts.append(f"Textures: {', '.join(sp.textures)}")
            if sp.lighting:
                parts.append(f"Lighting: {sp.lighting}")
            if sp.clutter_level:
                parts.append(f"Clutter level: {sp.clutter_level}")
        if brief.pain_points:
            parts.append(f"Pain points to address: {', '.join(brief.pain_points)}")
        if brief.constraints:
            parts.append(f"Constraints: {', '.join(brief.constraints)}")
        if brief.emotional_drivers:
            parts.append(f"Emotional drivers: {', '.join(brief.emotional_drivers)}")
        if brief.usage_patterns:
            parts.append(f"Usage patterns: {brief.usage_patterns}")
        if brief.renovation_willingness:
            parts.append(f"Renovation scope: {brief.renovation_willingness}")
        if brief.room_analysis_hypothesis:
            parts.append(f"Room analysis: {brief.room_analysis_hypothesis}")
        brief_text = "\n".join(parts)

        if brief.keep_items:
            keep_items_text = "- Keep these existing items in place: " + ", ".join(brief.keep_items)

    if inspiration_notes:
        notes = [f"  - Photo {n.photo_index}: {n.note}" for n in inspiration_notes]
        brief_text += "\n\nInspiration notes:\n" + "\n".join(notes)

    room_context = _format_room_context(room_dimensions)

    # Escape curly braces in user-provided text to prevent str.format() KeyError
    return template.format(
        brief=brief_text.replace("{", "{{").replace("}", "}}"),
        keep_items=keep_items_text.replace("{", "{{").replace("}", "}}"),
        room_context=room_context.replace("{", "{{").replace("}", "}}"),
        room_preservation=preservation,
        option_variant=option_variant,
    )


_PROJECT_ID_RE = re.compile(r"projects/([a-zA-Z0-9_-]+)/")


def _extract_project_id(urls: list[str]) -> str:
    """Extract project_id from R2 URLs containing the pattern projects/{id}/..."""
    for url in urls:
        match = _PROJECT_ID_RE.search(url)
        if match:
            return match.group(1)
    raise ApplicationError(
        "Could not extract project_id from photo URLs",
        non_retryable=True,
    )


def _upload_image(image: Image.Image, project_id: str, filename: str) -> str:
    """Upload a PIL Image to R2 and return the presigned URL."""
    from app.utils.r2 import generate_presigned_url, upload_object

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    key = f"projects/{project_id}/generated/{filename}"
    logger.info("r2_upload_start", key=key, size_bytes=buf.tell())
    upload_object(key, buf.getvalue(), content_type="image/png")
    return generate_presigned_url(key)


async def _generate_single_option(
    prompt: str,
    room_images: list[Image.Image],
    inspiration_images: list[Image.Image],
    option_index: int,
    source_urls: list[str] | None = None,
    image_config: types.GenerateContentConfig | None = None,
) -> Image.Image:
    """Generate a single design option via standalone Gemini call."""
    from app.utils.llm_cache import get_cached_bytes, set_cached_bytes

    # Dev/test cache: avoid redundant Gemini calls when prompt/inputs
    # haven't changed. Key includes prompt text, source URLs (stable R2 keys),
    # and option index to prevent cross-project collisions.
    # Will be removed in production (real users never send identical inputs).
    cache_key = [
        prompt,
        str(len(room_images)),
        str(len(inspiration_images)),
        str(option_index),
        *(source_urls or []),
    ]
    cached_png = get_cached_bytes("gemini_gen", cache_key)
    if cached_png:
        try:
            return Image.open(io.BytesIO(cached_png))
        except Exception:
            logger.warning("gemini_cache_corrupt", option=option_index)
            # Fall through to real Gemini call

    client = get_client()
    config = image_config or IMAGE_CONFIG

    # Build content: room photos + inspiration photos + text prompt
    contents: list = [*room_images, *inspiration_images, prompt]

    logger.info(
        "gemini_generate_start",
        option=option_index,
        num_room_images=len(room_images),
        num_inspiration_images=len(inspiration_images),
    )

    # Run sync Gemini call in thread pool to avoid blocking the event loop
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )

    result_image = extract_image(response)

    if result_image is None:
        # Retry once with explicit image request
        text_response = extract_text(response)
        logger.warning(
            "gemini_no_image_response",
            option=option_index,
            gemini_text=text_response[:300],
        )
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=contents + ["Please generate the room image now."],
            config=config,
        )
        result_image = extract_image(response)

    if result_image is None:
        text = extract_text(response)
        raise ApplicationError(
            f"Gemini returned text-only response for option {option_index}: {text[:200]}",
            non_retryable=False,
        )

    # Save to dev/test cache for reuse in subsequent runs
    buf = io.BytesIO()
    result_image.save(buf, format="PNG")
    set_cached_bytes("gemini_gen", cache_key, buf.getvalue())

    return result_image


async def _maybe_run_eval(
    options: list[Image.Image],
    original: Image.Image,
    brief: DesignBrief | None,
    generated_urls: list[str],
    original_url: str,
) -> None:
    """Run eval pipeline if EVAL_MODE is set. Never raises — logs and returns."""
    eval_mode = os.environ.get("EVAL_MODE", "off").lower()
    if eval_mode == "off":
        return

    prompt_version = get_active_version("generation")

    for idx, (option_img, gen_url) in enumerate(zip(options, generated_urls, strict=True)):
        try:
            from app.utils.image_eval import run_fast_eval

            fast = run_fast_eval(option_img, original, brief)
            logger.info(
                "eval_fast_result",
                option=idx,
                composite=fast.composite_score,
                clip_text=fast.clip_text_score,
                clip_image=fast.clip_image_score,
                edge_ssim=fast.edge_ssim_score,
                needs_deep=fast.needs_deep_eval,
                prompt_version=prompt_version,
            )

            deep_result = None
            if (
                eval_mode == "full"
                and brief is not None
                and (fast.needs_deep_eval or random.random() < 0.2)
            ):
                from app.activities.design_eval import evaluate_generation

                deep_result = await evaluate_generation(
                    original_photo_url=original_url,
                    generated_image_url=gen_url,
                    brief=brief,
                    fast_eval=fast,
                )
                logger.info(
                    "eval_deep_result",
                    option=idx,
                    total=deep_result.total,
                    tag=deep_result.tag,
                    prompt_version=prompt_version,
                )

            # Track scores
            from app.utils.score_tracking import append_score

            append_score(
                history_path=Path("eval_history.jsonl"),
                scenario=f"generation_option_{idx}",
                prompt_version=prompt_version,
                fast_eval=fast.__dict__,
                deep_eval=(
                    {"total": deep_result.total, "tag": deep_result.tag} if deep_result else {}
                ),
            )
        except Exception:
            logger.warning("eval_failed", option=idx, exc_info=True)


@activity.defn
async def generate_designs(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    """Generate 2 design options from room photos and design brief."""
    activity.logger.info(
        "generate_designs_start",
        num_room_photos=len(input.room_photo_urls),
        num_inspiration_photos=len(input.inspiration_photo_urls),
    )

    # Extract project_id from R2 key/URL path pattern: projects/{id}/...
    project_id = _extract_project_id(input.room_photo_urls)

    # Resolve R2 storage keys to presigned URLs (pass through existing URLs)
    from app.utils.r2 import resolve_urls

    room_urls = await asyncio.to_thread(resolve_urls, input.room_photo_urls)
    inspiration_urls = await asyncio.to_thread(resolve_urls, input.inspiration_photo_urls)

    try:
        # Download source images
        room_images, inspiration_images = await asyncio.gather(
            download_images(room_urls),
            download_images(inspiration_urls),
        )

        if not room_images:
            raise ApplicationError(
                "No room photos provided",
                non_retryable=True,
            )

        # Safety cap: product allows 2 room + 3 inspiration = 5 images max,
        # well under the model's 14-image ceiling. This guard only fires if
        # upstream validation is bypassed or limits change.
        total_images = len(room_images) + len(inspiration_images)
        if total_images > MAX_INPUT_IMAGES:
            max_inspiration = MAX_INPUT_IMAGES - len(room_images)
            if max_inspiration <= 0:
                room_images = room_images[:MAX_INPUT_IMAGES]
                inspiration_images = []
            else:
                inspiration_images = inspiration_images[:max_inspiration]
            logger.warning(
                "input_images_truncated",
                original_count=total_images,
                room_kept=len(room_images),
                inspiration_kept=len(inspiration_images),
            )

        # Build per-option prompts with differentiated variant instructions
        prompts = [
            _build_generation_prompt(
                input.design_brief,
                input.inspiration_notes,
                input.room_dimensions,
                option_variant=variant,
            )
            for variant in _OPTION_VARIANTS
        ]

        # Detect aspect ratio from first room photo to match output to input
        aspect_ratio = _detect_aspect_ratio(room_images[0])
        config = _make_image_config(aspect_ratio)
        logger.info("aspect_ratio_detected", aspect_ratio=aspect_ratio)

        # Generate 2 options in parallel with differentiated prompts
        # Pass original R2 keys (stable, not presigned) for cache key identity
        source_urls = input.room_photo_urls + input.inspiration_photo_urls
        option_0, option_1 = await asyncio.gather(
            *(
                _generate_single_option(
                    prompt, room_images, inspiration_images, idx, source_urls, config
                )
                for idx, prompt in enumerate(prompts)
            )
        )

        # Upload to R2 (sync boto3 calls run in thread pool)
        url_0 = await asyncio.to_thread(_upload_image, option_0, project_id, "option_0.png")
        url_1 = await asyncio.to_thread(_upload_image, option_1, project_id, "option_1.png")

        # Run eval if enabled — never blocks, never fails the activity
        await _maybe_run_eval(
            options=[option_0, option_1],
            original=room_images[0],
            brief=input.design_brief,
            generated_urls=[url_0, url_1],
            original_url=input.room_photo_urls[0],
        )

        return GenerateDesignsOutput(
            options=[
                DesignOption(image_url=url_0, caption="Design Option A"),
                DesignOption(image_url=url_1, caption="Design Option B"),
            ]
        )

    except ApplicationError:
        raise
    except Exception as e:
        error_type = type(e).__name__
        error_msg = str(e)

        # TODO: Catch typed google.genai exceptions when SDK stabilizes
        is_rate_limit = (
            "429" in error_msg
            or "RESOURCE_EXHAUSTED" in error_msg
            or "ResourceExhausted" in error_type
        )
        if is_rate_limit:
            raise ApplicationError(
                "Gemini rate limited",
                non_retryable=False,
            ) from e

        if "SAFETY" in error_msg or "blocked" in error_msg.lower():
            raise ApplicationError(
                f"Content policy violation: {error_msg[:200]}",
                non_retryable=True,
            ) from e

        raise ApplicationError(
            f"Generation failed: {error_type}: {error_msg[:200]}",
            non_retryable=False,
        ) from e
