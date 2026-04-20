"""SearchTrialRunner — runs queries through Exa and evaluates with deterministic checks.

No LLM calls. Pure search + 3 binary checks. Returns structured TrialResult.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.activities.shopping import _search_exa

from .checks import check_dimension_matches, check_link_loads, check_product_matches
from .models import CheckResult, TrialResult

if TYPE_CHECKING:
    from app.models.contracts import RoomDimensions


async def run_trial(
    query: str,
    query_components: list[str],
    target_item: dict[str, Any],
    exa_api_key: str,
    search_type: str = "auto",
    num_results: int = 3,
    room_dimensions: RoomDimensions | None = None,
    include_domains: list[str] | None = None,
    include_text: list[str] | None = None,
    *,
    skip_link_check: bool = False,
) -> TrialResult:
    """Run a single query through Exa and evaluate results with 3 checks.

    Args:
        query: The search query string.
        query_components: Which components built this query (for ablation).
        target_item: The extracted item dict we're searching for.
        exa_api_key: Exa API key.
        search_type: Exa search type ("auto", "deep", "keyword").
        num_results: Number of results to request.
        room_dimensions: Optional room dimensions for check 3.
        include_domains: Optional domain whitelist.
        include_text: Optional text filter.
        skip_link_check: Skip HTTP HEAD checks (for cached/offline runs).

    Returns:
        TrialResult with per-result check outcomes and aggregate rates.
    """
    start_ms = int(time.monotonic() * 1000)

    # Run search
    async with httpx.AsyncClient() as http_client:
        results = await _search_exa(
            http_client,
            query,
            exa_api_key,
            num_results,
            search_type=search_type,
            include_domains=include_domains,
            include_text=include_text,
        )

    if not results:
        elapsed = int(time.monotonic() * 1000) - start_ms
        return TrialResult(
            query=query,
            query_components=query_components,
            search_type=search_type,
            results_count=0,
            check_results=[],
            link_alive_rate=None,
            product_match_rate=None,
            dimension_match_rate=None,
            latency_ms=elapsed,
        )

    # Run checks for each result
    async def _check_one(r: dict[str, Any]) -> CheckResult:
        url = r.get("url", "")

        # Check 1: link liveness
        if skip_link_check:
            link_ok = None
        else:
            link_ok = await check_link_loads(url)

        # Check 2: product match (pure string matching, instant)
        exa_summary = r.get("summary") or {}
        exa_text = r.get("text", "")
        pm = check_product_matches(exa_summary, exa_text, target_item)

        # Check 3: dimension match
        dims_str = None
        if isinstance(exa_summary, dict):
            dims_str = exa_summary.get("dimensions")
        dm = check_dimension_matches(dims_str, target_item, room_dimensions)

        return CheckResult(
            url=url,
            link_loads=link_ok,
            product_matches=pm.matches,
            dimension_matches=dm.matches,
            match_detail=pm.detail,
            dimension_detail=dm.detail,
        )

    check_results = list(await asyncio.gather(*[_check_one(r) for r in results]))

    elapsed = int(time.monotonic() * 1000) - start_ms

    # Compute aggregate rates (ignore None values)
    link_checks = [c for c in check_results if c.link_loads is not None]
    product_checks = [c for c in check_results if c.product_matches is not None]
    dim_checks = [c for c in check_results if c.dimension_matches is not None]

    return TrialResult(
        query=query,
        query_components=query_components,
        search_type=search_type,
        results_count=len(check_results),
        check_results=check_results,
        link_alive_rate=(
            sum(1 for c in link_checks if c.link_loads) / len(link_checks) if link_checks else None
        ),
        product_match_rate=(
            sum(1 for c in product_checks if c.product_matches) / len(product_checks)
            if product_checks
            else None
        ),
        dimension_match_rate=(
            sum(1 for c in dim_checks if c.dimension_matches) / len(dim_checks)
            if dim_checks
            else None
        ),
        latency_ms=elapsed,
    )
