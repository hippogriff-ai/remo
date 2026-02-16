"""generate_designs activity — initial room redesign generation.

Takes room photos + design brief, generates 2 design options via
two parallel standalone Gemini calls (no chat session). Uploads
results to R2 and returns DesignOption objects.
"""

from __future__ import annotations

import asyncio
import io
import os
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
from app.utils.prompt_versioning import (
    get_active_version,
    load_versioned_prompt,
    strip_changelog_lines,
)

logger = structlog.get_logger()

# Strong references to background eval tasks to prevent GC before completion
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

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


def _orientation_to_compass(degrees: float) -> str:
    """Convert wall orientation in degrees to a compass direction label.

    RoomPlan convention: 0° = south-facing, 90° = west-facing,
    180° = north-facing, 270° = east-facing. Intercardinals at 45° intervals.
    """
    # Normalize to [0, 360)
    deg = degrees % 360
    # 8 compass points at 45° intervals, starting from South at 0°
    directions = [
        "south",
        "southwest",
        "west",
        "northwest",
        "north",
        "northeast",
        "east",
        "southeast",
    ]
    index = round(deg / 45) % 8
    return directions[index]


# Maximum furniture items to include in room context (noise reduction)
_MAX_FURNITURE_ITEMS = 15
# Minimum furniture dimension to include (meters) — smaller items are noise
_MIN_FURNITURE_SIZE_M = 0.3


def _format_room_context(dims: RoomDimensions | None) -> str:
    """Format room dimensions into structured scene data for the generation prompt.

    Returns empty string when no dimensions are available so the prompt
    template's {room_context} placeholder collapses cleanly.

    Output uses section headers (ROOM GEOMETRY / WALLS / FIXED OPENINGS /
    EXISTING FURNITURE) for clear Gemini parsing. Includes wall compass
    orientations, relative furniture proportions, and noise filtering.

    Uses fallback labels for None types/materials (G23).
    Gracefully handles non-dict entries and non-numeric dimensions.
    """
    if dims is None:
        return ""

    sections: list[str] = []

    # --- ROOM GEOMETRY ---
    geo_lines = [
        f"- Dimensions: {dims.width_m:.1f}m wide × {dims.length_m:.1f}m long, "
        f"ceiling height {dims.height_m:.1f}m"
    ]
    if dims.floor_area_sqm is not None:
        geo_lines.append(f"- Floor area: {dims.floor_area_sqm:.1f} m²")
    sections.append("\nROOM GEOMETRY (LiDAR-measured, precise):\n" + "\n".join(geo_lines))

    # --- WALLS ---
    if dims.walls:
        wall_lines: list[str] = []
        for w in dims.walls:
            if not isinstance(w, dict):
                continue
            wid = w.get("id", f"wall_{len(wall_lines)}")
            ww = w.get("width")
            wh = w.get("height")
            orientation = w.get("orientation")
            try:
                width_str = f"{float(ww):.1f}m wide" if ww is not None else ""
                height_str = f"{float(wh):.1f}m tall" if wh is not None else ""
            except (TypeError, ValueError):
                width_str = ""
                height_str = ""
            dim_parts = [p for p in [width_str, height_str] if p]
            compass = ""
            if orientation is not None:
                try:
                    deg = float(orientation)
                    direction = _orientation_to_compass(deg)
                    compass = f", faces {direction} ({deg:.0f}°)"
                except (TypeError, ValueError):
                    pass
            if dim_parts:
                wall_lines.append(f"- {wid}: {', '.join(dim_parts)}{compass}")
            elif compass:
                wall_lines.append(f"- {wid}{compass}")
        if wall_lines:
            sections.append(f"WALLS ({len(wall_lines)} detected):\n" + "\n".join(wall_lines))

    # --- FIXED OPENINGS ---
    if dims.openings:
        opening_descs = []
        for o in dims.openings:
            if not isinstance(o, dict):
                continue
            otype = str(o.get("type") or "opening")
            ow = o.get("width")
            oh = o.get("height")
            if ow is not None and oh is not None:
                try:
                    opening_descs.append(f"- {otype} ({float(ow):.1f}m × {float(oh):.1f}m)")
                except (TypeError, ValueError):
                    opening_descs.append(f"- {otype}")
            else:
                opening_descs.append(f"- {otype}")
        if opening_descs:
            sections.append("FIXED OPENINGS (do not relocate):\n" + "\n".join(opening_descs))

    # --- EXISTING FURNITURE ---
    if dims.furniture:
        shorter_wall = min(dims.width_m, dims.length_m)
        furniture_descs = []
        for f in dims.furniture:
            if not isinstance(f, dict):
                continue
            ftype = str(f.get("type") or "item")
            fw = f.get("width")
            fd = f.get("depth")
            fh = f.get("height")
            f_dims: list[str] = []
            try:
                if fw is not None:
                    f_dims.append(f"{float(fw):.1f}m")
                if fd is not None:
                    f_dims.append(f"{float(fd):.1f}m")
                if fh is not None:
                    f_dims.append(f"h{float(fh):.1f}m")
            except (TypeError, ValueError):
                f_dims = []

            # Skip small items (< 0.3m in all measured dimensions) as noise
            if f_dims:
                try:
                    measured = []
                    if fw is not None:
                        measured.append(float(fw))
                    if fd is not None:
                        measured.append(float(fd))
                    if fh is not None:
                        measured.append(float(fh))
                    if measured and max(measured) < _MIN_FURNITURE_SIZE_M:
                        continue
                except (TypeError, ValueError):
                    pass

            desc = f"- {ftype}"
            if f_dims:
                # Footprint × height format
                footprint_parts = []
                height_part = ""
                for p in f_dims:
                    if p.startswith("h"):
                        height_part = p[1:]  # strip 'h' prefix for new format
                    else:
                        footprint_parts.append(p)
                if footprint_parts and height_part:
                    fp_label = "footprint" if len(footprint_parts) >= 2 else "wide"
                    desc += f": {' × '.join(footprint_parts)} {fp_label}, {height_part} tall"
                elif footprint_parts:
                    fp_label = "footprint" if len(footprint_parts) >= 2 else "wide"
                    desc += f": {' × '.join(footprint_parts)} {fp_label}"
                elif height_part:
                    desc += f": {height_part} tall"

                # Add relative proportion for large furniture
                try:
                    if fw is not None and shorter_wall > 0:
                        pct = float(fw) / shorter_wall * 100
                        if pct >= 20:
                            desc += f" — spans ~{pct:.0f}% of shorter wall"
                except (TypeError, ValueError):
                    pass

            furniture_descs.append(desc)
            if len(furniture_descs) >= _MAX_FURNITURE_ITEMS:
                break
        if furniture_descs:
            sections.append(
                "EXISTING FURNITURE (scale reference — respect these proportions):\n"
                + "\n".join(furniture_descs)
            )

    # --- SURFACES ---
    if dims.surfaces:
        surface_descs = [
            f"- {s.get('type') or 'surface'}: {s.get('material') or 'unknown'}"
            for s in dims.surfaces
            if isinstance(s, dict)
        ]
        if surface_descs:
            sections.append("SURFACES:\n" + "\n".join(surface_descs))

    return "\n\n".join(sections)


