"""Remo contract models — FROZEN at P0 exit gate.

T0 owns this file exclusively. All teams depend on these models.
After freeze, only additive (new optional fields) changes are fast-tracked.
Breaking changes require formal process with all consuming teams.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# === Shared Types ===


class StyleProfile(BaseModel):
    lighting: str | None = None
    colors: list[str] = []
    textures: list[str] = []
    clutter_level: str | None = None
    mood: str | None = None


class InspirationNote(BaseModel):
    photo_index: int
    note: str
    agent_clarification: str | None = None


# === Skill System ===


class SkillSummary(BaseModel):
    """Lightweight skill reference for manifests and listings."""

    skill_id: str
    name: str
    description: str
    style_tags: list[str] = []
    trigger_phrases: list[str] = []


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
    knowledge: dict = {}  # T3-defined: prompts, examples, references


class SkillManifest(BaseModel):
    """Available skills for a given project context."""

    skills: list[SkillSummary] = []
    default_skill_ids: list[str] = []  # auto-loaded for every project


# === Renovation & Cost Types ===


class FeasibilityNote(BaseModel):
    """Assessment of a specific renovation intervention."""

    intervention: str
    assessment: Literal["likely_feasible", "needs_verification", "risky", "not_feasible"]
    confidence: float = Field(ge=0, le=1)
    explanation: str
    cost_impact: str | None = None  # "adds $2-5K for plumbing reroute"
    professional_needed: str | None = None  # "licensed plumber"


class ProfessionalFee(BaseModel):
    """Estimated cost for a professional service."""

    professional_type: str  # "structural engineer", "licensed plumber"
    reason: str  # "Load-bearing wall assessment"
    estimate_cents: int = Field(ge=0)


class CostBreakdown(BaseModel):
    """Detailed project cost breakdown (materials + labor + fees)."""

    materials_cents: int = Field(ge=0, default=0)
    labor_estimate_cents: int | None = None  # None if cosmetic-only
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


class DesignBrief(BaseModel):
    room_type: str
    occupants: str | None = None
    lifestyle: str | None = None
    pain_points: list[str] = []
    keep_items: list[str] = []
    style_profile: StyleProfile | None = None
    constraints: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    style_skills_used: list[str] = []  # skill_ids loaded during intake
    renovation_intent: RenovationIntent | None = None  # populated by intake agent
    # Designer Brain enhancement (additive, Phase 1a)
    emotional_drivers: list[str] = []  # "started WFH, room feels oppressive"
    usage_patterns: str | None = None  # "couple WFH Mon-Fri, host dinners monthly"
    renovation_willingness: str | None = None  # "repaint yes, replace flooring no"
    room_analysis_hypothesis: str | None = None  # preserved from photo analysis


class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []
    openings: list[dict] = []
    furniture: list[dict] = []
    surfaces: list[dict] = []
    floor_area_sqm: float | None = None


class AnnotationRegion(BaseModel):
    region_id: int = Field(ge=1, le=3)
    center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)
    radius: float = Field(ge=0, le=1)
    instruction: str = Field(min_length=10)
    action: str | None = None  # Replace, Remove, Change finish, Resize, Reposition
    avoid: list[str] = []
    constraints: list[str] = []


class DesignOption(BaseModel):
    image_url: str
    caption: str


class ProductMatch(BaseModel):
    category_group: str
    product_name: str
    retailer: str
    price_cents: int = Field(ge=0)
    product_url: str
    image_url: str | None = None
    confidence_score: float = Field(ge=0, le=1)
    why_matched: str
    fit_status: str | None = None
    fit_detail: str | None = None
    dimensions: str | None = None


class UnmatchedItem(BaseModel):
    category: str
    search_keywords: str
    google_shopping_url: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class QuickReplyOption(BaseModel):
    number: int
    label: str
    value: str


class WorkflowError(BaseModel):
    message: str
    retryable: bool


class RevisionRecord(BaseModel):
    revision_number: int
    type: str
    base_image_url: str
    revised_image_url: str
    instructions: list[str] = []


# === Photo Data ===


class PhotoData(BaseModel):
    photo_id: str
    storage_key: str
    photo_type: Literal["room", "inspiration"]
    note: str | None = None


class ScanData(BaseModel):
    storage_key: str
    room_dimensions: RoomDimensions | None = None


# === Room Analysis (Designer Brain) ===


class LightingAssessment(BaseModel):
    """Structured lighting observations from room photos."""

    natural_light_direction: str | None = None  # "south-facing windows"
    natural_light_intensity: str | None = None  # "abundant" / "moderate" / "limited"
    window_coverage: str | None = None  # "full wall" / "single window"
    existing_artificial: str | None = None  # "single overhead" / "layered"
    lighting_gaps: list[str] = []  # ["dark reading corner", "no task lighting"]


class FurnitureObservation(BaseModel):
    """Single piece of furniture observed in room photos."""

    item: str  # "L-shaped gray sectional"
    condition: str | None = None  # "good" / "worn" / "dated"
    placement_note: str | None = None  # "faces wall instead of window"
    keep_candidate: bool = False  # designer thinks worth keeping


class BehavioralSignal(BaseModel):
    """Inference about lifestyle from room evidence."""

    observation: str  # "books stacked on floor near armchair"
    inference: str  # "active reader lacking storage"
    design_implication: str | None = None  # "add reading nook with task lighting"


class RoomAnalysis(BaseModel):
    """Pre-intake photo analysis — the designer's first 5 minutes of observation."""

    # Identity & space
    room_type: str | None = None
    room_type_confidence: float = Field(ge=0, le=1, default=0.5)
    estimated_dimensions: str | None = None  # "approximately 12x15 feet"
    layout_pattern: str | None = None  # "open plan" / "L-shaped"

    # Observations
    lighting: LightingAssessment | None = None
    furniture: list[FurnitureObservation] = []
    architectural_features: list[str] = []  # ["crown molding", "bay window"]
    flooring: str | None = None  # "hardwood, good condition"
    existing_palette: list[str] = []  # ["cool gray walls", "warm oak floors"]
    overall_warmth: str | None = None  # "cool" / "warm" / "neutral" / "mixed"
    circulation_issues: list[str] = []  # ["path to window blocked by ottoman"]

    # Inferences
    style_signals: list[str] = []  # ["mid-century legs", "warm neutral palette"]
    behavioral_signals: list[BehavioralSignal] = []
    tensions: list[str] = []  # ["beautiful moldings with flat-pack furniture"]

    # Synthesis
    hypothesis: str | None = None  # "lived-in family room, good bones, poor lighting"
    strengths: list[str] = []
    opportunities: list[str] = []
    uncertain_aspects: list[str] = []  # ["lighting warmer than photos suggest"]

    # Meta
    photo_count: int = 0


