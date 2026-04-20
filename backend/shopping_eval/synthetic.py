"""Score-Then-Search — run Claude on the synthetic listing prompt, cache the output.

Wraps `build_synthetic_listing_query` (which returns a *prompt*) with the actual
Claude call that turns that prompt into a listing-shaped query string ready for
Exa's neural search. File-cached by prompt hash so re-runs inside the tuning
loop are $0.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anthropic

from app.activities.shopping import build_synthetic_listing_query

if TYPE_CHECKING:
    from app.models.contracts import DesignBrief

DEFAULT_MODEL = os.environ.get("SHOPPING_EVAL_CLAUDE_MODEL", "claude-opus-4-5")
_SYNTHETIC_CACHE_DIR = os.environ.get("SYNTHETIC_LISTING_CACHE_DIR")


def _cache_path(prompt: str, model: str) -> Path | None:
    if not _SYNTHETIC_CACHE_DIR:
        return None
    key = hashlib.sha256(f"{model}:{prompt}".encode()).hexdigest()[:16]
    cache_dir = Path(_SYNTHETIC_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"synthetic_{key}.json"


async def generate_synthetic_listing(
    item: dict[str, Any],
    anthropic_api_key: str,
    design_brief: DesignBrief | None = None,
    *,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 256,
) -> str:
    """Ask Claude to write a synthetic product listing, return the listing text.

    Cache hit → returns cached listing without calling Claude. Cache miss →
    one Claude call, cached for next run.
    """
    prompt = build_synthetic_listing_query(item, design_brief=design_brief)

    cache_path = _cache_path(prompt, model)
    if cache_path and cache_path.exists():
        cached = json.loads(cache_path.read_text())
        return str(cached["listing"])

    client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )

    listing = ""
    if resp.content:
        first = resp.content[0]
        listing = getattr(first, "text", "").strip()

    if cache_path:
        cache_path.write_text(json.dumps({"listing": listing, "model": model}))

    return listing
