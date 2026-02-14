"""Skill loader — loads style skill packs from the filesystem on demand.

Pure Python module (not a Temporal activity). Filesystem reads are
microseconds, so no async needed. Module-level caches avoid re-reading
the same files within a single worker process.
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from app.models.contracts import SkillManifest

log = structlog.get_logger("skill_loader")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"
SKILLS_DIR = PROMPTS_DIR / "skills"

# Module-level caches (same pattern as intake.py _system_prompt_cache)
_manifest_cache: SkillManifest | None = None
_skill_content_cache: dict[str, str] = {}


def load_manifest() -> SkillManifest:
    """Load and cache the skill manifest from manifest.json."""
    global _manifest_cache  # noqa: PLW0603
    if _manifest_cache is not None:
        return _manifest_cache

    manifest_path = SKILLS_DIR / "manifest.json"
    raw = json.loads(manifest_path.read_text())
    _manifest_cache = SkillManifest(**raw)
    log.info("skill_manifest_loaded", skill_count=len(_manifest_cache.skills))
    return _manifest_cache


def load_skill_content(skill_id: str) -> str | None:
    """Load and cache a single skill's markdown content.

    Returns None if the skill file doesn't exist.
    """
    if skill_id in _skill_content_cache:
        return _skill_content_cache[skill_id]

    skill_path = SKILLS_DIR / f"{skill_id}.md"
    if not skill_path.exists():
        log.warning("skill_file_not_found", skill_id=skill_id)
        return None

    content = skill_path.read_text()
    _skill_content_cache[skill_id] = content
    return content


def build_skill_summary_block(manifest: SkillManifest) -> str:
    """Build a compact table of all skills for the base system prompt."""
    lines = []
    for skill in manifest.skills:
        triggers = ", ".join(skill.trigger_phrases[:5])
        lines.append(f"| {skill.skill_id} | {skill.name} | {triggers} |")
    return "\n".join(lines)


_NO_SKILLS_PLACEHOLDER = (
    "No style guides loaded yet — request skills via "
    "`requested_skills` when you detect the user's direction."
)


def build_loaded_skills_block(skill_ids: list[str]) -> str:
    """Build the injected content block for loaded skills.

    Concatenates the markdown content for each requested skill ID.
    Returns a placeholder message if no skills are loaded.
    """
    sections = [content for sid in skill_ids if (content := load_skill_content(sid))]

    if not sections:
        if skill_ids:
            log.warning(
                "skill_content_load_failures",
                requested=skill_ids,
                loaded=0,
            )
        return _NO_SKILLS_PLACEHOLDER

    return "\n\n---\n\n".join(sections)


def clear_caches() -> None:
    """Clear all module-level caches. Used in tests."""
    global _manifest_cache  # noqa: PLW0603
    _manifest_cache = None
    _skill_content_cache.clear()
