# Architecture Evolution Phase 1 — Implementation Blueprint

## Context

Remo V1 is a single-room, linear workflow pipeline optimized for decoration. This plan adds:
- **Design expertise as loadable skills** — dynamic style knowledge packs with full loading contract
- **Feasibility-as-guidance** — intelligent renovation flags (not construction validation)
- **Cost estimation** — materials + labor + professional fees in shopping pipeline
- **Enhanced spatial model** — structured LiDAR data for design intelligence (Phase 1b)

All changes are **additive and backward-compatible**. No workflow steps change. No breaking contract modifications.

---

## Phasing Strategy

**Phase 1a ships first** — skill loading + cost/feasibility contracts. This is the lower-hanging fruit and unblocks T3's intake agent work immediately.

**Phase 1b follows** — spatial model (structured walls, LiDAR parser enhancement). Higher effort, lower urgency since it depends on T3's `infer_spatial_features` activity which is further out.

---

## Phase 1a: Skills + Cost/Feasibility

### Execution Overview

| PR | Scope | Owner | Lines | Depends on |
|----|-------|-------|-------|------------|
| **PR 1a** | Skill + cost/feasibility contracts + tests | T0 | ~280 | None |
| **PR 2a** | Mock stubs (skill loading + cost) + DB migration | T0 | ~100 | PR 1a |
| **PR 3a** | iOS Swift mirrors + UI scaffolding | T1 | ~300 | PR 1a |

PRs 2a and 3a are independent (parallel after PR 1a merges).

```
PR 1a (contracts) ──┬── PR 2a (mock stubs + DB)
                    └── PR 3a (iOS mirrors + UI)
```

---

### PR 1a: Skill + Cost/Feasibility Contracts (~280 lines)

#### File: `backend/app/models/contracts.py`

##### New models — Skill Loading Harness

Insert after `InspirationNote` (line 28), before `DesignBrief`:

```python
# === Skill System ===

class SkillSummary(BaseModel):
    """Lightweight skill reference for manifests and listings."""
    skill_id: str
    name: str
    description: str
    style_tags: list[str] = []


class StyleSkillPack(BaseModel):
    """Full style knowledge pack loaded during intake.

    T3 owns the `knowledge` dict structure — the contract defines
    the envelope (identity, metadata, versioning) while leaving
    the actual knowledge payload open for T3 to iterate on.
    """
    skill_id: str
    name: str
    description: str
    version: int = Field(ge=1, default=1)
    style_tags: list[str] = []
    applicable_room_types: list[str] = []  # empty = all rooms
    knowledge: dict = {}                    # T3-defined: prompts, examples, references


class SkillManifest(BaseModel):
    """Available skills for a given project context."""
    skills: list[SkillSummary] = []
    default_skill_ids: list[str] = []      # auto-loaded for every project
```

##### New models — Cost/Feasibility Types

```python
# === Renovation & Cost Types ===

class FeasibilityNote(BaseModel):
    """Assessment of a specific renovation intervention."""
    intervention: str
    assessment: Literal["likely_feasible", "needs_verification", "risky", "not_feasible"]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    cost_impact: str | None = None          # "adds $2-5K for plumbing reroute"
    professional_needed: str | None = None  # "licensed plumber", "structural engineer"


class ProfessionalFee(BaseModel):
    """Estimated cost for a professional service."""
    professional_type: str   # "structural engineer", "licensed plumber"
    reason: str              # "Load-bearing wall assessment"
    estimate_cents: int = Field(ge=0)


class CostBreakdown(BaseModel):
    """Detailed project cost breakdown (materials + labor + fees)."""
    materials_cents: int = Field(ge=0, default=0)
    labor_estimate_cents: int | None = None       # None if cosmetic-only
    labor_estimate_note: str | None = None
    professional_fees: list[ProfessionalFee] = []
    permit_fees_estimate_cents: int | None = None
    total_low_cents: int = Field(ge=0, default=0)
    total_high_cents: int = Field(ge=0, default=0)


class RenovationIntent(BaseModel):
    """User's renovation scope and feasibility analysis."""
    scope: Literal["cosmetic", "moderate", "structural"]
    interventions: list[str] = []
    feasibility_notes: list[FeasibilityNote] = []
    estimated_permits: list[str] = []
```

##### New models — Skill Loading Activity Contracts

Insert alongside existing activity I/O models:

```python
class LoadSkillInput(BaseModel):
    """Activity input for loading skill packs from R2."""
    skill_ids: list[str] = Field(min_length=1)


class LoadSkillOutput(BaseModel):
    """Activity output with loaded skill packs."""
    skill_packs: list[StyleSkillPack] = []
    not_found: list[str] = []              # skill IDs that couldn't be loaded
```

