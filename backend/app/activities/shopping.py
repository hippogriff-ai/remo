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
    RoomDimensions,
    UnmatchedItem,
)

log = structlog.get_logger("t3.shopping")

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


# === Step 1: Item Extraction ===

_extraction_prompt_cache: str | None = None


def _load_extraction_prompt(
    design_brief: DesignBrief | None,
    revision_history: list[RevisionRecord],
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

    return template.format(
        design_brief=brief_text,
        iteration_history=iterations_text,
        keep_items=json.dumps(keep_items),
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
) -> list[dict[str, Any]]:
    """Step 1: Extract purchasable items from the design image."""
    prompt_text = _load_extraction_prompt(design_brief, revision_history)
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
    return _validate_extracted_items(raw_items)


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


def _build_search_queries(item: dict[str, Any]) -> list[str]:
    """Build source-aware search queries for an extracted item.

    Queries include "buy" to steer Exa toward product pages (not blogs/reviews).
    Source tag determines query strategy:
    - BRIEF_ANCHORED: user's own language (highest recall)
    - ITERATION_ANCHORED: iteration instruction keywords
    - IMAGE_ONLY: AI-described category + attributes

    All tags get a description-based query when available, since the
    extraction prompt produces rich professional descriptions.
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

    return [q.strip() for q in queries if q.strip()]


EXA_MAX_RETRIES = 1
EXA_RETRY_DELAY = 1.0


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
    """
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
) -> list[dict[str, Any]]:
    """Search Exa for products matching a single extracted item."""
    queries = _build_search_queries(item)
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
) -> list[list[dict[str, Any]]]:
    """Step 2: Search Exa for products for all items (parallelized)."""
    async with httpx.AsyncClient() as http_client:
        tasks = [search_products_for_item(http_client, item, exa_api_key) for item in items]
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
) -> str:
    """Fill scoring prompt template for a specific item-product pair."""
    template = _load_scoring_prompt()

    style_mood = ""
    color_palette = ""
    room_type = ""
    if design_brief:
        room_type = design_brief.room_type
        if design_brief.style_profile:
            style_mood = design_brief.style_profile.mood or ""
            color_palette = ", ".join(design_brief.style_profile.colors)

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
    )


async def score_product(
    client: anthropic.AsyncAnthropic,
    item: dict[str, Any],
    product: dict[str, Any],
    design_brief: DesignBrief | None,
) -> dict[str, Any]:
    """Score a single product against an item using the rubric."""
    prompt = _build_scoring_prompt(item, product, design_brief)

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

    scores["product_url"] = product.get("url", "")
    scores["product_name"] = product.get("title", "Unknown")
    scores["image_url"] = product.get("image")
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
            return await score_product(client, item, product, design_brief)

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
        item_scores.sort(key=lambda s: s.get("weighted_total", 0), reverse=True)

    return all_scores


# === Step 4: Dimension Filtering ===


def filter_by_dimensions(
    items: list[dict[str, Any]],
    scored_products: list[list[dict[str, Any]]],
    room_dimensions: RoomDimensions | None,
) -> list[list[dict[str, Any]]]:
    """Step 4: Filter products by room dimensions if LiDAR data available."""
    if room_dimensions is None:
        return scored_products
    # Dimension filtering is a P2+ enhancement.
    # For now, pass through — the scoring rubric's dimension criterion
    # already penalizes size mismatches.
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
            confidence = scored.get("weighted_total", 0.0)
            url = scored.get("product_url", "")
            if (
                confidence >= 0.5
                and url not in used_urls
                and (best is None or confidence > best.get("weighted_total", 0))
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

        confidence = best.get("weighted_total", 0.0)
        best_url = best.get("product_url", "")
        if best_url:
            used_urls.add(best_url)
        price_cents = best.get("price_cents", 0)
        total_cost += price_cents

        fit_status = None
        fit_detail = None
        if confidence >= 0.8:
            fit_status = "fits"
        elif confidence >= 0.5:
            fit_status = "tight"
            fit_detail = _build_fit_detail(best)

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

    client = anthropic.AsyncAnthropic(api_key=anthropic_key)
    has_brief = input.design_brief is not None
    num_revisions = len(input.revision_history)
    num_photos = len(input.original_room_photo_urls)

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
            input.design_image_url,
            input.original_room_photo_urls,
            input.design_brief,
            input.revision_history,
        )
    except anthropic.RateLimitError as e:
        log.warning("shopping_extraction_rate_limited")
        raise ApplicationError(
            f"Claude rate limited during extraction: {e}",
            non_retryable=False,
        ) from e
    except anthropic.APIStatusError as e:
        log.error("shopping_extraction_api_error", status=e.status_code)
        non_retryable = e.status_code == 400
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
        search_results = await search_all_items(items, exa_key)
    except Exception as e:
        log.error("shopping_search_failed", error=str(e))
        raise ApplicationError(f"Exa search failed: {e}", non_retryable=False) from e

    total_results = sum(len(r) for r in search_results)
    log.info("shopping_search_complete", items=len(items), total_results=total_results)

    # Step 3: Score products (individual failures handled inside score_all_products;
    # these handlers catch errors from the gather setup or unexpected propagation)
    try:
        scored = await score_all_products(client, items, search_results, input.design_brief)
    except anthropic.RateLimitError as e:
        log.warning("shopping_scoring_rate_limited")
        raise ApplicationError(
            f"Claude rate limited during scoring: {e}",
            non_retryable=False,
        ) from e
    except anthropic.APIStatusError as e:
        log.error("shopping_scoring_api_error", status=e.status_code)
        non_retryable = e.status_code == 400
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
