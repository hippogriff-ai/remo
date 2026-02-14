"""Shopping list pipeline — extracts items, searches, scores, and filters.

5-step pipeline:
1. Anchored item extraction (Claude vision)
2. Product search (Exa API, parallelized)
3. Rubric-based scoring (Claude, parallelized)
4. Dimension filtering (if LiDAR)
5. Confidence filtering + Google Shopping fallback

Stateless Temporal activity: all inputs passed in, output returned.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any

import anthropic
import httpx
import structlog
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.contracts import (
    DesignBrief,
    GenerateShoppingListInput,
    GenerateShoppingListOutput,
    ProductMatch,
    RevisionRecord,
    RoomContext,
    RoomDimensions,
    UnmatchedItem,
)

log = structlog.get_logger("shopping")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

MODEL = "claude-opus-4-6"
SCORING_MODEL = "claude-sonnet-4-5-20250929"
MAX_TOKENS = 4096
EXA_BASE_URL = "https://api.exa.ai"
MAX_CONCURRENT_SCORES = 5


def _strip_code_fence(text: str) -> str:
    """Remove markdown code fences from Claude responses.

    Handles both multiline (```json\\n...\\n```) and single-line (```{...}```) formats.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    # Remove opening fence + optional language tag
    text = text.removeprefix("```")
    for lang in ("json", "JSON"):
        text = text.removeprefix(lang)
    text = text.lstrip("\n")
    # Remove closing fence
    text = text.rsplit("```", 1)[0].strip()
    return text


def _extract_json(text: str) -> dict[str, Any]:
    """Extract and parse JSON from Claude's free-form text response.

    Handles three patterns Claude may produce:
    1. Pure JSON: '{"items": [...]}'
    2. Code-fenced JSON: '```json\\n{"items": [...]}\\n```'
    3. JSON with preamble/postamble: 'Here is the result:\\n{"items": [...]}'

    Returns parsed dict, or empty dict on failure.
    """
    text = _strip_code_fence(text.strip())
    if not text:
        return {}

    # Fast path: already clean JSON
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Slow path: find the outermost JSON object in the text
    start = text.find("{")
    if start == -1:
        return {}

    # Walk forward to find the matching closing brace
    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape_next:
            escape_next = False
            continue
        if in_string:
            if ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])  # type: ignore[no-any-return]
                except json.JSONDecodeError:
                    return {}

    return {}


# === Room Constraints (shared by extraction, search, scoring, filtering) ===


def _compute_room_constraints(
    room_dimensions: RoomDimensions,
) -> dict[str, dict[str, str]]:
    """Compute per-category max sizes from room measurements.

    Uses standard interior design proportions to derive furniture size limits
    from LiDAR-measured (or photo-estimated) room dimensions.
    Returns empty dict if dimensions are zero or negative.
    """
    width = room_dimensions.width_m
    length = room_dimensions.length_m
    height = room_dimensions.height_m

    if width <= 0 or length <= 0 or height <= 0:
        log.warning(
            "invalid_room_dimensions",
            width=width,
            length=length,
            height=height,
        )
        return {}

    longer_cm = max(width, length) * 100
    shorter_cm = min(width, length) * 100
    h_cm = height * 100

    # Sofa: max ~75% of longer usable wall (minus 1.2m traffic clearance)
    usable_wall_cm = longer_cm - 120
    sofa_max_cm = max(0, usable_wall_cm * 0.75)

    # Coffee table: ~2/3 of max sofa width
    coffee_max_cm = sofa_max_cm * 0.67

    # Rug: ~80% of shorter wall × ~70% of longer wall
    rug_w_cm = shorter_cm * 0.80
    rug_l_cm = longer_cm * 0.70

    # Dining table: room minus 1.8m clearance (90cm per side for chairs)
    dining_l_cm = max(0, longer_cm - 180)

    # Lighting: floor lamp = ceiling height - 30cm
    lamp_max_cm = max(0, h_cm - 30)

    return {
        "sofa": {"max_width_cm": f"{sofa_max_cm:.0f}", "inches": f"{sofa_max_cm / 2.54:.0f}"},
        "coffee_table": {
            "max_width_cm": f"{coffee_max_cm:.0f}",
            "inches": f"{coffee_max_cm / 2.54:.0f}",
        },
        "rug": {
            "width_cm": f"{rug_w_cm:.0f}",
            "length_cm": f"{rug_l_cm:.0f}",
            "inches": f"{rug_w_cm / 2.54:.0f}x{rug_l_cm / 2.54:.0f}",
        },
        "dining_table": {
            "max_length_cm": f"{dining_l_cm:.0f}",
            "inches": f"{dining_l_cm / 2.54:.0f}",
        },
        "floor_lamp": {
            "max_height_cm": f"{lamp_max_cm:.0f}",
            "inches": f"{lamp_max_cm / 2.54:.0f}",
        },
    }