##### Modified models — additive fields only

**`DesignBrief`** — add 2 fields after `inspiration_notes`:
```python
    style_skills_used: list[str] = []                  # skill_ids loaded during intake
    renovation_intent: RenovationIntent | None = None   # populated by intake agent
```

**`IntakeChatInput`** — add 1 field after `user_message`:
```python
    available_skills: list[SkillSummary] = []  # skills available for loading
```

**`GenerateShoppingListOutput`** — add 1 field after `total_estimated_cost_cents`:
```python
    cost_breakdown: CostBreakdown | None = None
```

##### Design decisions

| Decision | Rationale |
|----------|-----------|
| `knowledge: dict` (open) | T3 owns the internal structure. They can iterate on prompt formats, example schemas, etc. without contract changes. T0 defines the envelope. |
| `SkillSummary` separate from `StyleSkillPack` | Manifests transmit lightweight references. Full packs are loaded on-demand by the intake agent. Avoids sending all knowledge over the wire for listing. |
| `available_skills: list[SkillSummary]` (not `list[str]`) | The intake agent needs name + description to decide which skills to load, not just IDs. |
| `LoadSkillInput.skill_ids` min_length=1 | Loading zero skills is a no-op — catch it at the contract level. |
| `not_found` in `LoadSkillOutput` | Graceful degradation: intake continues with whatever skills loaded. Missing skills are logged, not errors. |
| `version: int` on `StyleSkillPack` | Cache invalidation for R2-stored packs. Worker can skip re-loading if version matches cached. |
| `Literal` for `assessment`, `scope` | Closed sets driving UI rendering (icons, colors). Consistent with `PhotoData.photo_type` pattern. |
| `str` for `cost_impact`, `professional_needed` | Rich user-facing text: "$2-5K for plumbing reroute". Better than enums for homeowners. |

#### File: `backend/tests/test_contracts.py`

##### Add to `expected` list:
```python
    "SkillSummary",
    "StyleSkillPack",
    "SkillManifest",
    "FeasibilityNote",
    "ProfessionalFee",
    "CostBreakdown",
    "RenovationIntent",
    "LoadSkillInput",
    "LoadSkillOutput",
```

##### New test classes (~100 lines):

**Skill models:**
- `TestSkillSummary` — valid construction, style_tags default empty
- `TestStyleSkillPack` — valid with knowledge dict, version >= 1, version 0 fails
- `TestSkillManifest` — empty valid, with skills and defaults
- `TestLoadSkillInput` — valid with 1+ IDs, empty list fails (min_length=1)
- `TestLoadSkillOutput` — empty valid (all defaults), with packs and not_found

**Cost/feasibility models:**
- `TestFeasibilityNote` — valid construction, invalid assessment rejected
- `TestCostBreakdown` — minimal (all defaults), full with ProfessionalFee, negative total fails
- `TestRenovationIntent` — cosmetic/moderate/structural valid, "nuclear" rejected

**Evolution (backward compat):**
- `TestDesignBriefEvolution` — existing minimal works (new fields default), with renovation_intent and style_skills_used
- `TestShoppingListOutputEvolution` — cost_breakdown optional, with CostBreakdown
- `TestIntakeChatInputEvolution` — available_skills defaults to []
- `TestEvolutionRoundTrips` — JSON serialize/deserialize for DesignBrief, ShoppingListOutput with all new fields
- `TestForwardCompatibility` — old JSON (without new fields) still deserializes for all 3 modified models

#### Verification
```bash
cd backend
.venv/bin/python -m pytest tests/test_contracts.py -x -q
.venv/bin/python -m pytest -x -q                           # full suite (301+ tests pass)
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy app/
```

---

### PR 2a: Mock Stubs + DB Migration (~100 lines)

#### File: `backend/app/activities/mock_stubs.py`

Add imports for new types. Add two new mock activities:

```python
@activity.defn
async def load_style_skill(input: LoadSkillInput) -> LoadSkillOutput:
    """Mock skill loader — returns sample skill packs."""
    packs = []
    not_found = []
    for skill_id in input.skill_ids:
        if skill_id in _MOCK_SKILLS:
            packs.append(_MOCK_SKILLS[skill_id])
        else:
            not_found.append(skill_id)
    return LoadSkillOutput(skill_packs=packs, not_found=not_found)

_MOCK_SKILLS = {
    "mid-century-modern": StyleSkillPack(
        skill_id="mid-century-modern",
        name="Mid-Century Modern",
        description="Clean lines, organic curves, and a love of different materials",
        style_tags=["retro", "organic", "minimal"],
        knowledge={"principles": ["form follows function", "less is more"]},
    ),
    "japandi": StyleSkillPack(
        skill_id="japandi",
        name="Japandi",
        description="Japanese minimalism meets Scandinavian warmth",
        style_tags=["minimal", "natural", "warm"],
        knowledge={"principles": ["wabi-sabi", "hygge", "natural materials"]},
    ),
}
```

