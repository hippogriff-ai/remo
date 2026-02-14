# Skill-Based Style Loading for Intake Agent

## Context

The intake agent bakes **all** design knowledge (10 styles, translation engine, room rules) into a single monolithic system prompt (`backend/prompts/intake_system.txt`, ~220 lines). Every conversation pays the context cost for all 10 styles, even when the user only cares about one. The existing `StyleSkillPack` / `SkillManifest` contracts (`contracts.py:34-64`) were scaffolded but never wired.

**Goal**: Refactor intake to use progressive-disclosure skill loading — deep style knowledge loads on-demand when the agent detects a user's style direction, producing richer briefs while keeping the base prompt lean.

---

## Architecture: Field-Based Detection, Next-Turn Loading

```
Turn 1: User says "I want something cozy"
  -> Agent sees compact skill summary table in base prompt
  -> Agent calls interview_client with requested_skills: ["cozy"]
  -> Backend saves "cozy" to session.loaded_skill_ids

Turn 2: User answers follow-up
  -> Backend rebuilds prompt with cozy.md content injected
  -> Agent now has deep cozy knowledge (~500-1000 words)
  -> Agent asks cozy-specific probing questions

Turn N: Agent drafts brief
  -> style_skills_used: ["cozy"] recorded in DesignBrief
```

**Why field-based, not a new tool**: Adding `requested_skills` to the existing `interview_client` tool preserves the `tool_choice: "any"` + exactly-one-tool-per-turn invariant.

**Why next-turn, not same-turn**: Loading on the next turn avoids a double API call. The compact summary is enough for the detection turn; deep knowledge arrives for probing questions.

---

## Phase 1: Infrastructure (no behavioral changes) — **DONE**

### 1a. NEW: `backend/prompts/skills/` directory — **DONE** (manifest.json + 10 .md files created with all required sections)

```
backend/prompts/skills/
  manifest.json
  cozy.md, modern.md, bright_airy.md, calm.md, luxurious.md,
  rustic.md, minimalist.md, bohemian.md, scandinavian.md, more_space.md
```

**manifest.json** format:
```json
{
  "skills": [
    {
      "skill_id": "cozy",
      "name": "Cozy & Warm",
      "description": "Warm palettes, layered textiles, intimate furniture",
      "style_tags": ["warm", "cozy", "intimate", "layered"],
      "trigger_phrases": ["cozy", "warm", "snug", "comfortable", "inviting"]
    }
  ]
}
```

Each `.md` file sections: Core Parameters, Room-Type Adaptations, Furniture Recommendations, Do's/Don'ts, Common Blends, Example Partial Brief. Content sourced from `specs/DESIGN_INTELLIGENCE.md` Section 2, expanded with room-specific adaptations.

### 1b. NEW: `backend/app/activities/skill_loader.py` — **DONE** (4 functions + structlog + clear_caches, code reviewed & simplified)

Pure Python module (not a Temporal activity — filesystem reads are microseconds).

```python
SKILLS_DIR = PROMPTS_DIR / "skills"   # reuse PROMPTS_DIR from intake.py:31

# Module-level caches (same pattern as intake.py:339 _system_prompt_cache)
_manifest_cache: SkillManifest | None = None
_skill_content_cache: dict[str, str] = {}

def load_manifest() -> SkillManifest          # load/cache manifest.json -> SkillManifest (contracts.py:60)
def load_skill_content(skill_id: str) -> str | None  # load/cache single .md file
def build_skill_summary_block(manifest: SkillManifest) -> str  # compact table for base prompt
def build_loaded_skills_block(skill_ids: list[str]) -> str     # concatenated .md content for injection
```

**Reuses**: `SkillManifest` (`contracts.py:60-64`), `SkillSummary` (`contracts.py:34-40`), `PROMPTS_DIR` (`intake.py:31`).

### 1c. Tests for Phase 1 — **DONE** (14 tests in TestSkillLoader, all passing)

In `test_intake.py`, new class `TestSkillLoader`:
- `test_load_manifest_returns_skills` — manifest loads with 10 skills
- `test_load_manifest_caching` — second call returns cached
- `test_load_skill_content_returns_markdown` — loads cozy.md content
- `test_load_skill_content_unknown_returns_none` — unknown ID returns None
- `test_build_skill_summary_block` — compact table has all skill IDs
- `test_build_loaded_skills_block_single` — one skill's content injected
- `test_build_loaded_skills_block_multiple` — two skills concatenated
- `test_build_loaded_skills_block_empty` — empty list returns placeholder

---

