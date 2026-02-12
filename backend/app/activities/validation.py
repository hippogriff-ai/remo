"""Photo validation — blur, resolution, and content classification.

Runs synchronously in the FastAPI photo upload handler (not a Temporal activity)
because it's fast (<3s) and needs immediate user feedback.

Three checks:
1. Resolution: min 1024px shortest side (Pillow) — <10ms
2. Blur: Laplacian variance on normalized 1024px image, threshold 60 — <50ms
3. Content: Claude Haiku 4.5 image classification — ~1-2s
"""

from __future__ import annotations

import base64
import io

import anthropic
import structlog
from PIL import Image, ImageFilter

from app.config import settings
from app.models.contracts import ValidatePhotoInput, ValidatePhotoOutput

logger = structlog.get_logger()

MIN_RESOLUTION = 1024
BLUR_THRESHOLD = 60.0
NORMALIZE_SIZE = 1024


def validate_photo(input: ValidatePhotoInput) -> ValidatePhotoOutput:
    """Run all validation checks on an uploaded photo."""
    failures: list[str] = []
    messages: list[str] = []

    try:
        img = Image.open(io.BytesIO(input.image_data))
        img.load()  # Force full decode to catch truncated/decompression bombs early
    except (OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        logger.warning("photo_validation_image_open_failed", error=str(exc))
        return ValidatePhotoOutput(
            passed=False,
            failures=["invalid_image"],
            messages=["Could not open image. Please upload a valid JPEG or PNG."],
        )

    # Check 1: Resolution
    res_ok, res_msg = _check_resolution(img)
    if not res_ok:
        failures.append("low_resolution")
        messages.append(res_msg)

    # Check 2: Blur
    blur_ok, blur_msg = _check_blur(img)
    if not blur_ok:
        failures.append("blurry")
        messages.append(blur_msg)

    # Check 3: Content classification (only if basic checks pass)
    if not failures:
        if settings.anthropic_api_key:
            content_ok, content_msg = _check_content(input.image_data, input.photo_type)
            if not content_ok:
                failures.append("content_rejected")
                messages.append(content_msg)
        else:
            logger.warning(
                "photo_validation_content_check_skipped",
                reason="anthropic_api_key not configured",
            )

    passed = len(failures) == 0
    if passed:
        messages.append("Photo looks great!")

    logger.info(
        "photo_validation",
        photo_type=input.photo_type,
        passed=passed,
        failures=failures,
    )
    return ValidatePhotoOutput(passed=passed, failures=failures, messages=messages)


def _check_resolution(img: Image.Image) -> tuple[bool, str]:
    """Check that the shortest side is at least MIN_RESOLUTION pixels."""
    shortest = min(img.size)
    if shortest < MIN_RESOLUTION:
        return (
            False,
            f"Image is too small ({shortest}px). Minimum {MIN_RESOLUTION}px on shortest side.",
        )
    return True, ""


def _check_blur(img: Image.Image) -> tuple[bool, str]:
    """Detect blur via Laplacian variance on a normalized grayscale image."""
    gray = img.convert("L")
    # Normalize to NORMALIZE_SIZE on shortest side for consistent measurement
    w, h = gray.size
    shortest = min(w, h)
    if shortest > NORMALIZE_SIZE:
        scale = NORMALIZE_SIZE / shortest
        gray = gray.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    # Cap longest side to prevent memory exhaustion on extreme aspect ratios
    # (e.g. 1024x50000 bypasses shortest-side normalization)
    w, h = gray.size
    if max(w, h) > NORMALIZE_SIZE * 4:
        scale = (NORMALIZE_SIZE * 4) / max(w, h)
        gray = gray.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    # Laplacian filter (edge detection) — low variance = blurry
    laplacian = gray.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1))
    pixels = list(laplacian.getdata())
    if not pixels:
        return False, "Could not analyze image for blur."

    mean = sum(pixels) / len(pixels)
    variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)

    if variance < BLUR_THRESHOLD:
        return (
            False,
            f"Image appears blurry (score: {variance:.0f}, minimum: {BLUR_THRESHOLD:.0f}).",
        )
    return True, ""


_anthropic_client: anthropic.Anthropic | None = None


def _get_anthropic_client() -> anthropic.Anthropic:
    """Lazy singleton for Anthropic client — reuses connection pool across calls."""
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _anthropic_client


def _detect_media_type(image_data: bytes) -> str:
    """Detect image media type from bytes (JPEG, PNG, etc.)."""
    img = Image.open(io.BytesIO(image_data))
    fmt = (img.format or "JPEG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    return f"image/{fmt.lower()}"


def _check_content(image_data: bytes, photo_type: str) -> tuple[bool, str]:
    """Classify image content using Claude Haiku 4.5."""
    client = _get_anthropic_client()
    b64 = base64.b64encode(image_data).decode()

    if photo_type == "room":
        prompt = (
            "Is this a photo of an interior room (living room, bedroom, kitchen, bathroom, "
            "dining room, office, etc.)? Reply with exactly YES or NO, then a brief reason."
        )
    else:
        prompt = (
            "Is this a photo that could serve as design inspiration (interior design, furniture, "
            "decor, architecture, textures, etc.)? Photos of people or animals are not acceptable "
            "as inspiration. Reply with exactly YES or NO, then a brief reason."
        )

    try:
        media_type = _detect_media_type(image_data)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        if not response.content or not hasattr(response.content[0], "text"):
            logger.error(
                "photo_validation_unexpected_response",
                photo_type=photo_type,
                content_length=len(response.content) if response.content else 0,
            )
            return True, ""  # fail open for P1

        answer = response.content[0].text.strip().upper()
        if answer.startswith("NO"):
            if photo_type == "inspiration":
                return False, (
                    "Inspiration photos should show spaces, furniture, or design details "
                    "— not people or animals. Please choose a different image."
                )
            reason = response.content[0].text.strip()
            return False, f"This doesn't look like a valid {photo_type} photo. {reason}"
        return True, ""
    except anthropic.APIError as exc:
        logger.error(
            "photo_validation_content_check_failed",
            photo_type=photo_type,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        # Fail open for P1 — content check is best-effort
        return True, ""
