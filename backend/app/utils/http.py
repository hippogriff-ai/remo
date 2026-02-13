"""Shared HTTP image download helpers for Temporal activities.

Used by generate.py and edit.py to download room/inspiration photos
from R2 presigned URLs. Validates content-type and image integrity.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from PIL import Image
from temporalio.exceptions import ApplicationError


async def fetch_image(client: httpx.AsyncClient, url: str) -> Image.Image:
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
        # 429 is retryable (throttling); other 4xx are non-retryable client errors
        is_non_retryable = response.status_code < 500 and response.status_code != 429
        raise ApplicationError(
            f"HTTP {response.status_code} downloading image: {url[:100]}",
            non_retryable=is_non_retryable,
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


async def download_image(url: str) -> Image.Image:
    """Download an image from a URL."""
    import httpx

    async with httpx.AsyncClient() as client:
        return await fetch_image(client, url)


async def download_images(urls: list[str]) -> list[Image.Image]:
    """Download multiple images concurrently with a shared HTTP client."""
    if not urls:
        return []
    import httpx

    async with httpx.AsyncClient() as client:
        tasks = [fetch_image(client, url) for url in urls]
        return await asyncio.gather(*tasks)