def _format_room_constraints_for_prompt(
    room_context: RoomContext | None,
    room_dimensions: RoomDimensions | None,
) -> str:
    """Format room constraints for prompt injection.

    Uses LiDAR dimensions when available, falls back to photo-estimated dimensions.
    Includes furniture observations from photo analysis if present.
    """
    if room_dimensions is None and (room_context is None or room_context.room_dimensions is None):
        # Try photo-estimated dimensions as last resort
        if (
            room_context
            and room_context.photo_analysis
            and room_context.photo_analysis.estimated_dimensions
        ):
            return (
                f"Estimated room size: {room_context.photo_analysis.estimated_dimensions} "
                "(from photo analysis — approximate, use for general sizing only)"
            )
        return "No room dimensions available."

    # Use LiDAR dims preferentially, then context dims
    dims = room_dimensions or (room_context.room_dimensions if room_context else None)
    if dims is None:
        return "No room dimensions available."

    constraints = _compute_room_constraints(dims)
    if room_dimensions or (room_context and "lidar" in (room_context.enrichment_sources or [])):
        source = "LiDAR scan"
    else:
        source = "photo analysis"

    lines = [
        f"Room: {dims.width_m:.1f}m x {dims.length_m:.1f}m, "
        f"ceiling {dims.height_m:.1f}m ({source})",
    ]

    if constraints:
        lines.append("")
        lines.append("Per-category size limits:")
        if "sofa" in constraints:
            lines.append(f'- Sofa: max ~{constraints["sofa"]["inches"]}" wide')
        if "coffee_table" in constraints:
            lines.append(f'- Coffee table: max ~{constraints["coffee_table"]["inches"]}" wide')
        if "rug" in constraints:
            lines.append(f'- Rug: ~{constraints["rug"]["inches"]}" area')
        if "dining_table" in constraints:
            lines.append(f'- Dining table: max ~{constraints["dining_table"]["inches"]}" long')
        if "floor_lamp" in constraints:
            lines.append(f'- Floor lamp: max ~{constraints["floor_lamp"]["inches"]}" tall')

    # Include furniture observations from photo analysis
    if room_context and room_context.photo_analysis:
        analysis = room_context.photo_analysis
        if analysis.furniture:
            furniture_notes = []
            for f in analysis.furniture:
                desc = f.item
                if f.condition:
                    desc += f" ({f.condition})"
                if f.keep_candidate:
                    desc += " [keep]"
                furniture_notes.append(desc)
            if furniture_notes:
                lines.append("")
                lines.append("Detected furniture: " + "; ".join(furniture_notes))

    return "\n".join(lines)


# === Step 1: Item Extraction ===

_extraction_prompt_cache: str | None = None


def _load_extraction_prompt(
    design_brief: DesignBrief | None,
    revision_history: list[RevisionRecord],
    room_context: RoomContext | None = None,
    room_dimensions: RoomDimensions | None = None,
) -> str:
    """Load and fill the item extraction prompt template (template cached)."""
    global _extraction_prompt_cache  # noqa: PLW0603
    if _extraction_prompt_cache is None:
        _extraction_prompt_cache = (PROMPTS_DIR / "item_extraction.txt").read_text()
    template = _extraction_prompt_cache

    brief_text = design_brief.model_dump_json(indent=2) if design_brief else "None"
    keep_items = design_brief.keep_items if design_brief else []

    iterations_text = "None"
    if revision_history:
        iterations = []
        for rev in revision_history:
            iterations.append(
                f"Revision {rev.revision_number} ({rev.type}): {', '.join(rev.instructions)}"
            )
        iterations_text = "\n".join(iterations)

    room_constraints = _format_room_constraints_for_prompt(room_context, room_dimensions)

    return template.format(
        design_brief=brief_text,
        iteration_history=iterations_text,
        keep_items=json.dumps(keep_items),
        room_constraints=room_constraints,
    )


def _build_extraction_messages(
    design_image_url: str,
    original_room_photo_urls: list[str],
    prompt_text: str,
) -> list[dict[str, Any]]:
    """Build messages with image inputs for extraction."""
    content: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {"type": "url", "url": design_image_url},
        },
    ]
    for url in original_room_photo_urls:
        content.append(
            {
                "type": "image",
                "source": {"type": "url", "url": url},
            }
        )
    content.append({"type": "text", "text": prompt_text})
    return [{"role": "user", "content": content}]