Enhance existing `generate_shopping_list` mock to return a sample `CostBreakdown`:
```python
cost_breakdown=CostBreakdown(
    materials_cents=9999,
    labor_estimate_cents=None,
    total_low_cents=9999,
    total_high_cents=12000,
)
```

#### File: `backend/app/models/db.py`

Add to `ShoppingList` class:
```python
cost_breakdown: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
```

#### File: `backend/migrations/versions/002_add_cost_breakdown.py` (NEW)

Simple additive migration — one nullable JSONB column on `shopping_lists`.

Note: `DesignBriefRow.brief_data` is already JSONB storing the full DesignBrief dict. New fields (`style_skills_used`, `renovation_intent`) serialize automatically. No migration needed for those.

#### Verification
```bash
cd backend
.venv/bin/python -m pytest tests/test_db_models.py -xvs
.venv/bin/python -m pytest tests/test_workflow.py -xvs
.venv/bin/python -m pytest -x -q
```

---

### PR 3a: iOS Swift Mirrors + UI Scaffolding (~300 lines, T1 owned)

#### File: `ios/Packages/RemoModels/Sources/RemoModels/Models.swift`

##### 2 new typed enums:
- `FeasibilityAssessment` — `.likelyFeasible`, `.needsVerification`, `.risky`, `.notFeasible`
- `RenovationScope` — `.cosmetic`, `.moderate`, `.structural`

##### 8 new structs (all `Codable, Hashable, Sendable` with CodingKeys):
- `SkillSummary` — 4 fields
- `StyleSkillPack` — 7 fields (knowledge as `[String: AnyCodable]`)
- `SkillManifest` — 2 fields
- `FeasibilityNote` — 6 fields + computed `assessmentEnum`
- `RenovationIntent` — 4 fields + computed `scopeEnum`
- `ProfessionalFee` — 3 fields
- `CostBreakdown` — 7 fields

##### 1 modified struct (custom decoder for backward compat):

**`DesignBrief`** — add `styleSkillsUsed: [String]`, `renovationIntent: RenovationIntent?`
- Custom decoder: `styleSkillsUsed` is non-optional array, needs `decodeIfPresent ?? []`

**`ShoppingListOutput`** — add `costBreakdown: CostBreakdown?`
- No custom decoder needed: `Optional` auto-synthesizes `decodeIfPresent`

#### File: `ios/Packages/RemoNetworking/Sources/RemoNetworking/MockWorkflowClient.swift`

- `approve`: add sample `CostBreakdown` to mock shopping list
- Intake summary: add sample `RenovationIntent` with `FeasibilityNote` to `partialBrief`
- Add `styleSkillsUsed: ["mid-century-modern"]` to mock brief

#### File: `ios/Packages/RemoModels/Tests/RemoModelsTests/ModelsTests.swift`

~15 new tests:
- Decode tests for each new model (6: SkillSummary, StyleSkillPack, FeasibilityNote, RenovationIntent, ProfessionalFee, CostBreakdown)
- Forward-compat: DesignBrief/ShoppingListOutput without new fields (2)
- Round-trip encode/decode for RenovationIntent, CostBreakdown, StyleSkillPack (3)
- Typed enum accessor tests with unknown values (2: FeasibilityAssessment, RenovationScope)
- DesignBrief with renovation_intent round-trip (1)
- SkillManifest empty + populated (1)

#### UI scaffolding (minimal, renders when data present):

**`ShoppingListScreen.swift`** — `CostBreakdownSection` view: materials, labor, professional fees, permits, total range. Uses existing `formatPrice()` helper. Only shown when `shoppingList.costBreakdown` is non-nil.

**`IntakeChatScreen.swift`** — Feasibility notes in `SummaryCard`: renovation scope label, per-note icon + color (green/orange/red) + explanation text. Only shown when `brief.renovationIntent` is non-nil.

---

## Phase 1b: Spatial Model (deferred)

Ships after Phase 1a. Higher effort, depends on T3's `infer_spatial_features` activity.

| PR | Scope | Owner | Lines | Depends on |
|----|-------|-------|-------|------------|
| **PR 1b** | Spatial contracts (WallSegment, OpeningDetail, InferredFeature) + RoomDimensions fields + tests | T0 | ~150 | Phase 1a merged |
| **PR 2b** | LiDAR parser enhancement + parser tests | T0 | ~200 | PR 1b |
| **PR 3b** | iOS spatial model mirrors + tests | T1 | ~200 | PR 1b |