class RoomContext(BaseModel):
    """Progressive room understanding that enriches over time."""

    photo_analysis: RoomAnalysis | None = None
    room_dimensions: RoomDimensions | None = None
    enrichment_sources: list[str] = []  # ["photos", "lidar"]


# === Activity Input/Output ===


class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None
    room_context: RoomContext | None = None


class GenerateDesignsOutput(BaseModel):
    options: list[DesignOption] = Field(min_length=2, max_length=2)


class EditDesignInput(BaseModel):
    project_id: str
    base_image_url: str
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    design_brief: DesignBrief | None = None
    annotations: list[AnnotationRegion] = []
    feedback: str | None = None
    chat_history_key: str | None = None
    room_dimensions: RoomDimensions | None = None
    room_context: RoomContext | None = None


class EditDesignOutput(BaseModel):
    revised_image_url: str
    chat_history_key: str


class GenerateShoppingListInput(BaseModel):
    design_image_url: str
    original_room_photo_urls: list[str]
    design_brief: DesignBrief | None = None
    revision_history: list[RevisionRecord] = []
    room_dimensions: RoomDimensions | None = None
    room_context: RoomContext | None = None


class GenerateShoppingListOutput(BaseModel):
    items: list[ProductMatch]
    unmatched: list[UnmatchedItem] = []
    total_estimated_cost_cents: int = Field(ge=0)
    cost_breakdown: CostBreakdown | None = None


class IntakeChatInput(BaseModel):
    mode: Literal["quick", "full", "open"]
    project_context: dict
    conversation_history: list[ChatMessage]
    user_message: str
    available_skills: list[SkillSummary] = []  # skills available for loading


class IntakeChatOutput(BaseModel):
    agent_message: str
    options: list[QuickReplyOption] | None = None
    is_open_ended: bool = False
    progress: str | None = None
    is_summary: bool = False
    partial_brief: DesignBrief | None = None
    requested_skills: list[str] = []  # skill IDs agent wants loaded next turn


class LoadSkillInput(BaseModel):
    """Activity input for loading skill packs from R2."""

    skill_ids: list[str] = Field(min_length=1)


class LoadSkillOutput(BaseModel):
    """Activity output with loaded skill packs."""

    skill_packs: list[StyleSkillPack] = []
    not_found: list[str] = []  # skill IDs that couldn't be loaded


class AnalyzeRoomPhotosInput(BaseModel):
    """Input for the read_the_room photo analysis activity."""

    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []


class AnalyzeRoomPhotosOutput(BaseModel):
    """Output from the read_the_room photo analysis activity."""

    analysis: RoomAnalysis


class ValidatePhotoInput(BaseModel):
    image_data: bytes
    photo_type: Literal["room", "inspiration"]


