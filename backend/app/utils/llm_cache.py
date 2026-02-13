"""Lightweight LLM response cache for development/testing.

Caches API responses to disk based on a hash of the inputs.
Cache hits avoid redundant API calls; cache misses fall through to real calls.

Controlled by LLM_CACHE_DIR env var — disabled when unset (production default).
Cache invalidates automatically when inputs change (different hash = different file).

Supports both JSON (text responses) and binary (images) caching.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

_CACHE_DIR: str | None = os.environ.get("LLM_CACHE_DIR")


def _cache_path(namespace: str, key_parts: list[str], ext: str = "json") -> Path | None:
    """Return cache file path, or None if caching is disabled."""
    if not _CACHE_DIR:
        return None
    raw = "|".join(key_parts)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:20]
    cache_dir = Path(_CACHE_DIR) / namespace
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{digest}.{ext}"


def get_cached(namespace: str, key_parts: list[str]) -> Any | None:
    """Return cached JSON value if it exists, else None."""
    path = _cache_path(namespace, key_parts)
    if path and path.exists():
        try:
            data = json.loads(path.read_text())
            logger.info("llm_cache_hit", namespace=namespace)
            return data
        except (json.JSONDecodeError, OSError):
            pass
    return None


def set_cached(namespace: str, key_parts: list[str], value: Any) -> None:
    """Save a JSON-serializable value to the cache."""
    path = _cache_path(namespace, key_parts)
    if path:
        try:
            path.write_text(json.dumps(value))
            logger.info("llm_cache_saved", namespace=namespace)
        except (OSError, TypeError):
            pass


def get_cached_bytes(namespace: str, key_parts: list[str], ext: str = "png") -> bytes | None:
    """Return cached binary data if it exists, else None."""
    path = _cache_path(namespace, key_parts, ext=ext)
    if path and path.exists():
        try:
            data = path.read_bytes()
            logger.info("llm_cache_hit", namespace=namespace, size=len(data))
            return data
        except OSError:
            pass
    return None


def set_cached_bytes(namespace: str, key_parts: list[str], data: bytes, ext: str = "png") -> None:
    """Save binary data to the cache."""
    path = _cache_path(namespace, key_parts, ext=ext)
    if path:
        try:
            path.write_bytes(data)
            logger.info("llm_cache_saved", namespace=namespace, size=len(data))
        except OSError:
            pass
