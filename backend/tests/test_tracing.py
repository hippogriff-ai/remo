"""Tests for LangSmith tracing wrapper — zero-cost when LANGSMITH_API_KEY unset."""

from __future__ import annotations

import types
from unittest.mock import MagicMock, patch


class TestTracingDisabled:
    """Verify tracing wrappers are no-ops when LANGSMITH_API_KEY is unset."""

    def test_wrap_anthropic_returns_same_client(self) -> None:
        """wrap_anthropic() returns the same object when tracing is disabled."""
        from app.utils.tracing import wrap_anthropic

        mock_client = MagicMock()
        result = wrap_anthropic(mock_client)
        assert result is mock_client

    def test_traceable_returns_same_function(self) -> None:
        """traceable() is a no-op decorator when tracing is disabled."""
        from app.utils.tracing import traceable

        @traceable(name="test_fn", run_type="chain")
        def my_function(x: int) -> int:
            return x * 2

        assert my_function(5) == 10

    def test_traceable_preserves_function_identity(self) -> None:
        """traceable() does not wrap the function when tracing is disabled."""
        from app.utils.tracing import traceable

        def original(x: int) -> int:
            return x + 1

        decorated = traceable(name="test")(original)
        assert decorated is original

    def test_whitespace_only_key_treated_as_disabled(self) -> None:
        """LANGSMITH_API_KEY=' ' is treated as unset (whitespace stripped)."""
        from app.utils.tracing import wrap_anthropic

        with patch.dict("os.environ", {"LANGSMITH_API_KEY": "   "}):
            mock_client = MagicMock()
            result = wrap_anthropic(mock_client)
            assert result is mock_client


class TestTracingImportFailure:
    """Verify graceful fallback when LANGSMITH_API_KEY is set but langsmith is not installed."""

    @patch.dict("os.environ", {"LANGSMITH_API_KEY": "fake-key"})
    @patch.dict("sys.modules", {"langsmith": None, "langsmith.wrappers": None})
    def test_wrap_anthropic_falls_back_on_import_error(self) -> None:
        """wrap_anthropic() returns unwrapped client when langsmith import fails."""
        from app.utils.tracing import wrap_anthropic

        mock_client = MagicMock()
        result = wrap_anthropic(mock_client)
        assert result is mock_client

    @patch.dict("os.environ", {"LANGSMITH_API_KEY": "fake-key"})
    @patch.dict("sys.modules", {"langsmith": None, "langsmith.wrappers": None})
    def test_traceable_falls_back_on_import_error(self) -> None:
        """traceable() is a no-op decorator when langsmith import fails."""
        from app.utils.tracing import traceable

        def original(x: int) -> int:
            return x + 1

        decorated = traceable(name="test")(original)
        assert decorated is original


class TestTracingRuntimeFailure:
    """Verify graceful fallback when langsmith is installed but wrapping fails at runtime."""

    @patch.dict("os.environ", {"LANGSMITH_API_KEY": "fake-key"})
    def test_wrap_anthropic_falls_back_on_runtime_error(self) -> None:
        """wrap_anthropic() returns unwrapped client when _wrap() raises at runtime."""
        # Create a fake langsmith.wrappers module where wrap_anthropic raises TypeError
        fake_wrappers = types.ModuleType("langsmith.wrappers")
        fake_wrappers.wrap_anthropic = MagicMock(side_effect=TypeError("incompatible client"))  # type: ignore[attr-defined]
        fake_langsmith = types.ModuleType("langsmith")

        modules = {"langsmith": fake_langsmith, "langsmith.wrappers": fake_wrappers}
        with patch.dict("sys.modules", modules):
            from app.utils.tracing import wrap_anthropic

            mock_client = MagicMock()
            result = wrap_anthropic(mock_client)
            assert result is mock_client

    @patch.dict("os.environ", {"LANGSMITH_API_KEY": "fake-key"})
    def test_traceable_falls_back_on_runtime_error(self) -> None:
        """traceable() is a no-op decorator when _traceable() raises at runtime."""
        fake_langsmith = types.ModuleType("langsmith")
        fake_langsmith.traceable = MagicMock(side_effect=ValueError("bad config"))  # type: ignore[attr-defined]

        with patch.dict("sys.modules", {"langsmith": fake_langsmith}):
            from app.utils.tracing import traceable

            def original(x: int) -> int:
                return x + 1

            decorated = traceable(name="test")(original)
            assert decorated is original
