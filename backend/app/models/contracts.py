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


class DesignBrief(BaseModel):
    room_type: str
    occupants: str | None = None
    pain_points: list[str] = []
    keep_items: list[str] = []
    style_profile: StyleProfile | None = None
    constraints: list[str] = []
    inspiration_notes: list[InspirationNote] = []


class RoomDimensions(BaseModel):
    width_m: float
    length_m: float
    height_m: float
    walls: list[dict] = []
    openings: list[dict] = []


class LassoRegion(BaseModel):
    region_id: int = Field(ge=1, le=3)
    path_points: list[tuple[float, float]]
    action: str
    instruction: str = Field(min_length=10)
    avoid_tokens: list[str] = []
    style_nudges: list[str] = []


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
    type: Literal["lasso", "regen"]
    base_image_url: str
    revised_image_url: str


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


class GenerateInpaintInput(BaseModel):
    base_image_url: str
    regions: list[LassoRegion] = Field(min_length=1, max_length=3)


class GenerateInpaintOutput(BaseModel):
    revised_image_url: str


class GenerateRegenInput(BaseModel):
    room_photo_urls: list[str]
    design_brief: DesignBrief | None = None
    current_image_url: str
    feedback: str
    revision_history: list[RevisionRecord] = []


class GenerateRegenOutput(BaseModel):
    revised_image_url: str


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


class IntakeChatInput(BaseModel):
    mode: Literal["quick", "full", "open"]
    project_context: dict
    conversation_history: list[ChatMessage]
    user_message: str


class IntakeChatOutput(BaseModel):
    agent_message: str
    options: list[QuickReplyOption] | None = None
    is_open_ended: bool = False
    progress: str | None = None
    is_summary: bool = False
    partial_brief: DesignBrief | None = None


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


class LassoEditRequest(BaseModel):
    regions: list[LassoRegion] = Field(min_length=1, max_length=3)


class RegenerateRequest(BaseModel):
    feedback: str


class ActionResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ErrorResponse(BaseModel):
    error: str
    message: str
    retryable: bool
    detail: str | None = None
