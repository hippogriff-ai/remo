"""generate_designs activity — initial room redesign generation.

Takes room photos + design brief, generates 2 design options via
two parallel standalone Gemini calls (no chat session). Uploads
results to R2 and returns DesignOption objects.
"""

from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

import structlog
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

logger = structlog.get_logger()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


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
        opening_types = [o.get("type", "opening") for o in dims.openings]
        parts.append(f"Openings: {', '.join(opening_types)}")
    if dims.furniture:
        furniture_types = [f.get("type", "item") for f in dims.furniture]
        parts.append(f"Existing furniture detected: {', '.join(furniture_types)}")
    if dims.surfaces:
        surface_descs = [
            f"{s.get('type', 'surface')}: {s.get('material', 'unknown')}" for s in dims.surfaces
        ]
        parts.append(f"Surfaces: {', '.join(surface_descs)}")

    return "\n".join(parts)


def _build_generation_prompt(
    brief: DesignBrief | None,
    inspiration_notes: list[InspirationNote],
    room_dimensions: RoomDimensions | None = None,
) -> str:
    """Build the generation prompt from templates and brief data."""
    template = _load_prompt("generation.txt")
    preservation = _load_prompt("room_preservation.txt")

    brief_text = "Create a beautiful, modern interior design."
    keep_items_text = ""

    if brief:
        parts = []
        parts.append(f"Room type: {brief.room_type}")
        if brief.occupants:
            parts.append(f"Occupants: {brief.occupants}")
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

    # Build content: room photos + inspiration photos + text prompt
    contents: list = []
    for img in room_images:
        contents.append(img)
    for img in inspiration_images:
        contents.append(img)
    contents.append(prompt)

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
        config=IMAGE_CONFIG,
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
            config=IMAGE_CONFIG,
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

        # Build prompt
        prompt = _build_generation_prompt(
            input.design_brief, input.inspiration_notes, input.room_dimensions
        )

        # Generate 2 options in parallel
        # Pass original R2 keys (stable, not presigned) for cache key identity
        source_urls = input.room_photo_urls + input.inspiration_photo_urls
        option_0, option_1 = await asyncio.gather(
            _generate_single_option(prompt, room_images, inspiration_images, 0, source_urls),
            _generate_single_option(prompt, room_images, inspiration_images, 1, source_urls),
        )

        # Upload to R2 (sync boto3 calls run in thread pool)
        url_0 = await asyncio.to_thread(_upload_image, option_0, project_id, "option_0.png")
        url_1 = await asyncio.to_thread(_upload_image, option_1, project_id, "option_1.png")

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
