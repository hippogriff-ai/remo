"""Deterministic quality checks for shopping search results.

Three checks, cheapest first:
1. Link liveness: HTTP HEAD → 200?
2. Product match: does the page contain the right product category/material?
3. Dimension match: do parsed dimensions fit room constraints?
"""

from __future__ import annotations

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
