"""Tests for prompt versioning and A/B testing support."""

from __future__ import annotations

from app.utils.prompt_versioning import (
    get_active_version,
    get_previous_version,
    list_versions,
    load_versioned_prompt,
)


class TestGetActiveVersion:
    def test_generation_active_v5(self):
        assert get_active_version("generation") == "v5"

    def test_room_preservation_active_v4(self):
        assert get_active_version("room_preservation") == "v4"

    def test_edit_active_v5(self):
        assert get_active_version("edit") == "v5"

    def test_unknown_prompt_defaults_v1(self):
        assert get_active_version("nonexistent") == "v1"


class TestGetPreviousVersion:
    def test_generation_previous_v2(self):
        assert get_previous_version("generation") == "v2"

    def test_edit_previous_v1(self):
        assert get_previous_version("edit") == "v1"

    def test_unknown_no_previous(self):
        assert get_previous_version("nonexistent") is None


class TestLoadVersionedPrompt:
    def test_loads_active_generation_prompt(self):
        text = load_versioned_prompt("generation")
        assert "interior designer" in text.lower()

    def test_loads_v1_generation_prompt(self):
        text = load_versioned_prompt("generation", version="v1")
        assert "interior designer" in text.lower()

    def test_loads_v2_generation_prompt(self):
        text = load_versioned_prompt("generation", version="v2")
        assert "Architectural Digest" in text

    def test_v1_and_v2_are_different(self):
        v1 = load_versioned_prompt("generation", version="v1")
        v2 = load_versioned_prompt("generation", version="v2")
        assert v1 != v2

    def test_loads_edit_v1(self):
        text = load_versioned_prompt("edit", version="v1")
        assert "numbered colored circles" in text

    def test_room_preservation_v1_shorter(self):
        v1 = load_versioned_prompt("room_preservation", version="v1")
        v2 = load_versioned_prompt("room_preservation", version="v2")
        assert len(v1) < len(v2)

    def test_fallback_to_unversioned(self):
        # intake_system.txt has no versions, should fall back
        text = load_versioned_prompt("intake_system")
        assert len(text) > 0


class TestListVersions:
    def test_generation_has_multiple_versions(self):
        versions = list_versions("generation")
        assert "v1" in versions
        assert "v2" in versions
        assert "v5" in versions

    def test_edit_has_multiple_versions(self):
        versions = list_versions("edit")
        assert "v1" in versions
        assert "v5" in versions

    def test_room_preservation_has_multiple_versions(self):
        versions = list_versions("room_preservation")
        assert "v1" in versions
        assert "v2" in versions
        assert "v4" in versions

    def test_unknown_prompt_empty(self):
        versions = list_versions("nonexistent_prompt_xyz")
        assert versions == []