class ValidatePhotoOutput(BaseModel):
    passed: bool
    failures: list[str]
    messages: list[str]


# === Workflow State (returned by query) ===


class WorkflowState(BaseModel):
    step: str
    photos: list[PhotoData] = []
    scan_data: ScanData | None = None
    design_brief: DesignBrief | None = None
    generated_options: list[DesignOption] = []
    selected_option: int | None = None
    current_image: str | None = None
    revision_history: list[RevisionRecord] = []
    iteration_count: int = 0
    shopping_list: GenerateShoppingListOutput | None = None
    approved: bool = False
    error: WorkflowError | None = None
    chat_history_key: str | None = None
    # Designer Brain enhancement (additive, Phase 1a)
    room_analysis: RoomAnalysis | None = None
    room_context: RoomContext | None = None


# === API Request/Response Models ===


class CreateProjectRequest(BaseModel):
    device_fingerprint: str
    has_lidar: bool = False


class CreateProjectResponse(BaseModel):
    project_id: str


class PhotoUploadResponse(BaseModel):
    photo_id: str
    validation: ValidatePhotoOutput


class IntakeStartRequest(BaseModel):
    mode: Literal["quick", "full", "open"]


class IntakeMessageRequest(BaseModel):
    message: str
    conversation_history: list[ChatMessage] = Field(default=[], max_length=20)
    mode: Literal["quick", "full", "open"] | None = None


class IntakeConfirmRequest(BaseModel):
    brief: DesignBrief


class SelectOptionRequest(BaseModel):
    index: int = Field(ge=0, le=1)


class AnnotationEditRequest(BaseModel):
    annotations: list[AnnotationRegion] = Field(min_length=1, max_length=3)


class TextFeedbackRequest(BaseModel):
    feedback: str = Field(min_length=10, max_length=2000)


class ActionResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    error: str
    message: str
    retryable: bool
    detail: str | None = None


# === Tile Mode (Material Swap) ===
#
# Parallel path from the intake/redesign flow. Keep layout, swap material,
# output count + photoreal render + cut-sheet. Shared primitives — any future
# material-estimation flow (hardwood planks, drywall, paint, wallpaper) should
# consume SurfacePatch + MaterialSpec too.


class Vec2(BaseModel):
    x: float
    y: float


class HoleKind(StrEnum):
    """Type of surface-local region to exclude from tile packing."""

    OPENING = "opening"  # door or window — excluded from tile count
    MASK = "mask"  # user-drawn region (niche, tub surround, vanity)


class Hole(BaseModel):
    """A rectangular, axis-aligned region within a SurfacePatch that should
    not be tiled. Used for openings (doors/windows) and user masks.
    """

    x_m: float
    y_m: float
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    label: str = ""
    kind: HoleKind


class SurfacePatch(BaseModel):
    """A planar surface region for material estimation.

    Coordinates in `polygon`/`holes`/`axis`/`origin` are surface-local 2D meters
    (the patch has been unfolded into its own plane). `normal_*` gives the
    world-space orientation used when projecting the patch back into a camera
    frame for the mask-edit pipeline.
    """

    patch_id: str
    kind: Literal["wall", "floor", "ceiling"]
    polygon: list[Vec2] = Field(min_length=3)
    holes: list[Hole] = []
    axis: Vec2
    origin: Vec2
    normal_x: float
    normal_y: float
    normal_z: float


class MaterialSpec(BaseModel):
    """Unit-of-sale descriptor for a tile / plank / slab / roll."""

    material_id: str
    module_width_m: float = Field(gt=0)
    module_height_m: float = Field(gt=0)
    thickness_mm: float | None = None
    unit_of_sale: Literal["each", "box", "sqft", "sqm"] = "box"
    modules_per_unit: int = Field(ge=1, default=1)
    grout_width_mm: float = Field(ge=0, default=3.0)
    pattern_repeat_m: float | None = None
    orientation_rule: Literal["any", "long_axis_horizontal", "long_axis_vertical"] = "any"
    price_per_box_cents: int | None = None
    reference_image_url: str | None = None


class OveragePolicy(StrEnum):
    FLAT_10 = "flat_10"
    RISK_ADJUSTED = "risk_adjusted"
    CONTRACTOR_TIER = "contractor_tier"


class ContractorTier(StrEnum):
    APPRENTICE = "apprentice"  # +15% — less experienced, more waste
    PRO = "pro"  # +12% — default; experienced contractor
    PERFECTIONIST = "perfectionist"  # +18% — demanding fit, more selective cuts


class StartingPointRule(StrEnum):
    CENTERED_FOCAL_WALL = "centered_focal_wall"
    LARGEST_WALL_CORNER = "largest_wall_corner"
    MINIMIZE_CUT_COUNT = "minimize_cut_count"