_OPTION_VARIANTS: tuple[str, str] = (
    "Design Direction: Lean into the primary style elements from the brief. "
    "Emphasize the dominant mood and color palette. If pain points were mentioned, "
    "prioritize addressing the first one with a clean, polished solution.",
    "Design Direction: Explore a complementary variation. If the brief mentions "
    "multiple styles or pain points, lean toward the secondary elements. Try a "
    "bolder accent color, a different furniture arrangement, or an unexpected "
    "texture contrast — while staying true to the overall aesthetic.",
)


def _format_color_palette(colors: list[str]) -> str:
    """Format colors with 60-30-10 proportional hierarchy for Gemini.

    Research shows proportional color descriptions with application guidance
    produce more cohesive palettes than flat comma-separated lists.
    """
    if len(colors) == 1:
        return f"Color palette: {colors[0]} (dominant throughout)"
    if len(colors) == 2:
        return (
            f"Color palette (70/30): {colors[0]} (70% — walls, large surfaces), "
            f"{colors[1]} (30% — furniture, textiles)"
        )
    # 3+ colors: 60-30-10 rule
    parts = [
        f"Color palette (60/30/10): {colors[0]} (60% — walls, large surfaces), "
        f"{colors[1]} (30% — furniture, textiles), "
        f"{colors[2]} (10% — accent pillows, art, accessories)"
    ]
    if len(colors) > 3:
        extras = ", ".join(colors[3:])
        parts.append(f"Additional accents: {extras}")
    return "\n".join(parts)


