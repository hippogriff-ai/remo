"""Remo contract models — FROZEN at P0 exit gate.

T0 owns this file exclusively. All teams depend on these models.
After freeze, only additive (new optional fields) changes are fast-tracked.
Breaking changes require formal process with all consuming teams.
"""

from __future__ import annotations

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


class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []
    openings: list[dict] = []


class AnnotationRegion(BaseModel):
    region_id: int = Field(ge=1, le=3)
    center_x: float = Field(ge=0, le=1)
    center_y: float = Field(ge=0, le=1)
    radius: float = Field(ge=0, le=1)
    instruction: str = Field(min_length=10)


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


# === Activity Input/Output ===


class GenerateDesignsInput(BaseModel):
    room_photo_urls: list[str]
    inspiration_photo_urls: list[str] = []
    inspiration_notes: list[InspirationNote] = []
    design_brief: DesignBrief | None = None
    room_dimensions: RoomDimensions | None = None


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


class EditDesignOutput(BaseModel):
    revised_image_url: str
    chat_history_key: str


class GenerateShoppingListInput(BaseModel):
    design_image_url: str
    original_room_photo_urls: list[str]
    design_brief: DesignBrief | None = None
    revision_history: list[RevisionRecord] = []
    room_dimensions: RoomDimensions | None = None


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


class LoadSkillInput(BaseModel):
    """Activity input for loading skill packs from R2."""

    skill_ids: list[str] = Field(min_length=1)


class LoadSkillOutput(BaseModel):
    """Activity output with loaded skill packs."""

    skill_packs: list[StyleSkillPack] = []
    not_found: list[str] = []  # skill IDs that couldn't be loaded


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


class IntakeConfirmRequest(BaseModel):
    brief: DesignBrief


class SelectOptionRequest(BaseModel):
    index: int = Field(ge=0, le=1)


class AnnotationEditRequest(BaseModel):
    annotations: list[AnnotationRegion] = Field(min_length=1, max_length=3)


class TextFeedbackRequest(BaseModel):
    feedback: str = Field(min_length=10)


class ActionResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    error: str
    message: str
    retryable: bool
    detail: str | None = None
