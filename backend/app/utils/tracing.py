"""LangSmith tracing for LLM calls — zero-cost when LANGSMITH_API_KEY is unset.

Evaluation is lazy: the env var and import are checked on first call, not at
import time.  If langsmith is requested (key is set) but not installed, we
fall back to no-ops with a warning rather than crashing the pipeline.
"""

from __future__ import annotations

import os
from typing import Any

import structlog

_log = structlog.get_logger("tracing")


def wrap_anthropic(client: Any) -> Any:
    """Wrap Anthropic client for auto-tracing. No-op without LANGSMITH_API_KEY."""
    if not os.environ.get("LANGSMITH_API_KEY", "").strip():
        return client
    try:
        from langsmith.wrappers import wrap_anthropic as _wrap
    except (ImportError, ModuleNotFoundError):
        _log.warning(
            "langsmith_not_installed",
            reason="LANGSMITH_API_KEY is set but langsmith is not installed; "
            "install with: pip install 'langsmith>=0.2,<1'",
        )
        return client
    try:
        return _wrap(client)
    except Exception as exc:
        _log.error(
            "langsmith_wrap_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            reason="langsmith wrapping failed; continuing without tracing",
        )
        return client


def wrap_gemini(client: Any) -> Any:
    """Wrap Gemini (google-genai) client for auto-tracing. No-op without LANGSMITH_API_KEY."""
    if not os.environ.get("LANGSMITH_API_KEY", "").strip():
        return client
    try:
        from langsmith.wrappers import wrap_gemini as _wrap
    except (ImportError, ModuleNotFoundError):
        _log.warning(
            "langsmith_not_installed",
            reason="LANGSMITH_API_KEY is set but langsmith is not installed; "
            "install with: pip install 'langsmith>=0.2,<1'",
        )
        return client
    try:
        return _wrap(client)
    except Exception as exc:
        _log.error(
            "langsmith_wrap_gemini_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            reason="langsmith Gemini wrapping failed; continuing without tracing",
        )
        return client


def traceable(**kwargs: Any) -> Any:
    """Decorator for tracing arbitrary functions. No-op without LANGSMITH_API_KEY."""
    if not os.environ.get("LANGSMITH_API_KEY", "").strip():

        def _noop(fn: Any) -> Any:
            return fn

        return _noop
    try:
        from langsmith import traceable as _traceable
    except (ImportError, ModuleNotFoundError):
        _log.warning(
            "langsmith_not_installed",
            reason="LANGSMITH_API_KEY is set but langsmith is not installed; "
            "install with: pip install 'langsmith>=0.2,<1'",
        )

        def _noop(fn: Any) -> Any:
            return fn

        return _noop
    try:
        return _traceable(**kwargs)
    except Exception as exc:
        _log.error(
            "langsmith_traceable_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            reason="langsmith decorator failed; continuing without tracing",
        )

        def _noop(fn: Any) -> Any:
            return fn

        return _noop