async def extract_items(
    client: anthropic.AsyncAnthropic,
    design_image_url: str,
    original_room_photo_urls: list[str],
    design_brief: DesignBrief | None,
    revision_history: list[RevisionRecord],
    *,
    source_urls: list[str] | None = None,
    room_context: RoomContext | None = None,
    room_dimensions: RoomDimensions | None = None,
) -> list[dict[str, Any]]:
    """Step 1: Extract purchasable items from the design image."""
    from app.utils.llm_cache import get_cached, set_cached

    prompt_text = _load_extraction_prompt(
        design_brief, revision_history, room_context, room_dimensions
    )

    # Dev/test cache: use stable R2 keys (not presigned URLs) to prevent
    # cache misses when signatures rotate. Will be removed in production.
    stable_urls = source_urls or [design_image_url, *original_room_photo_urls]
    cache_key = [prompt_text, *stable_urls]
    cached = get_cached("claude_extraction", cache_key)
    if cached and isinstance(cached, list):
        return cached  # type: ignore[no-any-return]

    messages = _build_extraction_messages(design_image_url, original_room_photo_urls, prompt_text)

    response = await client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=messages,  # type: ignore[arg-type]
    )

    log.info(
        "shopping_extraction_tokens",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=MODEL,
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    data = _extract_json(text)
    raw_items: list[dict[str, Any]] = data.get("items") or []
    result = _validate_extracted_items(raw_items)

    # Save to dev/test cache
    set_cached("claude_extraction", cache_key, result)
    return result


_REQUIRED_ITEM_FIELDS = {"category", "description"}
_VALID_SOURCE_TAGS = {"BRIEF_ANCHORED", "ITERATION_ANCHORED", "IMAGE_ONLY"}
_VALID_PRIORITIES = {"HIGH", "MEDIUM", "LOW"}


def _validate_extracted_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and normalize extracted items, dropping malformed entries.

    Required fields: category, description (must be non-empty strings).
    Normalizes source_tag and search_priority to valid values.
    """
    valid: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            log.warning(
                "shopping_item_dropped",
                reason="non-dict entry",
                item_type=type(item).__name__,
            )
            continue

        # Required fields must be non-empty strings
        if not all(isinstance(item.get(f), str) and item[f].strip() for f in _REQUIRED_ITEM_FIELDS):
            log.warning(
                "shopping_item_dropped",
                reason="missing required fields",
                item_keys=list(item.keys()),
            )
            continue

        # Normalize source_tag
        if item.get("source_tag") not in _VALID_SOURCE_TAGS:
            item["source_tag"] = "IMAGE_ONLY"

        # Normalize search_priority
        if item.get("search_priority") not in _VALID_PRIORITIES:
            item["search_priority"] = "MEDIUM"

        valid.append(item)

    if len(valid) < len(items):
        log.info(
            "shopping_items_validated",
            raw=len(items),
            valid=len(valid),
            dropped=len(items) - len(valid),
        )

    return valid


# === Step 2: Exa Search ===


_PRIORITY_NUM_RESULTS = {"HIGH": 5, "MEDIUM": 3, "LOW": 2}


def _num_results_for_item(item: dict[str, Any]) -> int:
    """Return Exa num_results based on item's search_priority tag."""
    return _PRIORITY_NUM_RESULTS.get(item.get("search_priority", "MEDIUM"), 3)


def _room_size_label(room_dimensions: RoomDimensions) -> str:
    """Classify room as small (<15 sqm), medium (15-25 sqm), or large (>25 sqm)."""
    area_sqm = room_dimensions.width_m * room_dimensions.length_m
    if area_sqm < 15:
        return "small"
    elif area_sqm <= 25:
        return "medium"
    return "large"


def _build_search_queries(
    item: dict[str, Any],
    room_dimensions: RoomDimensions | None = None,
) -> list[str]:
    """Build source-aware search queries for an extracted item.

    Queries include "buy" to steer Exa toward product pages (not blogs/reviews).
    Source tag determines query strategy:
    - BRIEF_ANCHORED: user's own language (highest recall)
    - ITERATION_ANCHORED: iteration instruction keywords
    - IMAGE_ONLY: AI-described category + attributes

    All tags get a description-based query when available, since the
    extraction prompt produces rich professional descriptions.

    When room_dimensions are available, adds a size-constrained query for
    primary furniture categories (sofa, table, rug, cabinet).
    """
    tag = item.get("source_tag", "IMAGE_ONLY")
    category = item.get("category", "")
    style = item.get("style", "")
    material = item.get("material", "")
    color = item.get("color", "")
    description = item.get("description", "")
    dims = item.get("estimated_dimensions", "")

    queries = []
    if tag == "BRIEF_ANCHORED":
        ref = item.get("source_reference", description)
        queries.append(f"buy {ref}")
        queries.append(f"{category} {style} {material} shop")
    elif tag == "ITERATION_ANCHORED":
        ref = item.get("source_reference", description)
        queries.append(f"buy {ref}")
        queries.append(f"{category} {material} {color} shop")
    else:
        queries.append(f"buy {category} {material} {color}")
        queries.append(f"{category} {style} shop")

    # Description-based query for all tags — the extraction prompt produces
    # rich descriptions like "ivory boucle sofa with down-blend cushions"
    if description and description != item.get("source_reference", ""):
        queries.append(f"buy {description}")

    if dims:
        queries.append(f"{category} {dims}")

    # Room-aware constrained query for primary furniture
    if room_dimensions is not None:
        constraint_key = _match_category(item)
        if constraint_key:
            constraints = _compute_room_constraints(room_dimensions)
            cat_constraint = constraints.get(constraint_key)
            if cat_constraint:
                max_inches = cat_constraint.get("inches", "")
                size_label = _room_size_label(room_dimensions)
                if max_inches:
                    queries.append(
                        f"{category} {material} under {max_inches} inches {size_label} room"
                    )

    return [q.strip() for q in queries if q.strip()]


EXA_MAX_RETRIES = 1
EXA_RETRY_DELAY = 1.0

# File-based Exa cache: set EXA_CACHE_DIR to a directory path to enable.
# First call with a query → real Exa API call, result saved to cache.
# Subsequent calls with the same query → loaded from cache, no API call.
_EXA_CACHE_DIR: str | None = os.environ.get("EXA_CACHE_DIR")


def _exa_cache_path(query: str, num_results: int) -> Path | None:
    """Return cache file path for a query, or None if caching is disabled."""
    if not _EXA_CACHE_DIR:
        return None
    import hashlib

    key = hashlib.sha256(f"{query}:{num_results}".encode()).hexdigest()[:16]
    cache_dir = Path(_EXA_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"exa_{key}.json"


async def _search_exa(
    http_client: httpx.AsyncClient,
    query: str,
    api_key: str,
    num_results: int = 3,
) -> list[dict[str, Any]]:
    """Execute a single Exa neural search query with content retrieval.

    Requests page text (truncated to 1000 chars) so the scoring model
    can compare actual product descriptions rather than guessing from titles.
    Retries once on transient failures (429, 500+) with a short backoff.

    When EXA_CACHE_DIR is set, results are cached to disk per query and
    reused on subsequent calls to avoid redundant API costs.
    """
    # Check cache first
    cache_file = _exa_cache_path(query, num_results)
    if cache_file and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text())
            log.info("exa_cache_hit", query=query[:80])
            return cached  # type: ignore[no-any-return]
        except (json.JSONDecodeError, OSError):
            pass  # Cache corrupt — fall through to real call

    payload = {
        "query": query,
        "type": "neural",
        "numResults": num_results,
        "useAutoprompt": True,
        "contents": {
            "text": {"maxCharacters": 1000},
        },
    }
    headers = {"x-api-key": api_key}

    for attempt in range(1 + EXA_MAX_RETRIES):
        try:
            resp = await http_client.post(
                f"{EXA_BASE_URL}/search",
                headers=headers,
                json=payload,
                timeout=15.0,
            )
        except httpx.TimeoutException:
            if attempt < EXA_MAX_RETRIES:
                log.warning("exa_search_timeout", query=query[:80], attempt=attempt + 1)
                await asyncio.sleep(EXA_RETRY_DELAY)
                continue
            log.warning("exa_search_timeout_final", query=query[:80])
            return []

        if resp.status_code == 200:
            data = resp.json()
            results: list[dict[str, Any]] = data.get("results", [])
            # Save to cache if enabled
            if cache_file:
                try:
                    cache_file.write_text(json.dumps(results))
                    log.info("exa_cache_saved", query=query[:80])
                except OSError:
                    pass
            return results

        # Retry on transient errors (429 rate limit, 500+ server errors)
        if resp.status_code in (429, 500, 502, 503) and attempt < EXA_MAX_RETRIES:
            log.warning(
                "exa_search_retrying",
                status=resp.status_code,
                query=query[:80],
                attempt=attempt + 1,
            )
            await asyncio.sleep(EXA_RETRY_DELAY)
            continue

        # Non-retryable failure (400, 401, 403, etc.)
        log.warning("exa_search_failed", status=resp.status_code, query=query[:80])
        return []

    return []


async def search_products_for_item(
    http_client: httpx.AsyncClient,
    item: dict[str, Any],
    exa_api_key: str,
    room_dimensions: RoomDimensions | None = None,
) -> list[dict[str, Any]]:
    """Search Exa for products matching a single extracted item."""
    queries = _build_search_queries(item, room_dimensions=room_dimensions)
    num_results = _num_results_for_item(item)
    tasks = [_search_exa(http_client, q, exa_api_key, num_results) for q in queries]
    all_results = await asyncio.gather(*tasks, return_exceptions=True)

    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for result_set in all_results:
        if isinstance(result_set, BaseException):
            continue
        for r in result_set:  # type: ignore[union-attr]
            url = r.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                deduped.append(r)
    return deduped


async def search_all_items(
    items: list[dict[str, Any]],
    exa_api_key: str,
    room_dimensions: RoomDimensions | None = None,
) -> list[list[dict[str, Any]]]:
    """Step 2: Search Exa for products for all items (parallelized)."""
    async with httpx.AsyncClient() as http_client:
        tasks = [
            search_products_for_item(
                http_client, item, exa_api_key, room_dimensions=room_dimensions
            )
            for item in items
        ]
        return await asyncio.gather(*tasks)


# === Step 3: Rubric-Based Scoring ===


_PRICE_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")


def _extract_price_text(product: dict[str, Any]) -> str:
    """Extract a price string from Exa product data.

    Checks the 'text' content field for dollar amounts (e.g., '$1,299.00').
    Returns the first match or 'Unknown'.
    """
    text = product.get("text", "")
    match = _PRICE_RE.search(text)
    return match.group(0) if match else "Unknown"


def _price_to_cents(price_text: str) -> int:
    """Convert a price string like '$1,299.00' to cents (129900).

    Returns 0 if the price can't be parsed.
    """
    if price_text == "Unknown" or not price_text.startswith("$"):
        return 0
    try:
        cleaned = price_text.replace("$", "").replace(",", "")
        return int(float(cleaned) * 100)
    except (ValueError, OverflowError):
        return 0


SCORING_WEIGHTS_DEFAULT = {
    "category": 0.30,
    "material": 0.20,
    "color": 0.20,
    "style": 0.20,
    "dimensions": 0.10,
}
SCORING_WEIGHTS_LIDAR = {
    "category": 0.25,
    "material": 0.20,
    "color": 0.18,
    "style": 0.17,
    "dimensions": 0.20,
}

_scoring_prompt_cache: str | None = None


def _load_scoring_prompt() -> str:
    """Load the product scoring prompt template (cached after first read)."""
    global _scoring_prompt_cache  # noqa: PLW0603
    if _scoring_prompt_cache is None:
        _scoring_prompt_cache = (PROMPTS_DIR / "product_scoring.txt").read_text()
    return _scoring_prompt_cache


def _build_scoring_prompt(
    item: dict[str, Any],
    product: dict[str, Any],
    design_brief: DesignBrief | None,
    room_dimensions: RoomDimensions | None = None,
) -> str:
    """Fill scoring prompt template for a specific item-product pair."""
    template = _load_scoring_prompt()

    has_valid_dims = (
        room_dimensions is not None
        and room_dimensions.width_m > 0
        and room_dimensions.length_m > 0
        and room_dimensions.height_m > 0
    )
    weights = SCORING_WEIGHTS_LIDAR if has_valid_dims else SCORING_WEIGHTS_DEFAULT

    style_mood = ""
    color_palette = ""
    room_type = ""
    if design_brief:
        room_type = design_brief.room_type
        if design_brief.style_profile:
            style_mood = design_brief.style_profile.mood or ""
            color_palette = ", ".join(design_brief.style_profile.colors)

    room_dims_section = ""
    if room_dimensions:
        room_dims_section = (
            f"\n## Room Dimensions (LiDAR-measured)\n"
            f"- Width: {room_dimensions.width_m:.1f}m "
            f'({room_dimensions.width_m / 0.0254:.0f}")\n'
            f"- Length: {room_dimensions.length_m:.1f}m "
            f'({room_dimensions.length_m / 0.0254:.0f}")\n'
            f"- Height: {room_dimensions.height_m:.1f}m "
            f'({room_dimensions.height_m / 0.0254:.0f}")\n'
        )

    return template.format(
        item_category=item.get("category", ""),
        item_description=item.get("description", ""),
        item_style=item.get("style", ""),
        item_material=item.get("material", ""),
        item_color=item.get("color", ""),
        item_dimensions=item.get("estimated_dimensions", "unknown"),
        room_type=room_type,
        style_mood=style_mood,
        color_palette=color_palette,
        product_name=product.get("title", "Unknown"),
        product_description=product.get("text", product.get("title", "")),
        product_price=_extract_price_text(product),
        product_url=product.get("url", ""),
        w_category=weights["category"],
        w_material=weights["material"],
        w_color=weights["color"],
        w_style=weights["style"],
        w_dimensions=weights["dimensions"],
        room_dimensions_section=room_dims_section,
        weighted_total_formula=(
            f"cat*{weights['category']} + mat*{weights['material']} + "
            f"col*{weights['color']} + sty*{weights['style']} + "
            f"dim*{weights['dimensions']}"
        ),
    )


async def score_product(
    client: anthropic.AsyncAnthropic,
    item: dict[str, Any],
    product: dict[str, Any],
    design_brief: DesignBrief | None,
    room_dimensions: RoomDimensions | None = None,
) -> dict[str, Any]:
    """Score a single product against an item using the rubric."""
    from app.utils.llm_cache import get_cached, set_cached

    prompt = _build_scoring_prompt(item, product, design_brief, room_dimensions=room_dimensions)

    # Dev/test cache: avoid redundant Claude scoring calls when prompt
    # hasn't changed. Will be removed in production.
    cache_key = [prompt]
    cached = get_cached("claude_scoring", cache_key)
    if cached and isinstance(cached, dict):
        # Restore product metadata (not included in scored output)
        cached["product_url"] = product.get("url", "")
        cached["product_name"] = product.get("title", "Unknown")
        cached["image_url"] = product.get("image")
        cached["dimensions"] = product.get("text", "")
        if "price_cents" not in cached:
            cached["price_cents"] = _price_to_cents(_extract_price_text(product))
        return cached  # type: ignore[no-any-return]

    # Dev/test cache: avoid redundant Claude scoring calls when prompt
    # hasn't changed. Will be removed in production.
    cache_key = [prompt]
    cached = get_cached("claude_scoring", cache_key)
    if cached and isinstance(cached, dict):
        # Restore product metadata (not included in scored output)
        cached["product_url"] = product.get("url", "")
        cached["product_name"] = product.get("title", "Unknown")
        cached["image_url"] = product.get("image")
        if "price_cents" not in cached:
            cached["price_cents"] = _price_to_cents(_extract_price_text(product))
        return cached  # type: ignore[no-any-return]

    response = await client.messages.create(
        model=SCORING_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = ""
    for block in response.content:
        if hasattr(block, "text"):
            text += block.text

    scores = _extract_json(text)
    if not scores:
        scores = {"weighted_total": 0.0, "why_matched": "Scoring failed"}

    # Save to dev/test cache (core scores only, not product metadata)
    set_cached("claude_scoring", cache_key, scores)

    scores["product_url"] = product.get("url", "")
    scores["product_name"] = product.get("title", "Unknown")
    scores["image_url"] = product.get("image")
    # Carry product text through for downstream dimension parsing
    scores["dimensions"] = product.get("text", "")
    scores["_input_tokens"] = response.usage.input_tokens
    scores["_output_tokens"] = response.usage.output_tokens
    # Extract price from Exa content for cost estimation
    if "price_cents" not in scores:
        scores["price_cents"] = _price_to_cents(_extract_price_text(product))
    return scores


async def score_all_products(
    client: anthropic.AsyncAnthropic,
    items: list[dict[str, Any]],
    search_results: list[list[dict[str, Any]]],
    design_brief: DesignBrief | None,
    room_dimensions: RoomDimensions | None = None,
) -> list[list[dict[str, Any]]]:
    """Step 3: Score all products for all items (parallelized with semaphore).

    Flattens all item×product pairs into a single batch, runs them concurrently
    (limited by MAX_CONCURRENT_SCORES to avoid rate limits), then reassembles
    results per item and sorts by score.

    Individual scoring failures are tolerated — a single rate-limited or errored
    call won't crash the pipeline. Failed scores are logged and skipped.
    """
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCORES)

    async def _score_limited(item: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await score_product(
                client, item, product, design_brief, room_dimensions=room_dimensions
            )

    # Flatten all item-product pairs, tracking which item each belongs to
    tasks: list[asyncio.Task[dict[str, Any]]] = []
    item_indices: list[int] = []
    for idx, (item, products) in enumerate(zip(items, search_results, strict=True)):
        for product in products:
            tasks.append(asyncio.ensure_future(_score_limited(item, product)))
            item_indices.append(idx)

    total_tasks = len(tasks)
    if total_tasks > 0:
        log.info(
            "shopping_scoring_parallel",
            total_tasks=total_tasks,
            concurrency=MAX_CONCURRENT_SCORES,
        )

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Separate successes from failures — a single failed score shouldn't
    # crash the entire pipeline (the remaining scores are still valuable).
    results: list[tuple[int, dict[str, Any]]] = []
    num_failed = 0
    for idx, result in zip(item_indices, raw_results, strict=True):
        if isinstance(result, BaseException):
            num_failed += 1
            log.warning(
                "shopping_score_failed",
                item_index=idx,
                error=str(result)[:200],
            )
            continue
        results.append((idx, result))

    if num_failed > 0:
        log.info(
            "shopping_scoring_failures",
            failed=num_failed,
            succeeded=len(results),
            total=total_tasks,
        )

    # Aggregate token usage across successful scoring calls
    total_input = sum(r.get("_input_tokens", 0) for _, r in results)
    total_output = sum(r.get("_output_tokens", 0) for _, r in results)
    if total_tasks > 0:
        log.info(
            "shopping_scoring_tokens",
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            num_scores=len(results),
            model=SCORING_MODEL,
        )

    # Reassemble into per-item lists, stripping internal token fields
    all_scores: list[list[dict[str, Any]]] = [[] for _ in items]
    for idx, scored in results:
        scored.pop("_input_tokens", None)
        scored.pop("_output_tokens", None)
        all_scores[idx].append(scored)

    # Sort each item's products by score (best first)
    for item_scores in all_scores:
        item_scores.sort(
            key=lambda s: float(s.get("weighted_total", 0) or 0),
            reverse=True,
        )

    return all_scores


# === Step 4: Dimension Filtering ===

_DIMS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)"
    r"(?:\s*[xX×]\s*(\d+(?:\.\d+)?))?"
    r"\s*(in(?:ches?)?|cm|\")?",
)