```
PR 1b (spatial contracts) ──┬── PR 2b (LiDAR parser)
                            └── PR 3b (iOS mirrors)
```

### PR 1b: Spatial Contracts (~150 lines)

#### File: `backend/app/models/contracts.py`

```python
# === Spatial Types ===

class WallSegment(BaseModel):
    """Structured wall data from RoomPlan LiDAR scan."""
    wall_id: str
    start_x: float           # normalized 0-1 plan coordinates
    start_y: float
    end_x: float
    end_y: float
    length_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    is_exterior: bool | None = None       # None = unknown
    load_bearing_confidence: float = Field(ge=0, le=1, default=0.0)
    has_plumbing: bool | None = None
    has_electrical: bool | None = None
    notes: list[str] = []


class OpeningDetail(BaseModel):
    """Structured opening (door/window) from RoomPlan."""
    opening_id: str
    wall_id: str
    type: Literal["door", "window", "open", "archway"]
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    sill_height_m: float | None = None


class InferredFeature(BaseModel):
    """AI-inferred spatial feature from progressive confidence pipeline."""
    feature_type: str                     # open — AI discovers new types
    description: str
    confidence: float = Field(ge=0, le=1)
    source: Literal["lidar", "photo_analysis", "user_reported", "building_plan"]
    wall_id: str | None = None
    notes: str | None = None
```

**`RoomDimensions`** — add 4 fields after `openings`:
```python
    structured_walls: list[WallSegment] = []
    structured_openings: list[OpeningDetail] = []
    floor_area_sqm: float | None = None
    inferred_features: list[InferredFeature] = []
```

Old `walls: list[dict]` preserved alongside `structured_walls` for backward compat.

Tests: `TestWallSegment`, `TestOpeningDetail`, `TestInferredFeature`, `TestRoomDimensionsEvolution`, round-trips.

### PR 2b: LiDAR Parser Enhancement (~200 lines)

#### File: `backend/app/utils/lidar.py`

- Add helper: `_parse_structured_walls(raw_walls, default_height) -> list[WallSegment]`
- Add helper: `_parse_structured_openings(raw_openings) -> list[OpeningDetail]`
- Modify `parse_room_dimensions` to call helpers, compute `floor_area_sqm`, pass structured fields to constructor

#### File: `backend/tests/test_lidar.py`

~10 new tests in `TestStructuredParsing`:
- Structured walls/openings extracted from valid JSON
- Floor area from JSON vs computed fallback
- Graceful handling of malformed walls, unknown opening types
- Backward compat: untyped `result.walls` unchanged

### PR 3b: iOS Spatial Mirrors (~200 lines)

- 3 new enums: `OpeningType`, `InferredFeatureType`, `InferenceSource`
- 3 new structs: `WallSegment`, `OpeningDetail`, `InferredFeature`
- Modified `RoomDimensions` with custom decoder (3 non-optional arrays)
- MockWorkflowClient `uploadScan` enhanced with sample structured data
- ~10 new tests

---

## What Does NOT Change

- **Workflow step sequence**: photos -> scan -> intake -> generation -> selection -> iteration -> approval -> shopping -> done
- **Workflow code** (`design_project.py`): No changes. DesignBrief flows through automatically.
- **Temporal architecture**: One workflow per project. Signals + query unchanged.
- **Error handling**: Same `WorkflowError` pattern. Feasibility issues are content, not errors.
- **Start_over**: Already clears `design_brief` (which includes `renovation_intent`). Preserves photos + scan.
- **iOS polling**: `GET /projects/{id}` returns `WorkflowState`. New nested fields appear automatically.

---

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Old JSON missing new fields | Certain (existing data) | All new fields have defaults. Forward-compat tests verify. |
| iOS custom decoders miss a field | Low | Every field in custom decoder tested; round-trip tests catch gaps. |
| T3 needs to change `knowledge` dict schema | Expected | `knowledge: dict` is intentionally open — no contract change needed. |
| Skill pack versioning conflicts | Low | `version` field + R2 immutable keys. Cache invalidation is explicit. |
| `Literal` rejects future opening types | Low (Phase 1b) | LiDAR parser defaults unknown types to `"open"`. |

---

## Phase 2 Dependencies (tracked, not in scope)

1. **`infer_spatial_features` activity** (T3) — Claude Vision analyzes photos + LiDAR -> `InferredFeature` list
2. **`extract_from_building_plans` activity** (T3) — plan upload + Claude Vision reads blueprints
3. **Skill pack content** in R2 (`skills/` prefix) — T3 creates style + feasibility knowledge packs
4. **`load_style_skill` wired into intake agent** — T3 implements within `run_intake_chat` activity
5. **Cost estimation logic** in shopping activity — T3 populates `CostBreakdown` from brief + shopping results
