"""Prompt versioning — load versioned prompts from manifest.

Reads `prompts/prompt_versions.json` to determine the active version of each
prompt, then loads the corresponding versioned file (e.g., `generation_v2.txt`).
Falls back to the unversioned file if the versioned file doesn't exist.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"
VERSIONS_FILE = PROMPTS_DIR / "prompt_versions.json"


def _load_versions_manifest() -> dict[str, dict[str, str]]:
    """Load the prompt versions manifest."""
    if not VERSIONS_FILE.exists():
        return {}
    try:
        return json.loads(VERSIONS_FILE.read_text())  # type: ignore[no-any-return]
    except (json.JSONDecodeError, OSError) as e:
        log.error("prompt_manifest_corrupted: %s", e)
        return {}


def get_active_version(prompt_name: str) -> str:
    """Get the active version string for a prompt (e.g., 'v2')."""
    manifest = _load_versions_manifest()
    entry = manifest.get(prompt_name, {})
    return entry.get("active", "v1")


def get_previous_version(prompt_name: str) -> str | None:
    """Get the previous version string for a prompt, or None."""
    manifest = _load_versions_manifest()
    entry = manifest.get(prompt_name, {})
    return entry.get("previous")


def load_versioned_prompt(prompt_name: str, version: str | None = None) -> str:
    """Load a prompt file at the specified (or active) version.

    Tries `{prompt_name}_{version}.txt` first, falls back to `{prompt_name}.txt`.

    Args:
        prompt_name: Base name without extension (e.g., "generation").
        version: Specific version to load (e.g., "v1"). If None, uses active.

    Returns:
        The prompt text content.

    Raises:
        FileNotFoundError: If neither versioned nor base file exists.
    """
    if version is None:
        version = get_active_version(prompt_name)

    versioned_path = PROMPTS_DIR / f"{prompt_name}_{version}.txt"
    if versioned_path.exists():
        return versioned_path.read_text()

    # Fall back to unversioned file
    base_path = PROMPTS_DIR / f"{prompt_name}.txt"
    if base_path.exists():
        return base_path.read_text()

    raise FileNotFoundError(f"No prompt file found: tried {versioned_path} and {base_path}")


def strip_changelog_lines(text: str) -> str:
    """Remove version changelog comments (e.g. '[v5: ...]') from prompt text.

    These are developer metadata that waste tokens and add noise if sent
    to the model. The prompt_versions.json manifest tracks versions instead.
    """
    lines = text.split("\n")
    filtered = [ln for ln in lines if not (ln.startswith("[v") and ln.endswith("]"))]
    return "\n".join(filtered).lstrip("\n")


def list_versions(prompt_name: str) -> list[str]:
    """List all available versions for a prompt."""
    versions = []
    for path in sorted(PROMPTS_DIR.glob(f"{prompt_name}_v*.txt")):
        # Extract version from filename: "generation_v2.txt" -> "v2"
        stem = path.stem  # "generation_v2"
        version = stem.replace(f"{prompt_name}_", "")
        versions.append(version)
    return versions