# Map extracted item categories to constraint keys from _compute_room_constraints
_CATEGORY_TO_CONSTRAINT: dict[str, str] = {
    "sofa": "sofa",
    "sectional": "sofa",
    "accent chair": "sofa",
    "coffee table": "coffee_table",
    "side table": "coffee_table",
    "console": "coffee_table",
    "dining table": "dining_table",
    "rug": "rug",
    "floor lamp": "floor_lamp",
}


def _parse_product_dims_cm(
    dims_str: str | None,
    category: str | None = None,
) -> tuple[float, float, float] | None:
    """Parse product dimension string into (width_cm, depth_cm, height_cm).

    Handles formats like "84x36x32 inches", "213x91cm", "8x10".
    When no unit is specified: assumes feet for rugs (US convention where
    "8x10" means 8'x10'), inches for all other furniture.
    """
    if not dims_str:
        return None
    match = _DIMS_RE.search(dims_str)
    if not match:
        return None

    d1 = float(match.group(1))
    d2 = float(match.group(2))
    d3 = float(match.group(3)) if match.group(3) else 0.0
    unit = (match.group(4) or "").lower().strip()

    is_cm = unit.startswith("cm")
    is_rug = category and "rug" in category.lower()

    if is_cm:
        factor = 1.0
    elif not unit and is_rug:
        factor = 30.48  # feet → cm (US rug convention: "8x10" = 8'x10')
    else:
        factor = 2.54  # inches → cm

    return (d1 * factor, d2 * factor, d3 * factor)


