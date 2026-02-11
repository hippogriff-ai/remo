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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

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
)
from app.utils.gemini_chat import (
    GEMINI_MODEL,
    IMAGE_CONFIG,
    extract_image,
    extract_text,
    get_client,
)

logger = structlog.get_logger()

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

# NOTE: Download helpers (_fetch_image, _download_image, _download_images)
# are duplicated in edit.py — extract to shared utility in P2.


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


def _build_generation_prompt(
    brief: DesignBrief | None,
    inspiration_notes: list[InspirationNote],
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

    # Escape curly braces in user-provided text to prevent str.format() KeyError
    return template.format(
        brief=brief_text.replace("{", "{{").replace("}", "}}"),
        keep_items=keep_items_text.replace("{", "{{").replace("}", "}}"),
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


async def _fetch_image(client: httpx.AsyncClient, url: str) -> Image.Image:
    """Fetch and validate a single image using the given HTTP client."""
    import httpx

    try:
        response = await client.get(url, timeout=30)
    except httpx.TimeoutException as exc:
        raise ApplicationError(
            f"Timeout downloading image: {url[:100]}",
            non_retryable=False,
        ) from exc
    except httpx.RequestError as exc:
        raise ApplicationError(
            f"Network error downloading image: {url[:100]}: {type(exc).__name__}",
            non_retryable=False,
        ) from exc

    if response.status_code >= 400:
        is_client_error = response.status_code < 500
        raise ApplicationError(
            f"HTTP {response.status_code} downloading image: {url[:100]}",
            non_retryable=is_client_error,
        )

    content_type = response.headers.get("content-type", "")
    if content_type and not content_type.startswith("image/"):
        raise ApplicationError(
            f"Expected image content-type, got: {content_type}",
            non_retryable=True,
        )

    try:
        img = Image.open(io.BytesIO(response.content))
        img.load()  # Force full decode to catch truncation
    except Exception as exc:
        raise ApplicationError(
            f"Downloaded image is corrupt: {url[:100]}",
            non_retryable=True,
        ) from exc
    return img


async def _download_image(url: str) -> Image.Image:
    """Download an image from a URL."""
    import httpx

    async with httpx.AsyncClient() as client:
        return await _fetch_image(client, url)


async def _download_images(urls: list[str]) -> list[Image.Image]:
    """Download multiple images concurrently with a shared HTTP client."""
    if not urls:
        return []
    import httpx

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_image(client, url) for url in urls]
        return await asyncio.gather(*tasks)


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
) -> Image.Image:
    """Generate a single design option via standalone Gemini call."""

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

    return result_image


@activity.defn
async def generate_designs(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    """Generate 2 design options from room photos and design brief."""
    activity.logger.info(
        "generate_designs_start",
        num_room_photos=len(input.room_photo_urls),
        num_inspiration_photos=len(input.inspiration_photo_urls),
    )

    # Extract project_id from R2 URL path pattern: projects/{id}/...
    project_id = _extract_project_id(input.room_photo_urls)

    try:
        # Download source images
        room_images, inspiration_images = await asyncio.gather(
            _download_images(input.room_photo_urls),
            _download_images(input.inspiration_photo_urls),
        )

        if not room_images:
            raise ApplicationError(
                "No room photos provided",
                non_retryable=True,
            )

        # Build prompt
        prompt = _build_generation_prompt(input.design_brief, input.inspiration_notes)

        # Generate 2 options in parallel
        option_0, option_1 = await asyncio.gather(
            _generate_single_option(prompt, room_images, inspiration_images, 0),
            _generate_single_option(prompt, room_images, inspiration_images, 1),
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
