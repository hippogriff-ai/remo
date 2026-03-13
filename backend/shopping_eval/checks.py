"""Deterministic quality checks for shopping search results.

Three checks, cheapest first:
1. Link liveness: HTTP HEAD → 200?
2. Product match: does the page contain the right product category/material?
3. Dimension match: do parsed dimensions fit room constraints?
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_LINK_CHECK_TIMEOUT = 5.0


async def check_link_loads(url: str, *, timeout: float = _LINK_CHECK_TIMEOUT) -> bool:
    """Check 1: Can the URL be loaded? (HTTP HEAD, follow redirects.)

    Returns True if status is 200-399. Returns False on timeout, connection
    error, or 4xx/5xx. Cost: ~10ms, $0.
    """
    if not url:
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.head(url, timeout=timeout)
            return resp.status_code < 400
    except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError):
        return False


# --- Check 2: Product match ---


@dataclass(frozen=True)
class ProductMatchResult:
    """Result of product match check with explanation."""

    matches: bool
    detail: str


_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "sofa": ["sofa", "couch", "loveseat", "settee"],
    "sectional": ["sectional", "l-shaped sofa", "modular sofa"],
    "accent chair": ["chair", "armchair", "accent chair", "lounge chair"],
    "coffee table": ["coffee table", "cocktail table"],
    "side table": ["side table", "end table", "accent table", "nightstand"],
    "console": ["console", "entry table", "hallway table", "sofa table"],
    "dining table": ["dining table", "kitchen table"],
    "rug": ["rug", "carpet", "area rug", "runner"],
    "floor lamp": ["floor lamp", "standing lamp", "arc lamp"],
    "table lamp": ["table lamp", "desk lamp", "bedside lamp"],
    "pendant": ["pendant", "chandelier", "hanging light"],
    "chandelier": ["chandelier", "pendant", "ceiling light"],
    "curtains": ["curtain", "drape", "window treatment", "panel"],
    "throw pillow": ["pillow", "cushion", "throw pillow"],
    "blanket": ["throw", "blanket", "afghan"],
    "ottoman": ["ottoman", "pouf", "footstool"],
    "wall art": ["art", "print", "painting", "canvas", "poster", "wall decor"],
    "mirror": ["mirror"],
    "bookshelf": ["bookshelf", "bookcase", "shelving", "etagere"],
    "credenza": ["credenza", "sideboard", "buffet", "media console"],
    "planter": ["planter", "pot", "vase"],
}


def _normalize(text: str) -> str:
    """Lowercase and strip for matching."""
    return text.lower().strip()


def check_product_matches(
    exa_summary: dict,
    exa_text: str,
    target_item: dict,
) -> ProductMatchResult:
    """Check 2: Is the product actually the right type of product?

    Checks that the Exa result's product_name or page text contains keywords
    matching the target item's category. Pure string matching, no LLM. Cost: $0.

    Does NOT check style/color/material match — that's the LLM scorer's job.
    This check only filters out completely wrong products (e.g., a candle
    when searching for a sofa).
    """
    target_category = _normalize(target_item.get("category", ""))
    if not target_category:
        return ProductMatchResult(matches=False, detail="No target category")

    corpus_parts = []
    product_name = exa_summary.get("product_name", "")
    if product_name:
        corpus_parts.append(_normalize(product_name))
    if exa_text:
        corpus_parts.append(_normalize(exa_text))

    if not corpus_parts:
        return ProductMatchResult(matches=False, detail="No product data to check")

    corpus = " ".join(corpus_parts)

    keywords = []
    for cat_key, cat_keywords in _CATEGORY_KEYWORDS.items():
        if cat_key in target_category or target_category in cat_key:
            keywords.extend(cat_keywords)
            break

    if not keywords:
        keywords = [target_category]

    for keyword in keywords:
        if keyword in corpus:
            return ProductMatchResult(
                matches=True,
                detail=f"Found '{keyword}' in product data",
            )

    return ProductMatchResult(
        matches=False,
        detail=f"None of {keywords[:3]} found in product data",
    )


# --- Check 3: Dimension match ---

from app.activities.shopping import (
    _compute_room_constraints,
    _match_category,
    _parse_product_dims_cm,
)
from app.models.contracts import RoomDimensions


@dataclass(frozen=True)
class DimensionMatchResult:
    """Result of dimension check. matches=None means inconclusive."""

    matches: bool | None  # True=fits, False=too large, None=can't determine
    detail: str


def check_dimension_matches(
    exa_dimensions_str: str | None,
    target_item: dict,
    room_dimensions: RoomDimensions | None,
) -> DimensionMatchResult:
    """Check 3: Do the product dimensions fit the room?

    Reuses existing _parse_product_dims_cm and _compute_room_constraints
    from shopping.py. Returns None (inconclusive) when dimensions can't
    be parsed or room data is unavailable. Cost: $0.
    """
    if room_dimensions is None:
        return DimensionMatchResult(matches=None, detail="No room dimensions")

    if not exa_dimensions_str:
        return DimensionMatchResult(matches=None, detail="No product dimensions")

    constraint_key = _match_category(target_item)
    if not constraint_key:
        return DimensionMatchResult(matches=None, detail="Category has no size constraints")

    constraints = _compute_room_constraints(room_dimensions)
    cat_constraint = constraints.get(constraint_key)
    if not cat_constraint:
        return DimensionMatchResult(matches=None, detail="No constraint for category")

    item_category = target_item.get("category")
    parsed = _parse_product_dims_cm(exa_dimensions_str, category=item_category)
    if not parsed:
        return DimensionMatchResult(matches=None, detail="Could not parse dimensions")

    is_rug = constraint_key == "rug"
    if is_rug:
        max_w = float(cat_constraint.get("width_cm", 0))
        max_l = float(cat_constraint.get("length_cm", 0))
        if max_w <= 0 or max_l <= 0:
            return DimensionMatchResult(matches=None, detail="Invalid rug constraints")
        rug_dims = sorted(parsed[:2])
        rug_limits = sorted([max_w, max_l])
        ratio = max(
            rug_dims[0] / rug_limits[0] if rug_limits[0] > 0 else 0,
            rug_dims[1] / rug_limits[1] if rug_limits[1] > 0 else 0,
        )
    else:
        max_cm = float(
            cat_constraint.get("max_width_cm")
            or cat_constraint.get("max_length_cm")
            or cat_constraint.get("max_height_cm")
            or "0"
        )
        if max_cm <= 0:
            return DimensionMatchResult(matches=None, detail="Invalid constraint")
        ratio = max(parsed) / max_cm

    if ratio <= 1.15:  # 15% tolerance
        return DimensionMatchResult(
            matches=True,
            detail=f"Ratio {ratio:.2f} <= 1.15 (fits with tolerance)",
        )
    return DimensionMatchResult(
        matches=False,
        detail=f"Ratio {ratio:.2f} > 1.15 (exceeds room constraint)",
    )
