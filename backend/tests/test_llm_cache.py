"""Tests for llm_cache — disk-based LLM response cache.

Covers JSON and binary caching, cache miss/hit, disabled mode,
error handling (corrupt files, OS errors), and key hashing.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from app.utils import llm_cache


@pytest.fixture(autouse=True)
def _enable_cache(tmp_path):
    """Point _CACHE_DIR to a temp directory for every test."""
    with patch.object(llm_cache, "_CACHE_DIR", str(tmp_path)):
        yield tmp_path


class TestCachePath:
    """Tests for _cache_path helper."""

    def test_returns_path_when_enabled(self, tmp_path):
        path = llm_cache._cache_path("ns", ["a", "b"])
        assert path is not None
        assert path.parent == tmp_path / "ns"
        assert path.suffix == ".json"

    def test_creates_namespace_directory(self, tmp_path):
        llm_cache._cache_path("deep/nested", ["k"])
        assert (tmp_path / "deep" / "nested").is_dir()

    def test_returns_none_when_disabled(self):
        with patch.object(llm_cache, "_CACHE_DIR", None):
            assert llm_cache._cache_path("ns", ["a"]) is None

    def test_returns_none_when_empty_string(self):
        with patch.object(llm_cache, "_CACHE_DIR", ""):
            assert llm_cache._cache_path("ns", ["a"]) is None

    def test_custom_extension(self, tmp_path):
        path = llm_cache._cache_path("ns", ["x"], ext="png")
        assert path.suffix == ".png"

    def test_different_keys_produce_different_paths(self):
        p1 = llm_cache._cache_path("ns", ["a", "b"])
        p2 = llm_cache._cache_path("ns", ["a", "c"])
        assert p1 != p2

    def test_same_keys_produce_same_path(self):
        p1 = llm_cache._cache_path("ns", ["x", "y"])
        p2 = llm_cache._cache_path("ns", ["x", "y"])
        assert p1 == p2


class TestGetSetCachedJSON:
    """Tests for JSON caching (get_cached / set_cached)."""

    def test_miss_returns_none(self):
        assert llm_cache.get_cached("ns", ["no-such-key"]) is None

    def test_round_trip(self):
        data = {"model": "claude", "tokens": 42}
        llm_cache.set_cached("test", ["k1"], data)
        result = llm_cache.get_cached("test", ["k1"])
        assert result == data

    def test_stores_lists(self):
        data = [1, "two", {"three": 3}]
        llm_cache.set_cached("ns", ["list"], data)
        assert llm_cache.get_cached("ns", ["list"]) == data

    def test_different_namespaces_are_isolated(self):
        llm_cache.set_cached("ns_a", ["k"], "a")
        llm_cache.set_cached("ns_b", ["k"], "b")
        assert llm_cache.get_cached("ns_a", ["k"]) == "a"
        assert llm_cache.get_cached("ns_b", ["k"]) == "b"

    def test_overwrite_existing(self):
        llm_cache.set_cached("ns", ["k"], "old")
        llm_cache.set_cached("ns", ["k"], "new")
        assert llm_cache.get_cached("ns", ["k"]) == "new"

    def test_corrupt_json_returns_none(self, tmp_path):
        """Corrupt cache file should be treated as cache miss."""
        llm_cache.set_cached("ns", ["k"], {"valid": True})
        path = llm_cache._cache_path("ns", ["k"])
        path.write_text("not valid json{{{")
        assert llm_cache.get_cached("ns", ["k"]) is None

    def test_set_noop_when_disabled(self):
        with patch.object(llm_cache, "_CACHE_DIR", None):
            llm_cache.set_cached("ns", ["k"], "value")  # should not raise

    def test_get_noop_when_disabled(self):
        with patch.object(llm_cache, "_CACHE_DIR", None):
            assert llm_cache.get_cached("ns", ["k"]) is None

    def test_set_handles_unserializable(self):
        """Non-JSON-serializable value should not raise (TypeError caught)."""
        llm_cache.set_cached("ns", ["k"], object())  # not JSON-serializable

    def test_set_handles_os_error(self, tmp_path):
        """OSError during write should not raise."""
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            llm_cache.set_cached("ns", ["k"], "value")  # should not raise

    def test_get_handles_os_error(self, tmp_path):
        """OSError during read should return None."""
        llm_cache.set_cached("ns", ["k"], "value")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            assert llm_cache.get_cached("ns", ["k"]) is None


class TestGetSetCachedBytes:
    """Tests for binary caching (get_cached_bytes / set_cached_bytes)."""

    def test_miss_returns_none(self):
        assert llm_cache.get_cached_bytes("ns", ["no-such-key"]) is None

    def test_round_trip(self):
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        llm_cache.set_cached_bytes("img", ["k1"], data)
        result = llm_cache.get_cached_bytes("img", ["k1"])
        assert result == data

    def test_custom_extension(self, tmp_path):
        data = b"JPEG data"
        llm_cache.set_cached_bytes("img", ["k"], data, ext="jpg")
        result = llm_cache.get_cached_bytes("img", ["k"], ext="jpg")
        assert result == data

    def test_noop_when_disabled(self):
        with patch.object(llm_cache, "_CACHE_DIR", None):
            llm_cache.set_cached_bytes("ns", ["k"], b"data")  # should not raise
            assert llm_cache.get_cached_bytes("ns", ["k"]) is None

    def test_set_handles_os_error(self):
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            llm_cache.set_cached_bytes("ns", ["k"], b"data")  # should not raise

    def test_get_handles_os_error(self):
        llm_cache.set_cached_bytes("ns", ["k"], b"data")
        with patch.object(Path, "read_bytes", side_effect=OSError("permission denied")):
            assert llm_cache.get_cached_bytes("ns", ["k"]) is None

    def test_empty_bytes(self):
        llm_cache.set_cached_bytes("ns", ["empty"], b"")
        # Empty bytes is falsy but still a valid cache hit
        result = llm_cache.get_cached_bytes("ns", ["empty"])
        # Empty file: read_bytes returns b"", which is falsy
        # The module doesn't guard against empty — it returns whatever is on disk
        assert result == b""