def _match_category(item: dict[str, Any]) -> str | None:
    """Map an extracted item's category to a constraint key."""
    cat = (item.get("category") or "").lower()
    for keyword, constraint_key in _CATEGORY_TO_CONSTRAINT.items():
        if keyword in cat:
            return constraint_key
    return None


def filter_by_dimensions(
    items: list[dict[str, Any]],
    scored_products: list[list[dict[str, Any]]],
    room_dimensions: RoomDimensions | None,
) -> list[list[dict[str, Any]]]:
    """Step 4: Annotate products with room fit assessment.

    Does NOT remove products — only adds room_fit and room_fit_detail fields.
    Products without parseable dimensions pass through unchanged.
    """
    if room_dimensions is None:
        return scored_products

    if len(items) != len(scored_products):
        log.warning(
            "dimension_filter_list_mismatch",
            items_len=len(items),
            scored_len=len(scored_products),
        )
        return scored_products

    constraints = _compute_room_constraints(room_dimensions)

    for item_idx, item_scores in enumerate(scored_products):
        constraint_key = _match_category(items[item_idx])
        if constraint_key is None:
            continue

        constraint = constraints.get(constraint_key)
        if constraint is None:
            continue

        # Rugs use width_cm/length_cm (2-axis check); furniture uses max_*_cm (1-axis)
        is_rug = constraint_key == "rug"
        if is_rug:
            rug_max_w = float(constraint.get("width_cm") or "0")
            rug_max_l = float(constraint.get("length_cm") or "0")
            if rug_max_w <= 0 or rug_max_l <= 0:
                continue
        else:
            max_cm = float(
                constraint.get("max_width_cm")
                or constraint.get("max_length_cm")
                or constraint.get("max_height_cm")
                or "0"
            )
            if max_cm <= 0:
                continue

        item_category = items[item_idx].get("category")
        for product in item_scores:
            product_dims_str = product.get("dimensions") or product.get("estimated_dimensions")
            parsed = _parse_product_dims_cm(product_dims_str, category=item_category)
            if parsed is None:
                continue

            if is_rug:
                # Compare rug width/length against room limits (sorted to match)
                rug_dims = sorted(parsed[:2])
                rug_limits = sorted([rug_max_w, rug_max_l])
                ratio = max(
                    rug_dims[0] / rug_limits[0] if rug_limits[0] > 0 else 0,
                    rug_dims[1] / rug_limits[1] if rug_limits[1] > 0 else 0,
                )
                limit_desc = f'{rug_max_w / 2.54:.0f}"x{rug_max_l / 2.54:.0f}"'
            else:
                largest_dim = max(parsed)
                ratio = largest_dim / max_cm if max_cm > 0 else 0
                limit_desc = f'{max_cm / 2.54:.0f}"'

            product_largest = max(parsed) / 2.54  # largest dim in inches
            if ratio <= 1.0:
                product["room_fit"] = "fits"
                product["room_fit_detail"] = f'{product_largest:.0f}" within {limit_desc} limit'
            elif ratio <= 1.15:
                product["room_fit"] = "tight"
                product["room_fit_detail"] = f'{product_largest:.0f}" near {limit_desc} limit'
            else:
                product["room_fit"] = "too_large"
                product["room_fit_detail"] = f'{product_largest:.0f}" exceeds {limit_desc} limit'

    return scored_products