## Phase 2: Prompt Refactor (functionally identical, prompt cleaner) — **DONE**

### 2a. MODIFY: `backend/prompts/intake_system.txt`

**Remove** (lines 29-44): The full TRANSLATION ENGINE table with all 10 style mappings.

**Replace with** compact skill summary block + skill loading instructions:
```
## STYLE SKILL SYSTEM

You have access to style skill packs — deep design guides for specific styles. Below is a
compact summary. When you detect the user's style direction, use `requested_skills` in
your `interview_client` call to load the full guide for the next turn.

| skill_id | Name | Triggers |
|----------|------|----------|
{skill_summary_table}

When a style skill is loaded, its full guide appears below. Use it for probing questions,
design translations, and brief elevation. Without a loaded skill, use the compact summary
above for initial responses.

{loaded_skills_section}
```

**Add** `{skill_summary_table}` placeholder (replaced at runtime by `build_skill_summary_block()`).
**Add** `{loaded_skills_section}` placeholder (replaced at runtime by loaded skill content or "No style guides loaded yet — request skills via `requested_skills` when you detect the user's direction.").

### 2b. MODIFY: `backend/app/activities/intake.py` — `load_system_prompt()`

Add parameter `loaded_skill_ids: list[str] | None = None`.

After loading template from cache (`intake.py:356-357`):
1. Call `skill_loader.load_manifest()` to get manifest
2. Call `skill_loader.build_skill_summary_block(manifest)` -> replace `{skill_summary_table}`
3. Call `skill_loader.build_loaded_skills_block(loaded_skill_ids or [])` -> replace `{loaded_skills_section}`

**Important**: Clear `_system_prompt_cache` usage still works — the cached template has the placeholders. Dynamic replacement happens after cache read, same as `{mode_instructions}` and `{room_analysis_section}`.

### 2c. Tests for Phase 2

Extend `TestLoadSystemPrompt`:
- `test_skill_summary_table_present` — prompt contains compact table with all 10 skill IDs
- `test_translation_table_removed` — full translation table no longer in base prompt
- `test_loaded_skills_placeholder_replaced_empty` — without skills, shows "No style guides loaded"
- `test_loaded_skills_injected` — with `loaded_skill_ids=["cozy"]`, prompt contains cozy.md content
- `test_loaded_skills_multiple` — with 2 skills, both contents present

Update existing `test_translation_table_present` — change to verify compact summary presence instead of full table.

---

## Phase 3: Tool + Contracts (agent can request skills, not yet acted on) — **DONE**

### 3a. MODIFY: `backend/app/models/contracts.py` (additive only)

```python
class IntakeChatOutput(BaseModel):
    # ... existing fields (lines 343-349) ...
    requested_skills: list[str] = []  # NEW: skill IDs agent wants loaded next turn
```

### 3b. MODIFY: `backend/app/activities/intake.py` — Tool schema

Add to `interview_client` tool's `input_schema.properties` (after `domains_covered` at line 233):
```python
"requested_skills": {
    "type": "array",
    "items": {"type": "string"},
    "description": (
        "Style skill IDs to load for the NEXT turn. Request when you "
        "detect the user's style direction. Max 2. Check the skill "
        "summary table for valid IDs."
    ),
},
```

### 3c. MODIFY: `backend/app/activities/intake.py` — `_run_intake_core()`

After extracting `skill_name, skill_data` (line 748), extract requested skills:
```python
# Extract requested_skills from interview_client calls
requested_skills: list[str] = []
if skill_name == "interview_client":
    raw_skills = skill_data.get("requested_skills", [])
    # Validate against manifest and cap at 2
    manifest = skill_loader.load_manifest()
    valid_ids = {s.skill_id for s in manifest.skills}
    requested_skills = [s for s in raw_skills if s in valid_ids][:2]
```

Add to `IntakeChatOutput` construction (line 800):
```python
requested_skills=requested_skills,
```

### 3d. Tests for Phase 3

In `TestToolDefinitions`:
- `test_interview_tool_has_requested_skills` — field present, optional, array of strings

In `TestRunIntakeCoreMocked`:
- `test_requested_skills_extracted` — interview_client with `requested_skills: ["cozy"]` -> output has it
- `test_requested_skills_capped_at_two` — 3 skills -> only first 2 in output
- `test_requested_skills_validated_against_manifest` — invalid ID filtered out
- `test_draft_skill_no_requested_skills` — draft_design_brief doesn't produce requested_skills

In `test_contracts.py` (if round-trip test exists):
- `test_intake_chat_output_requested_skills_round_trip`