class TileRect(BaseModel):
    """One tile placed on a surface, in surface-local meters.

    Produced by pack_surface. Used both for PDF cut-sheet rendering and as the
    exact geometry iOS uses for its live-toggle SVG (Swift port of the same
    algorithm — parity tested).
    """

    x_m: float
    y_m: float
    width_m: float = Field(gt=0)
    height_m: float = Field(gt=0)
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    is_cut: bool


class PackResult(BaseModel):
    surface_id: str
    full_modules: int = Field(ge=0)
    cut_modules: int = Field(ge=0)
    tiles_required: int = Field(ge=0)  # full + cut (no offcut reuse in PR 1)
    rects: list[TileRect] = []
    covered_area_m2: float = Field(ge=0)
    waste_area_m2: float = Field(ge=0)
    start_x_m: float = 0.0
    start_y_m: float = 0.0


class TileModeInput(BaseModel):
    surfaces: list[SurfacePatch] = Field(min_length=1)
    wall_material: MaterialSpec
    floor_material: MaterialSpec
    overage_policy: OveragePolicy = OveragePolicy.FLAT_10
    contractor_tier: ContractorTier = ContractorTier.PRO
    starting_point_rule: StartingPointRule = StartingPointRule.CENTERED_FOCAL_WALL


class TileModeEstimate(BaseModel):
    wall_material: MaterialSpec
    floor_material: MaterialSpec
    wall_packs: list[PackResult] = []
    floor_packs: list[PackResult] = []
    wall_tiles_total: int = Field(ge=0)
    floor_tiles_total: int = Field(ge=0)
    wall_boxes_total: int = Field(ge=0)
    floor_boxes_total: int = Field(ge=0)
    overage_policy: OveragePolicy
    contractor_tier: ContractorTier | None = None
    starting_point_rule: StartingPointRule


# --- Tile-mode API request/response shapes ---


class TileSpecInput(BaseModel):
    """Designer's TileSpec (src/state.jsx) — cm-native for iOS form ergonomics.

    The API converts to a MaterialSpec (meters) before signaling the workflow.
    """

    width_cm: float = Field(gt=0)
    height_cm: float = Field(gt=0)
    grout_mm: float = Field(ge=0, default=3.0)
    per_box: int = Field(ge=1)
    price_per_box: float | None = None


class CreateTileProjectRequest(BaseModel):
    device_fingerprint: str


class SetTileSurfacesRequest(BaseModel):
    surfaces: list[SurfacePatch] = Field(min_length=1)


class SetTileMaterialsRequest(BaseModel):
    wall: TileSpecInput
    floor: TileSpecInput


class SetTilePoliciesRequest(BaseModel):
    overage_policy: OveragePolicy
    contractor_tier: ContractorTier | None = None
    starting_point_rule: StartingPointRule


# --- Tile-mode activity IO (stubs in PR 1; full impl in PR 5) ---


class RenderTileInput(BaseModel):
    project_id: str
    surfaces: list[SurfacePatch]
    wall_material: MaterialSpec
    floor_material: MaterialSpec
    reference_photo_url: str | None = None


class RenderTileOutput(BaseModel):
    image_url: str
    seed: int
    duration_seconds: float


class GenerateCutSheetInput(BaseModel):
    project_id: str
    estimate: TileModeEstimate
    surfaces: list[SurfacePatch]


class GenerateCutSheetOutput(BaseModel):
    pdf_url: str
    page_count: int


# --- Tile-mode workflow query state ---


class TileWorkflowState(BaseModel):
    """Shape returned by `GET /api/v1/tile-projects/{id}` via workflow query.

    Mirrors `WorkflowState` style but scoped to tile mode's fields.
    """

    step: str  # raw value of one of TILE_PROJECT_STEPS (+terminal states)
    surfaces: list[SurfacePatch] = []
    wall_material: MaterialSpec | None = None
    floor_material: MaterialSpec | None = None
    overage_policy: OveragePolicy = OveragePolicy.FLAT_10
    contractor_tier: ContractorTier | None = None
    starting_point_rule: StartingPointRule = StartingPointRule.CENTERED_FOCAL_WALL
    estimate: TileModeEstimate | None = None
    render_image_url: str | None = None
    cut_sheet_pdf_url: str | None = None
    error: WorkflowError | None = None


# --- ProjectStep raw values for tile-mode phases ---
# Extending the existing iOS ProjectStep enum (see
# ios/Packages/RemoModels/Sources/RemoModels/ProjectStep.swift) — PR 2 adds
# the Swift cases that map to these raw strings.

TILE_PROJECT_STEPS: list[str] = [
    "replace_material_scan",
    "replace_material_specs",
    "replace_material_estimate",
    "replace_material_render",
    "replace_material_export",
]