# === Step 5: Confidence Filtering ===


def _google_shopping_url(item: dict[str, Any]) -> str:
    """Build a Google Shopping fallback URL for an unmatched item."""
    keywords = (
        f"{item.get('category', '')} {item.get('material', '')} "
        f"{item.get('color', '')} {item.get('style', '')}"
    ).strip()
    encoded = urllib.parse.quote_plus(keywords)
    return f"https://www.google.com/search?tbm=shop&q={encoded}"


_SCORE_LABELS = {
    "category_score": "category",
    "material_score": "material",
    "color_score": "color",
    "style_score": "style",
    "dimensions_score": "dimensions",
}


def _build_fit_detail(scored: dict[str, Any]) -> str:
    """Build a human-readable explanation of why a match is 'tight' (0.5-0.79).

    Identifies the weakest sub-scores so the user understands the gap.
    """
    weak: list[str] = []
    for key, label in _SCORE_LABELS.items():
        score = scored.get(key)
        if isinstance(score, (int, float)) and score < 0.5:
            weak.append(label)

    if weak:
        return f"Weak on {', '.join(weak)}"
    return "Close overall match — no single criterion is weak"


def apply_confidence_filtering(
    items: list[dict[str, Any]],
    scored_products: list[list[dict[str, Any]]],
) -> tuple[list[ProductMatch], list[UnmatchedItem], int]:
    """Step 5: Apply confidence thresholds and build final output.

    Deduplicates across items: if the same product URL was already matched
    to a higher-priority item, it's skipped for subsequent items.

    Returns (matched_products, unmatched_items, total_estimated_cost_cents).
    """
    matched: list[ProductMatch] = []
    unmatched: list[UnmatchedItem] = []
    total_cost = 0
    used_urls: set[str] = set()

    for item, scores in zip(items, scored_products, strict=True):
        best = None
        for scored in scores:
            raw_conf = scored.get("weighted_total", 0)
            confidence = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
            url = scored.get("product_url", "")
            if (
                confidence >= 0.5
                and url not in used_urls
                and (best is None or confidence > float(best.get("weighted_total", 0) or 0))
            ):
                best = scored

        if best is None:
            unmatched.append(
                UnmatchedItem(
                    category=item.get("category", "Unknown"),
                    search_keywords=(
                        f"{item.get('description', '')} "
                        f"{item.get('material', '')} "
                        f"{item.get('color', '')}"
                    ).strip(),
                    google_shopping_url=_google_shopping_url(item),
                )
            )
            continue

        raw = best.get("weighted_total", 0.0)
        confidence = float(raw) if isinstance(raw, (int, float)) else 0.0
        best_url = best.get("product_url", "")
        if best_url:
            used_urls.add(best_url)
        raw_price = best.get("price_cents", 0)
        price_cents = int(raw_price) if isinstance(raw_price, (int, float)) else 0
        total_cost += price_cents

        fit_status = None
        fit_detail = None
        if confidence >= 0.8:
            fit_status = "fits"
        elif confidence >= 0.5:
            fit_status = "tight"
            fit_detail = _build_fit_detail(best)

        # Downgrade fit_status if product exceeds room dimensions
        room_fit = best.get("room_fit")
        if room_fit == "too_large":
            fit_status = "tight"
            fit_detail = best.get("room_fit_detail", fit_detail)
        elif room_fit == "tight" and fit_status == "fits":
            fit_status = "tight"
            fit_detail = best.get("room_fit_detail")

        matched.append(
            ProductMatch(
                category_group=item.get("category", "Unknown"),
                product_name=best.get("product_name", "Unknown"),
                retailer=_extract_retailer(best.get("product_url", "")),
                price_cents=price_cents,
                product_url=best.get("product_url", ""),
                image_url=best.get("image_url"),
                confidence_score=round(confidence, 3),
                why_matched=best.get("why_matched", ""),
                fit_status=fit_status,
                fit_detail=fit_detail,
                dimensions=item.get("estimated_dimensions"),
            )
        )

    return matched, unmatched, total_cost