---

## Phase 4: Session Wiring (full feature live) — **DONE**

### 4a. MODIFY: `backend/app/api/routes/projects.py` — `_IntakeSession`

Add field (at line 82):
```python
loaded_skill_ids: list[str] = field(default_factory=list)
```

### 4b. MODIFY: `_real_intake_message()` (line 759)

Pass loaded_skill_ids in project_context:
```python
project_context["loaded_skill_ids"] = session.loaded_skill_ids
```

After getting result, update session:
```python
if result.requested_skills:
    # Merge new skills into loaded set (cap at 2 total)
    combined = list(dict.fromkeys(session.loaded_skill_ids + result.requested_skills))
    session.loaded_skill_ids = combined[:2]
```

### 4c. MODIFY: `load_system_prompt()` in intake.py

Read `loaded_skill_ids` from `project_context` (or accept as parameter — same as `room_analysis`):
```python
loaded_skill_ids: list[str] = input.project_context.get("loaded_skill_ids", [])
system_prompt = load_system_prompt(input.mode, turn_number, previous_brief, room_analysis, loaded_skill_ids)
```

### 4d. MODIFY: `build_brief()` in intake.py

When building the brief, populate `style_skills_used` from project_context's loaded_skill_ids:
```python
# In _run_intake_core, when building the brief:
if brief_data and loaded_skill_ids:
    # Set style_skills_used on the built brief
    brief.style_skills_used = loaded_skill_ids
```

### 4e. Clear on reset

- `start_over` (line 965): `_intake_sessions.pop()` already clears the session entirely, which removes `loaded_skill_ids`. No change needed.
- `delete_project` (line 318): Same — session is popped entirely.

### 4f. Tests for Phase 4

In `test_api_endpoints.py` or `test_intake.py`:
- `test_session_tracks_loaded_skill_ids` — after requesting "cozy", session has it
- `test_loaded_skills_passed_to_prompt` — project_context includes loaded_skill_ids
- `test_loaded_skills_persist_across_turns` — second turn still has skills from first
- `test_loaded_skills_cap_at_two` — requesting 3rd skill doesn't exceed cap
- `test_start_over_clears_loaded_skills` — start_over creates fresh session
- `test_style_skills_used_in_brief` — final brief has style_skills_used populated
- `test_loaded_skills_deduplicated` — requesting same skill twice doesn't duplicate

---

## Multi-Style Handling

- **Cap**: Max 2 simultaneous skills (covers 95%+ of blend cases like "cozy modern")
- **`more_space`** is orthogonal — stacks with any style skill
- **Blend guidance** built into each skill's "Common Blends" section
- **Persistent**: Once loaded, skills stay for remaining turns (tracked in session)
- **Token budget**: 2 skills add ~1400-2800 tokens to system prompt (well within limits)

---

## Verification

1. **Unit tests**: `cd backend && .venv/bin/python -m pytest tests/test_intake.py -x`
2. **Full suite**: `.venv/bin/python -m pytest -x -q` — no regressions (1277+ tests)
3. **Lint/type check**: `.venv/bin/python -m ruff check . && .venv/bin/python -m ruff format --check . && .venv/bin/python -m mypy app/`
4. **Integration test**: Multi-turn intake where user mentions "cozy" -> verify `requested_skills: ["cozy"]` in output, then verify next turn's prompt contains cozy.md content
5. **Brief check**: Final DesignBrief has `style_skills_used` populated

---

## Key Existing Code to Reuse

| What | Location | How |
|------|----------|-----|
| `SkillSummary` model | `contracts.py:34-40` | Manifest entries |
| `StyleSkillPack` model | `contracts.py:43-57` | Registry model for skill metadata |
| `SkillManifest` model | `contracts.py:60-64` | Skill index |
| `DesignBrief.style_skills_used` | `contracts.py:119` | Already exists, just populate |
| `IntakeChatInput.available_skills` | `contracts.py:340` | Already exists for manifest summaries |
| `PROMPTS_DIR` constant | `intake.py:31` | Reuse for skill file paths |
| `_system_prompt_cache` pattern | `intake.py:339-357` | Same caching pattern for skill content |
| `DESIGN_INTELLIGENCE.md` Section 2 | `specs/DESIGN_INTELLIGENCE.md:57-77` | Source content for 10 skill .md files |
| `_IntakeSession` dataclass | `projects.py:77-82` | Add loaded_skill_ids field |
| `_real_intake_message()` | `projects.py:759-815` | Wire loaded_skill_ids into project_context |
