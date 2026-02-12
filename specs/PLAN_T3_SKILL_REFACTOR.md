# Plan: Refactor Intake Agent to Skill-Based Architecture

## Context

The current intake agent uses a single 205-line "god prompt" that handles both interviewing the user AND generating the design brief. Both tools (`update_design_brief` + `respond_to_user`) are called on **every turn** — there's no genuine decision boundary between questioning and brief generation. The transition is controlled by a fixed turn countdown, not by the agent's assessment of readiness.

**Problem:** The agent doesn't dynamically choose between "ask more questions" and "draft the brief." The same prompt tries to do both simultaneously, leading to muddled behavior on boundary turns (especially with vague users).

**Solution:** Split into two mutually exclusive skills — `interview_client` and `draft_design_brief` — where the agent picks ONE per turn. Shared design domain knowledge stays in the system prompt so both skills have a strong baseline understanding.

**Future:** The skill registry pattern is designed to scale. Style-specific draft skills (e.g., `draft_scandinavian`, `draft_mid_century`) can be added as separate prompt files and tool definitions, with server-side prefiltering loading only the relevant skills per turn.

---

## SDK Decision: Raw Anthropic SDK

Use the raw `anthropic` SDK (already in use), NOT the Agent SDK (`claude-agent-sdk`). Rationale:

1. **The intake activity is stateless** — one API call in, one Pydantic model out. The Agent SDK is designed for autonomous agents that read/write files and run commands.
2. **Single call per turn** — the Agent SDK's sub-agent pattern adds a routing call (2 calls/turn). Raw SDK achieves dynamic skill selection in 1 call via tool use.
3. **No new dependency** — the Agent SDK requires Claude Code infrastructure. The raw SDK is already installed and tested.
4. **Same extensibility** — a skill registry (`dict[str, Skill]`) gives the same declarative pattern as `AgentDefinition` without the overhead.
5. **Future style skills** — both approaches need server-side prefiltering when skill count grows. The raw SDK handles this identically.

---

## Architecture

### Skill Registry Pattern

```
backend/prompts/
  intake_shared.txt           # Shared design knowledge (both skills see this)
  skills/
    interview_client.txt      # Interview-specific behavioral instructions
    draft_design_brief.txt    # Draft-specific elevation/validation instructions
    # Future:
    # styles/scandinavian.txt
    # styles/mid_century.txt
```

**Per turn:**
1. Load shared system prompt (`intake_shared.txt` + mode instructions + previous brief)
2. Determine available skills (today: both always available; future: prefilter draft skills by detected style)
3. Append available skill instructions to system prompt
4. Build tools array from available skills
5. One `client.messages.create()` call — Claude picks ONE skill tool
6. Extract the chosen skill's structured output → `IntakeChatOutput`

### Shared Context Architecture

The key question: "both parts need to have a good understanding of the design domain."

**Answer: System prompt = shared brain. Tool descriptions = skill-specific behavior.**

```
┌──────────────────────────────────────────────────────┐
│  SYSTEM PROMPT (shared — both skills see this)       │
│                                                       │
│  ● Three-layer design stack (spatial, human, emotion) │
│  ● Translation engine table (cozy→params, modern→...) │
│  ● DIAGNOSE reasoning pipeline (8 steps)              │
│  ● Diagnostic question bank                           │
│  ● Room-specific guidance (bedroom, kitchen, etc.)    │
│  ● Color psychology reference                         │
│  ● Design domain notepad (11 domains)                 │
│  ● Mode instructions (quick/full/open + turn budget)  │
│  ● Skill selection guidance (when to interview/draft)  │
│  ● Previous brief context (injected on turn 2+)       │
├──────────────────────────────────────────────────────┤
│  TOOL: interview_client                               │
│  Description: behavioral rules for questioning        │
│  ● When: domains uncovered, vague answers, contradict.│
│  ● How: reference question bank, show translations    │
│  Schema: message, options, is_open_ended,             │
│          partial_brief_update (optional)               │
├──────────────────────────────────────────────────────┤
│  TOOL: draft_design_brief                             │
│  Description: behavioral rules for brief generation   │
│  ● When: 6+ domains covered, or final turn            │
│  ● How: apply elevation rules, 20-rule validation     │
│  Schema: message, options, design_brief (required)    │
└──────────────────────────────────────────────────────┘
```

Both tools inherit the shared design knowledge. The interview skill uses it to ask design-informed questions. The draft skill uses it to translate and elevate.

### Partial Brief on Interview Turns