_RETAILER_NAMES: dict[str, str] = {
    "amazon": "Amazon",
    "wayfair": "Wayfair",
    "ikea": "IKEA",
    "cb2": "CB2",
    "crateandbarrel": "Crate & Barrel",
    "potterybarn": "Pottery Barn",
    "westelm": "West Elm",
    "restorationhardware": "RH",
    "rh": "RH",
    "etsy": "Etsy",
    "overstock": "Overstock",
    "target": "Target",
    "walmart": "Walmart",
    "homedepot": "Home Depot",
    "lowes": "Lowe's",
    "anthropologie": "Anthropologie",
    "arhaus": "Arhaus",
    "allmodern": "AllModern",
    "joybird": "Joybird",
    "article": "Article",
    "castlery": "Castlery",
    "worldmarket": "World Market",
    "pier1": "Pier 1",
    "zgallerie": "Z Gallerie",
}


def _extract_retailer(url: str) -> str:
    """Extract retailer name from product URL.

    Checks known retailer domains first, falls back to domain capitalization.
    """
    if not url:
        return "Unknown"
    try:
        domain = urllib.parse.urlparse(url).netloc
        if not domain:
            return "Unknown"
        bare = domain.replace("www.", "").split(".")[0]
        if not bare:
            return "Unknown"
        return _RETAILER_NAMES.get(bare.lower(), bare.capitalize())
    except Exception:
        return "Unknown"