def _build_generation_prompt(
    brief: DesignBrief | None,
    inspiration_notes: list[InspirationNote],
    room_dimensions: RoomDimensions | None = None,
    option_variant: str = "",
) -> str:
    """Build the generation prompt from templates and brief data."""
    template = strip_changelog_lines(load_versioned_prompt("generation"))
    preservation = strip_changelog_lines(load_versioned_prompt("room_preservation"))

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
                parts.append(_format_color_palette(sp.colors))
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
    if room_context:
        room_context += "\n"  # Visual separator before option_variant

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
    """Upload a PIL Image to R2 and return the storage key.

    Returns the R2 key (not a presigned URL) so the workflow stores a stable
    reference.  The API layer presigns on every state query, giving iOS
    always-fresh URLs.
    """
    from app.utils.r2 import upload_object

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    key = f"projects/{project_id}/generated/{filename}"
    logger.info("r2_upload_start", key=key, size_bytes=buf.tell())
    upload_object(key, buf.getvalue(), content_type="image/png")
    return key


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

    # Run sync Gemini call in thread pool with timeout to prevent hanging
    async with asyncio.timeout(150):
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
        async with asyncio.timeout(150):
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
    generation_prompts: list[str] | None = None,
    room_context: str = "",
) -> None:
    """Run VLM eval pipeline if EVAL_MODE is set. Never raises — logs and returns."""
    eval_mode = os.environ.get("EVAL_MODE", "off").lower()
    if eval_mode == "off":
        return

    prompt_version = get_active_version("generation")

    for idx, (option_img, gen_url) in enumerate(zip(options, generated_urls, strict=True)):
        try:
            from app.utils.image_eval import run_artifact_check

            artifact = run_artifact_check(option_img)
            if artifact.has_artifacts:
                logger.warning(
                    "eval_artifacts_detected",
                    option=idx,
                    count=artifact.artifact_count,
                )

            result = None
            if brief is not None:
                from app.activities.design_eval import evaluate_generation

                gen_prompt = ""
                if generation_prompts and idx < len(generation_prompts):
                    gen_prompt = generation_prompts[idx]

                from app.utils.r2 import resolve_url

                gen_presigned = await asyncio.to_thread(resolve_url, gen_url)

                result = await evaluate_generation(
                    original_photo_url=original_url,
                    generated_image_url=gen_presigned,
                    brief=brief,
                    generation_prompt=gen_prompt,
                    room_context=room_context,
                    artifact_check={
                        "has_artifacts": artifact.has_artifacts,
                        "artifact_count": artifact.artifact_count,
                    },
                )
                logger.info(
                    "eval_vlm_result",
                    option=idx,
                    total=result.total,
                    tag=result.tag,
                    diagnostics=result.diagnostics,
                    prompt_version=prompt_version,
                )

            # Track scores
            from app.utils.score_tracking import append_score

            append_score(
                history_path=Path("eval_history.jsonl"),
                scenario=f"generation_option_{idx}",
                prompt_version=prompt_version,
                vlm_eval=(
                    {
                        "total": result.total,
                        "tag": result.tag,
                        **{c.name: c.score for c in result.criteria},
                        **result.diagnostics,
                    }
                    if result
                    else {}
                ),
                artifact_check={
                    "has_artifacts": artifact.has_artifacts,
                    "artifact_count": artifact.artifact_count,
                },
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
    from app.utils.tracing import trace_thread

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
        with trace_thread(project_id, "generate"):
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

        # Run eval if enabled — fire-and-forget, never blocks the activity
        room_context = _format_room_context(input.room_dimensions)
        task = asyncio.create_task(
            _maybe_run_eval(
                options=[option_0, option_1],
                original=room_images[0],
                brief=input.design_brief,
                generated_urls=[url_0, url_1],
                original_url=room_urls[0],
                generation_prompts=prompts,
                room_context=room_context,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        return GenerateDesignsOutput(
            options=[
                DesignOption(image_url=url_0, caption="Design Option A"),
                DesignOption(image_url=url_1, caption="Design Option B"),
            ]
        )

    except ApplicationError:
        raise
    except TimeoutError as e:
        raise ApplicationError(
            "Gemini API timed out after 150s",
            non_retryable=False,
        ) from e
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
