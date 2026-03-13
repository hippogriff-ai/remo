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