# === Main Activity ===


@activity.defn
async def generate_shopping_list(
    input: GenerateShoppingListInput,
) -> GenerateShoppingListOutput:
    """5-step shopping pipeline: extract → search → score → filter → output.

    Stateless: all data passed via input, output returned.
    """
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        raise ApplicationError("ANTHROPIC_API_KEY not set", non_retryable=True)

    exa_key = os.environ.get("EXA_API_KEY")
    if not exa_key:
        raise ApplicationError("EXA_API_KEY not set", non_retryable=True)

    from app.utils.tracing import wrap_anthropic

    client = wrap_anthropic(anthropic.AsyncAnthropic(api_key=anthropic_key))

    # Resolve R2 storage keys to presigned URLs (pass through existing URLs)
    from app.utils.r2 import resolve_url, resolve_urls

    design_image_url = resolve_url(input.design_image_url)
    original_room_photo_urls = resolve_urls(input.original_room_photo_urls)

    has_brief = input.design_brief is not None
    num_revisions = len(input.revision_history)
    num_photos = len(original_room_photo_urls)

    log.info(
        "shopping_pipeline_start",
        has_brief=has_brief,
        num_revisions=num_revisions,
        num_photos=num_photos,
        has_lidar=input.room_dimensions is not None,
    )

    # Step 1: Extract items
    try:
        items = await extract_items(
            client,
            design_image_url,
            original_room_photo_urls,
            input.design_brief,
            input.revision_history,
            source_urls=[input.design_image_url, *input.original_room_photo_urls],
            room_context=input.room_context,
            room_dimensions=input.room_dimensions,
        )
    except anthropic.RateLimitError as e:
        log.warning("shopping_extraction_rate_limited")
        raise ApplicationError(
            f"Claude rate limited during extraction: {e}",
            non_retryable=False,
        ) from e
    except anthropic.APIStatusError as e:
        log.error("shopping_extraction_api_error", status=e.status_code)
        non_retryable = 400 <= e.status_code < 500
        raise ApplicationError(
            f"Claude API error during extraction ({e.status_code}): {e}",
            non_retryable=non_retryable,
        ) from e

    log.info("shopping_items_extracted", count=len(items))
    if not items:
        log.warning("shopping_no_items_extracted")
        return GenerateShoppingListOutput(items=[], unmatched=[], total_estimated_cost_cents=0)

    # Step 2: Search products
    try:
        search_results = await search_all_items(
            items, exa_key, room_dimensions=input.room_dimensions
        )
    except Exception as e:
        log.error("shopping_search_failed", error=str(e))
        raise ApplicationError(f"Exa search failed: {e}", non_retryable=False) from e

    total_results = sum(len(r) for r in search_results)
    log.info("shopping_search_complete", items=len(items), total_results=total_results)

    # Step 3: Score products (individual failures handled inside score_all_products;
    # these handlers catch errors from the gather setup or unexpected propagation)
    try:
        scored = await score_all_products(
            client,
            items,
            search_results,
            input.design_brief,
            room_dimensions=input.room_dimensions,
        )
    except anthropic.RateLimitError as e:
        log.warning("shopping_scoring_rate_limited")
        raise ApplicationError(
            f"Claude rate limited during scoring: {e}",
            non_retryable=False,
        ) from e
    except anthropic.APIStatusError as e:
        log.error("shopping_scoring_api_error", status=e.status_code)
        non_retryable = 400 <= e.status_code < 500
        raise ApplicationError(
            f"Claude API error during scoring ({e.status_code}): {e}",
            non_retryable=non_retryable,
        ) from e

    # Step 4: Dimension filtering
    scored = filter_by_dimensions(items, scored, input.room_dimensions)

    # Step 5: Confidence filtering
    matched, unmatched, total_cost = apply_confidence_filtering(items, scored)

    log.info(
        "shopping_pipeline_complete",
        matched=len(matched),
        unmatched=len(unmatched),
        total_cost_cents=total_cost,
    )

    return GenerateShoppingListOutput(
        items=matched,
        unmatched=unmatched,
        total_estimated_cost_cents=total_cost,
    )