The `interview_client` tool includes an **optional** `partial_brief_update` field. On interview turns, the agent CAN incrementally update the brief (tracking what it's learned). If it does, `IntakeChatOutput.partial_brief` is populated. If it doesn't, the caller preserves the previous turn's brief.

This preserves today's accumulation behavior while keeping the interview skill focused on questioning. The draft skill produces the **complete, final** brief.

### Skill Selection: Dynamic, Not Fixed

The agent decides which skill to use based on conversation state. The system prompt gives explicit guidance:

```
### Skill Selection
Call EXACTLY ONE skill tool per turn:

- `interview_client` — Use when critical design domains are uncovered,
  user answers are vague and need probing, or contradictions need resolution.

- `draft_design_brief` — Use when you have sufficient information across
  key domains (room type, style, lighting, colors, textures, plus constraints).
  A good brief needs 6+ of 11 domains with depth.

Trust your domain assessment. A rich first answer may let you draft after
2 turns. A vague user may need all turns for interviewing.
```

**Final turn enforcement:** When `remaining_turns == 0`, the mode instructions say "You MUST use `draft_design_brief`." Server-side safety net forces `is_summary=True` if the agent still picks interview on the final turn.

---

## Temporal Integration

The skill-based refactor is **entirely contained within the activity**. Nothing outside changes.

```
┌─────────────────────────────────────────────────────────────┐
│  Temporal Workflow (design_project.py) — UNCHANGED          │
│                                                              │
│  step="intake"                                               │
│  wait for signal(complete_intake, brief) ←─────────┐        │
│  step="generate"                                    │        │
│  ...                                                │        │
└─────────────────────────────────────────────────────│────────┘
                                                      │
┌─────────────────────────────────────────────────────│────────┐
│  API Layer (projects.py) — UNCHANGED                │        │
│                                                      │        │
│  POST /intake/message → run_intake_chat activity     │        │
│  if output.is_summary → show summary to user         │        │
│  POST /intake/confirm → signal(complete_intake, brief)│       │
└─────────────────────────────────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  Activity: run_intake_chat (intake.py) — REFACTORED         │
│                                                              │
│  IntakeChatInput(mode, project_context, history, message)    │
│       │                                                      │
│       ▼                                                      │
│  load_system_prompt(shared knowledge + mode + skill guidance)│
│  build tools: [interview_client, draft_design_brief]         │
│  Claude picks ONE skill → extract_skill_call()               │
│       │                                                      │
│       ▼                                                      │
│  IntakeChatOutput(message, brief, is_summary, options)       │
│                                                              │
│  Contract unchanged. Workflow/API see same input/output.     │
└──────────────────────────────────────────────────────────────┘
```

**Key:** The `IntakeChatInput`/`IntakeChatOutput` contracts are frozen (T0 owns). The skill selection is internal to the activity. The workflow and API don't know or care about skills.

---

## Mode System: Turn Budget as Ceiling

The `mode` field in `IntakeChatInput` controls how many turns the agent has — but as a **ceiling**, not a script.

| Mode | Today (rigid) | Skill-based (dynamic) |
|------|--------------|----------------------|
| Quick (`MAX_TURNS=4`) | Always ~3 Qs + 1 summary | 1-3 interviews, draft when ready. Max 4. |
| Full (`MAX_TURNS=11`) | Always ~10 Qs + summary | 3-10 interviews, draft when ready. Max 11. |
| Open (`MAX_TURNS=16`) | Always ~15 Qs + summary | 5-15 interviews, draft when ready. Max 16. |

The agent calls `interview_client` until it assesses it has enough info (6+ domains with depth), then calls `draft_design_brief`. A user who gives a rich first answer gets a draft after 2 turns. A vague user uses all available turns.

**The mode controls the ceiling. The agent's domain assessment controls the actual count.**

Server-side enforcement unchanged: `turn_number >= MAX_TURNS[mode]` → force `is_summary=True`.

---

## Files to Modify

### 1. `backend/prompts/intake_system.txt` (MODIFY)

Update the OUTPUT FORMAT section (currently lines 158-205):

**Replace:**
```
You MUST call BOTH tools on EVERY turn:
1. `update_design_brief` — ...
2. `respond_to_user` — ...
```

**With:**
```
You MUST call EXACTLY ONE skill tool per turn:
- `interview_client` — when you need more information from the user
- `draft_design_brief` — when you're ready to produce the final elevated brief
Choose based on your domain coverage assessment and the mode guidelines above.
```

Keep all other sections (they're the shared design knowledge). Add a "Skill Selection" subsection to the BEHAVIORAL RULES section.

### 2. `backend/app/activities/intake.py` (MODIFY)

**Replace `INTAKE_TOOLS` (lines 37-210)** with two new tool definitions:

`interview_client` tool:
- `message` (required): response text with question
- `options` (optional): quick-reply chips (2-4)
- `is_open_ended` (optional): true for pain points/lifestyle probing
- `partial_brief_update` (optional): incremental brief fields in elevated design language
- `domains_covered` (optional): list of covered domains

`draft_design_brief` tool:
- `message` (required): summary text showing translated parameters
- `options` (optional): confirmation chips ("Captures it" / "Adjustments" / "Start fresh")
- `design_brief` (required): complete DesignBrief with all fields elevated
- The `design_brief` sub-schema reuses the current `update_design_brief` property schemas

**Replace `extract_tool_calls()` (lines 393-407)** with:
```python
def extract_skill_call(response) -> tuple[str | None, dict[str, Any]]:
    """Extract the skill tool call from response. Returns (skill_name, skill_data)."""
    for block in response.content:
        if block.type == "tool_use" and block.name in ("interview_client", "draft_design_brief"):
            return block.name, block.input
    return None, {}
```

**Update `_run_intake_core()` (lines 468-577)** to branch on skill name:
- `"draft_design_brief"` → `is_summary=True`, build full brief from `skill_data["design_brief"]`
- `"interview_client"` → `is_summary=False`, build partial brief from `skill_data.get("partial_brief_update")`
- `None` (fallback) → same fallback logic as today (extract text from content blocks)
- Keep server-side enforcement: force `is_summary=True` if `turn_number >= max_turns`

**Keep unchanged:**
- `load_system_prompt()`, `build_messages()`, `build_brief()`, `build_options()`
- `_format_brief_context()`, `_get_inspiration_note()`
- `MODE_INSTRUCTIONS` dict (add skill selection guidance to each mode)
- `MAX_TURNS` dict
- `@activity.defn run_intake_chat()` wrapper
- All error handling (rate limit, content policy, etc.)

### 3. `backend/app/activities/intake.py` — MODE_INSTRUCTIONS update

Add skill selection guidance to each mode:
```python
"quick": (
    "### Quick Mode (~3 turns)\n"
    # ... existing content ...
    "\n\n### Skill Selection\n"
    "Call EXACTLY ONE skill per turn:\n"
    "- `interview_client`: when domains are uncovered or answers need probing\n"
    "- `draft_design_brief`: when 6+ domains covered with sufficient depth\n"
    "Quick Mode guideline: ~2-3 interview turns, then draft. "
    "If the user's first answer covers 5+ domains, you may draft after 1-2 turns.\n\n"
    "Turn budget: {remaining_turns} turns remaining. "
    "When 0 turns remain, you MUST use `draft_design_brief`."
),
```

### 4. `backend/tests/test_intake.py` (MODIFY)

~15 of ~80 unit tests need updating:
- **Tool definition tests**: Check for `interview_client` and `draft_design_brief` names/schemas
- **`extract_tool_calls` tests**: Rename to `extract_skill_call`, update mock response blocks
- **`_run_intake_core` mocked tests**: Update mock responses to use new tool names
- **System prompt tests**: Update assertions for new OUTPUT FORMAT text

~65 tests remain unchanged (brief building, message building, input validation, error handling, etc.)

### 5. Integration/eval tests (VERIFY — likely unchanged)

- `backend/tests/eval/test_golden.py` — Tests `IntakeChatOutput` shape, not internal tool names. Should pass.
- `backend/tests/eval/test_full_mode.py`, `test_open_mode.py` — Same reasoning.
- `backend/tests/eval/scenarios.py`, `dataset.py` — Test brief quality, unchanged.
- `backend/app/activities/intake_eval.py` — Evaluates DesignBrief quality, independent of tools.

---

## Future Extensibility: Style-Specific Skills

When ready to add style-specific draft skills:

1. Create prompt files: `backend/prompts/styles/scandinavian.txt`, `mid_century.txt`, etc.
2. Each contains style-specific: translation mappings, material palette, color rules, furniture recommendations
3. Add to skill registry:
   ```python
   DRAFT_STYLES = {
       "scandinavian": {"prompt": "prompts/styles/scandinavian.txt", "tool_schema": ...},
       "mid_century": {"prompt": "prompts/styles/mid_century.txt", "tool_schema": ...},
   }
   ```
4. Server-side prefiltering: after interview phase, detect style from `partial_brief.style_profile.mood` → load only relevant style skill(s)
5. Claude picks the specific style tool → style-optimized brief

This scales to 10+ styles without token bloat because only 2-3 relevant skills are loaded per turn.

---

## Verification

```bash
cd backend

# 1. Lint + format
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .

# 2. Type check
.venv/bin/python -m mypy app/

# 3. Unit tests (non-integration)
.venv/bin/python -m pytest -x -q

# 4. Integration tests (needs ANTHROPIC_API_KEY)
.venv/bin/python -m pytest tests/eval/test_golden.py -x -v -m integration -s

# 5. Eval calibration (judge still calibrated)
.venv/bin/python -m pytest tests/eval/test_calibration.py -x -v -m integration

# 6. Verify skill selection behavior manually:
#    Run a quick-mode conversation and check that:
#    - Turns 1-2: agent calls interview_client (is_summary=False)
#    - Turn 3: agent calls draft_design_brief (is_summary=True)
#    - partial_brief accumulates across interview turns
```

---

## Summary of Changes

| What | Change | Impact |
|------|--------|--------|
| `intake_system.txt` | Update OUTPUT FORMAT → skill selection instructions | Prompt behavior |
| `intake.py` INTAKE_TOOLS | Replace 2 always-both tools → 2 mutually exclusive skill tools | Tool definitions |
| `intake.py` extract_tool_calls | → `extract_skill_call` (returns skill name + data) | Internal function |
| `intake.py` _run_intake_core | Branch on skill name instead of extracting both tools | Core logic |
| `intake.py` MODE_INSTRUCTIONS | Add skill selection guidance to each mode | Prompt injection |
| `test_intake.py` | ~15 tests updated for new tool names/schemas | Unit tests |
| Integration tests | Verify pass without modification | Regression check |
