"""Project API endpoints — thin proxy to Temporal workflow.

Mock mode: returns realistic stub data so T1 (iOS) can develop against it.
Real mode (P2): forwards signals/queries to Temporal.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from app.activities.validation import validate_photo
from app.config import settings
from app.models.contracts import (
    ActionResponse,
    AnnotationEditRequest,
    ChatMessage,
    CreateProjectRequest,
    CreateProjectResponse,
    DesignBrief,
    DesignOption,
    ErrorResponse,
    GenerateShoppingListOutput,
    IntakeChatInput,
    IntakeChatOutput,
    IntakeConfirmRequest,
    IntakeMessageRequest,
    IntakeStartRequest,
    PhotoData,
    PhotoUploadResponse,
    ProductMatch,
    QuickReplyOption,
    RevisionRecord,
    ScanData,
    SelectOptionRequest,
    TextFeedbackRequest,
    UnmatchedItem,
    ValidatePhotoInput,
    WorkflowState,
)
from app.utils.lidar import LidarParseError, parse_room_dimensions

logger = structlog.get_logger()

router = APIRouter(tags=["projects"])

MAX_PHOTO_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_INSPIRATION_PHOTOS = 3

# In-memory mock state store (replaced by Temporal in P2)
_mock_states: dict[str, WorkflowState] = {}


@dataclass
class _IntakeSession:
    """Per-project intake conversation state."""

    mode: Literal["quick", "full", "open"]
    history: list[ChatMessage] = field(default_factory=list)
    last_partial_brief: DesignBrief | None = None


_intake_sessions: dict[str, _IntakeSession] = {}

# Mock generation: stores (ready_at, options) per project for simulated delay
_mock_pending_generation: dict[str, tuple[float, list[DesignOption]]] = {}
MOCK_GENERATION_DELAY: float = 2.0  # seconds; set to 0.0 in tests

# Mock shopping: stores (ready_at, shopping_list) per project for simulated delay
_mock_pending_shopping: dict[str, tuple[float, GenerateShoppingListOutput]] = {}
MOCK_SHOPPING_DELAY: float = 2.0  # seconds; set to 0.0 in tests


def _maybe_complete_generation(project_id: str, state: WorkflowState) -> None:
    """Auto-complete generation if the simulated delay has elapsed."""
    if state.step != "generation":
        return
    pending = _mock_pending_generation.get(project_id)
    if pending is None:
        return
    ready_at, options = pending
    if time.monotonic() >= ready_at:
        state.generated_options = options
        state.step = "selection"
        _mock_pending_generation.pop(project_id, None)


def _maybe_complete_shopping(project_id: str, state: WorkflowState) -> None:
    """Auto-complete shopping list if the simulated delay has elapsed."""
    if state.step != "shopping":
        return
    pending = _mock_pending_shopping.get(project_id)
    if pending is None:
        return
    ready_at, shopping_list = pending
    if time.monotonic() >= ready_at:
        state.shopping_list = shopping_list
        state.step = "completed"
        _mock_pending_shopping.pop(project_id, None)


def _get_state(project_id: str) -> WorkflowState | None:
    state = _mock_states.get(project_id)
    if state is not None:
        _maybe_complete_generation(project_id, state)
        _maybe_complete_shopping(project_id, state)
    return state


def _error(status: int, code: str, message: str, *, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, message=message, retryable=retryable).model_dump(),
    )


_NOT_FOUND = ("workflow_not_found", "Project not found")


def _check_step(
    state: WorkflowState | None,
    expected: str | tuple[str, ...] | None,
    action: str,
) -> JSONResponse | None:
    if state is None:
        return _error(404, *_NOT_FOUND)
    if expected is None:
        return None
    valid = (expected,) if isinstance(expected, str) else expected
    if state.step not in valid:
        return _error(409, "wrong_step", f"Cannot {action} in step '{state.step}'")
    return None


def _mock_options(project_id: str, caption_a: str, caption_b: str) -> list[DesignOption]:
    return [
        DesignOption(
            image_url=f"https://r2.example.com/projects/{project_id}/generated/option_{i}.png",
            caption=caption,
        )
        for i, caption in enumerate([caption_a, caption_b])
    ]


def _apply_revision(
    state: WorkflowState,
    project_id: str,
    revision_type: str,
    instructions: list[str] | None = None,
) -> None:
    revision_num = state.iteration_count + 1
    revised_url = (
        f"https://r2.example.com/projects/{project_id}/generated/revision_{revision_num}.png"
    )
    state.revision_history.append(
        RevisionRecord(
            revision_number=revision_num,
            type=revision_type,
            base_image_url=state.current_image or "",
            revised_image_url=revised_url,
            instructions=instructions or [],
        )
    )
    state.current_image = revised_url
    state.chat_history_key = f"chat/{project_id}/history.json"
    state.iteration_count = revision_num
    if state.iteration_count >= 5:
        state.step = "approval"


# --- Project lifecycle ---


@router.post("/projects", status_code=201, response_model=CreateProjectResponse)
async def create_project(body: CreateProjectRequest) -> CreateProjectResponse:
    """Start a new design project. Creates a Temporal workflow."""
    project_id = str(uuid.uuid4())
    _mock_states[project_id] = WorkflowState(step="photos")
    logger.info("project_created", project_id=project_id, has_lidar=body.has_lidar)
    return CreateProjectResponse(project_id=project_id)


@router.get(
    "/projects/{project_id}",
    response_model=WorkflowState,
    responses={404: {"model": ErrorResponse}},
)
async def get_project_state(project_id: str):
    """Query current workflow state. iOS polls this endpoint."""
    state = _get_state(project_id)
    if state is None:
        return _error(404, *_NOT_FOUND)
    return state


@router.delete(
    "/projects/{project_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def delete_project(project_id: str):
    """Cancel project and purge data."""
    if project_id not in _mock_states:
        return _error(404, *_NOT_FOUND)
    del _mock_states[project_id]
    _intake_sessions.pop(project_id, None)
    _mock_pending_generation.pop(project_id, None)
    _mock_pending_shopping.pop(project_id, None)
    logger.info("project_deleted", project_id=project_id)


# --- Photo upload ---


@router.post(
    "/projects/{project_id}/photos",
    response_model=PhotoUploadResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def upload_photo(
    project_id: str,
    file: UploadFile,
    photo_type: Literal["room", "inspiration"] = "room",
    note: str | None = None,
):
    """Upload photo -> validate -> add to state if valid."""
    state = _get_state(project_id)
    if err := _check_step(state, ("photos", "scan"), "upload photos"):
        return err
    assert state is not None  # guaranteed by _check_step's 404 path

    # PHOTO-7: notes are only valid on inspiration photos
    if note is not None and photo_type != "inspiration":
        return _error(422, "note_not_allowed", "Notes are only allowed on inspiration photos")
    if note is not None and len(note) > 200:
        return _error(
            422, "note_too_long", "Inspiration photo note must be 200 characters or fewer"
        )

    # PHOTO-10: enforce max inspiration photos before doing any work
    if photo_type == "inspiration":
        inspo_count = sum(1 for p in state.photos if p.photo_type == "inspiration")
        if inspo_count >= MAX_INSPIRATION_PHOTOS:
            return _error(
                422,
                "too_many_inspiration_photos",
                f"Maximum {MAX_INSPIRATION_PHOTOS} inspiration photos",
            )

    # Stream-read with early termination to avoid buffering unbounded uploads
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(65_536):
        total += len(chunk)
        if total > MAX_PHOTO_BYTES:
            mb = MAX_PHOTO_BYTES // (1024 * 1024)
            return _error(413, "file_too_large", f"Photo exceeds {mb} MB limit")
        chunks.append(chunk)
    image_data = b"".join(chunks)

    validation = await asyncio.to_thread(
        validate_photo,
        ValidatePhotoInput(image_data=image_data, photo_type=photo_type),
    )

    photo_id = str(uuid.uuid4())

    if validation.passed:
        photo = PhotoData(
            photo_id=photo_id,
            storage_key=f"projects/{project_id}/photos/{photo_type}_{len(state.photos)}.jpg",
            photo_type=photo_type,
            note=note,
        )
        state.photos.append(photo)
        # Auto-transition to scan after minimum room photos (mirrors workflow behavior)
        room_count = sum(1 for p in state.photos if p.photo_type == "room")
        if room_count >= 2 and state.step == "photos":
            state.step = "scan"

    logger.info(
        "photo_uploaded",
        project_id=project_id,
        photo_id=photo_id,
        photo_type=photo_type,
        passed=validation.passed,
        failures=validation.failures,
        size_bytes=len(image_data),
    )
    return PhotoUploadResponse(photo_id=photo_id, validation=validation)


# --- Photo delete ---


@router.delete(
    "/projects/{project_id}/photos/{photo_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def delete_photo(project_id: str, photo_id: str):
    """Remove a photo from the project."""
    state = _get_state(project_id)
    if err := _check_step(state, ("photos", "scan"), "delete photo"):
        return err
    assert state is not None
    for i, photo in enumerate(state.photos):
        if photo.photo_id == photo_id:
            state.photos.pop(i)
            # Reverse auto-transition if room count drops below threshold
            room_count = sum(1 for p in state.photos if p.photo_type == "room")
            if room_count < 2 and state.step == "scan":
                state.step = "photos"
            logger.info("photo_deleted", project_id=project_id, photo_id=photo_id)
            return  # 204 implicit
    return _error(404, "photo_not_found", f"Photo {photo_id} not found")


# --- Scan ---


@router.post(
    "/projects/{project_id}/scan",
    response_model=ActionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def upload_scan(project_id: str, body: dict):
    """Upload LiDAR scan data -> parse dimensions -> store -> signal workflow."""
    state = _get_state(project_id)
    if err := _check_step(state, "scan", "upload scan"):
        return err
    assert state is not None

    try:
        dimensions = parse_room_dimensions(body)
    except LidarParseError as exc:
        logger.warning("scan_parse_failed", project_id=project_id, error=str(exc))
        return _error(422, "invalid_scan_data", str(exc))

    state.scan_data = ScanData(
        storage_key=f"projects/{project_id}/lidar/scan.json",
        room_dimensions=dimensions,
    )
    state.step = "intake"
    logger.info(
        "scan_uploaded",
        project_id=project_id,
        width_m=dimensions.width_m,
        length_m=dimensions.length_m,
        height_m=dimensions.height_m,
    )
    return ActionResponse()


@router.post(
    "/projects/{project_id}/scan/skip",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def skip_scan(project_id: str):
    """Skip LiDAR scan -> signal workflow."""
    state = _get_state(project_id)
    if err := _check_step(state, "scan", "skip scan"):
        return err
    assert state is not None
    state.step = "intake"
    return ActionResponse()


# --- Intake ---


@router.post(
    "/projects/{project_id}/intake/start",
    response_model=IntakeChatOutput,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_intake(project_id: str, body: IntakeStartRequest):
    """Begin intake conversation with selected mode.

    Returns a static welcome message and initializes conversation tracking.
    The first real agent call happens on send_intake_message.
    """
    state = _get_state(project_id)
    if err := _check_step(state, "intake", "start intake"):
        return err
    assert state is not None
    _intake_sessions[project_id] = _IntakeSession(mode=body.mode)
    return IntakeChatOutput(
        agent_message="Welcome! Let's design your perfect room. "
        "What type of room are we working with?",
        options=[
            QuickReplyOption(number=1, label="Living Room", value="living room"),
            QuickReplyOption(number=2, label="Bedroom", value="bedroom"),
            QuickReplyOption(number=3, label="Home Office", value="home office"),
        ],
        progress="Question 1 of 3",
    )


@router.post(
    "/projects/{project_id}/intake/message",
    response_model=IntakeChatOutput,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def send_intake_message(project_id: str, body: IntakeMessageRequest):
    """Send user message to intake agent.

    When use_mock_activities=False, calls T3's _run_intake_core with the
    project context and accumulated conversation history.
    When use_mock_activities=True, returns canned mock responses.
    """
    state = _get_state(project_id)
    if err := _check_step(state, "intake", "send intake message"):
        return err
    assert state is not None

    if not settings.use_mock_activities:
        return await _real_intake_message(project_id, state, body.message)

    return _mock_intake_message(project_id, body.message)


def _mock_intake_message(project_id: str, message: str) -> IntakeChatOutput | JSONResponse:
    """Canned 3-step mock conversation for iOS development."""
    session = _intake_sessions.get(project_id)
    if session is None:
        return _error(409, "wrong_step", "Call start_intake first")
    session.history.append(ChatMessage(role="user", content=message))
    step = len([m for m in session.history if m.role == "user"])

    if step == 1:
        reply = IntakeChatOutput(
            agent_message=f"Great, a {message}! What design style are you drawn to?",
            options=[
                QuickReplyOption(number=1, label="Modern Minimalist", value="modern"),
                QuickReplyOption(number=2, label="Warm & Cozy", value="warm"),
                QuickReplyOption(number=3, label="Industrial", value="industrial"),
                QuickReplyOption(number=4, label="Scandinavian", value="scandinavian"),
            ],
            progress="Question 2 of 3",
        )
    elif step == 2:
        reply = IntakeChatOutput(
            agent_message=(
                "Love that style! Anything specific you'd like to change "
                "or keep in the room? For example: 'replace the couch' "
                "or 'keep the bookshelf'."
            ),
            is_open_ended=True,
            progress="Question 3 of 3",
        )
    else:
        user_msgs = [m.content for m in session.history if m.role == "user"]
        room_type = user_msgs[0] if user_msgs else "living room"
        style = user_msgs[1] if len(user_msgs) > 1 else "modern"
        detail = user_msgs[2] if len(user_msgs) > 2 else message
        reply = IntakeChatOutput(
            agent_message=(
                f"Here's what I've gathered: a {room_type} with "
                f'{style} style. You mentioned: "{detail}". '
                "Does this look right?"
            ),
            is_summary=True,
            partial_brief=DesignBrief(room_type=room_type),
            progress="Summary",
        )
    session.history.append(ChatMessage(role="assistant", content=reply.agent_message))
    return reply


async def _real_intake_message(
    project_id: str, state: WorkflowState, message: str
) -> IntakeChatOutput | JSONResponse:
    """Call T3's intake agent with project context and conversation history."""
    from app.activities.intake import _run_intake_core

    session = _intake_sessions.get(project_id)
    if session is None:
        return _error(409, "wrong_step", "Call start_intake first")

    # photo_index must match the index within inspiration_photos (not all photos),
    # since T3's build_messages enumerates inspiration_photo_urls with its own index.
    inspiration_photos = [p for p in state.photos if p.photo_type == "inspiration"]
    project_context: dict = {
        "room_photos": [p.storage_key for p in state.photos if p.photo_type == "room"],
        "inspiration_photos": [p.storage_key for p in inspiration_photos],
        "inspiration_notes": [
            {"photo_index": i, "note": p.note} for i, p in enumerate(inspiration_photos) if p.note
        ],
    }
    if session.last_partial_brief is not None:
        project_context["previous_brief"] = session.last_partial_brief.model_dump()

    intake_input = IntakeChatInput(
        mode=session.mode,
        project_context=project_context,
        conversation_history=session.history,
        user_message=message,
    )

    try:
        result = await _run_intake_core(intake_input)
    except Exception:
        logger.exception("intake_agent_error", project_id=project_id)
        return _error(
            500, "intake_error", "The design assistant encountered an error", retryable=True
        )

    session.history.append(ChatMessage(role="user", content=message))
    session.history.append(ChatMessage(role="assistant", content=result.agent_message))
    if result.partial_brief is not None:
        session.last_partial_brief = result.partial_brief

    return result


@router.post(
    "/projects/{project_id}/intake/confirm",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def confirm_intake(project_id: str, body: IntakeConfirmRequest):
    """Confirm design brief and move to generation."""
    state = _get_state(project_id)
    if err := _check_step(state, "intake", "confirm intake"):
        return err
    assert state is not None
    state.design_brief = body.brief
    state.step = "generation"
    _mock_pending_generation[project_id] = (
        time.monotonic() + MOCK_GENERATION_DELAY,
        _mock_options(project_id, "Modern Minimalist", "Warm Contemporary"),
    )
    return ActionResponse()


@router.post(
    "/projects/{project_id}/intake/skip",
    response_model=ActionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def skip_intake(project_id: str):
    """Skip intake and use defaults.

    INTAKE-3a: skipping is only allowed if at least 1 inspiration photo
    was uploaded — without inspiration, the generator has no style signal.
    """
    state = _get_state(project_id)
    if err := _check_step(state, "intake", "skip intake"):
        return err
    assert state is not None
    has_inspiration = any(p.photo_type == "inspiration" for p in state.photos)
    if not has_inspiration:
        return _error(
            422,
            "intake_required",
            "Intake is required when no inspiration photos are uploaded",
        )
    state.step = "generation"
    _mock_pending_generation[project_id] = (
        time.monotonic() + MOCK_GENERATION_DELAY,
        _mock_options(project_id, "Design Option A", "Design Option B"),
    )
    return ActionResponse()


# --- Selection ---


@router.post(
    "/projects/{project_id}/select",
    response_model=ActionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def select_option(project_id: str, body: SelectOptionRequest):
    """Select one of the generated design options."""
    state = _get_state(project_id)
    if err := _check_step(state, "selection", "select"):
        return err
    assert state is not None
    if body.index >= len(state.generated_options):
        return _error(422, "invalid_selection", f"Option index {body.index} out of range")
    state.selected_option = body.index
    state.current_image = state.generated_options[body.index].image_url
    state.step = "iteration"
    return ActionResponse()


@router.post(
    "/projects/{project_id}/start-over",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_over(project_id: str):
    """Go back to intake and start the design process over."""
    state = _get_state(project_id)
    if err := _check_step(state, None, "start over"):
        return err
    assert state is not None
    if state.approved or state.step in ("shopping", "completed"):
        return _error(409, "wrong_step", f"Cannot start over in step '{state.step}'")
    state.generated_options = []
    state.selected_option = None
    state.current_image = None
    state.design_brief = None
    state.revision_history = []
    state.iteration_count = 0
    state.approved = False
    state.shopping_list = None
    state.error = None
    state.chat_history_key = None
    state.step = "intake"
    _intake_sessions.pop(project_id, None)
    _mock_pending_generation.pop(project_id, None)
    _mock_pending_shopping.pop(project_id, None)
    return ActionResponse()


# --- Iteration ---


@router.post(
    "/projects/{project_id}/iterate/annotate",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def submit_annotation_edit(project_id: str, body: AnnotationEditRequest):
    """Submit annotation-based edit (numbered circles on design)."""
    state = _get_state(project_id)
    if err := _check_step(state, "iteration", "submit annotation edit"):
        return err
    assert state is not None
    instructions = [a.instruction for a in body.annotations]
    _apply_revision(state, project_id, "annotation", instructions=instructions)
    return ActionResponse()


@router.post(
    "/projects/{project_id}/iterate/feedback",
    response_model=ActionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def submit_text_feedback(project_id: str, body: TextFeedbackRequest):
    """Submit text feedback for design revision."""
    state = _get_state(project_id)
    if err := _check_step(state, "iteration", "submit feedback"):
        return err
    assert state is not None
    # REGEN-2: enforce 10-char minimum for text feedback
    if len(body.feedback) < 10:
        return _error(
            422,
            "feedback_too_short",
            "Please provide more detail (at least 10 characters)",
        )
    _apply_revision(state, project_id, "feedback", instructions=[body.feedback])
    return ActionResponse()


# --- Approval ---


@router.post(
    "/projects/{project_id}/approve",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def approve_design(project_id: str):
    """Approve the current design and trigger shopping list generation."""
    state = _get_state(project_id)
    if err := _check_step(state, ("iteration", "approval"), "approve"):
        return err
    assert state is not None
    # Matches workflow: approve_design signal is ignored when there's an active error.
    # User must call retry_failed_step first.
    if state.error is not None:
        return _error(409, "active_error", "Resolve the error before approving")
    state.approved = True
    # Transition to shopping step with simulated delay (mirrors GAP-5 generation pattern).
    # iOS polls GET /projects/{id} and sees step="shopping" with shopping_list=None,
    # then on next poll after delay, sees step="completed" with shopping_list populated.
    shopping_list = GenerateShoppingListOutput(
        items=[
            ProductMatch(
                category_group="Furniture",
                product_name="Mock Accent Chair",
                retailer="Mock Store",
                price_cents=24999,
                product_url="https://example.com/accent-chair",
                image_url="https://example.com/images/accent-chair.jpg",
                confidence_score=0.92,
                why_matched="Matches modern minimalist style",
                fit_status="may_not_fit",
                fit_detail="Measure doorway width before ordering",
                dimensions='32"W x 28"D x 31"H',
            ),
            ProductMatch(
                category_group="Lighting",
                product_name="Mock Floor Lamp",
                retailer="Mock Store",
                price_cents=8999,
                product_url="https://example.com/floor-lamp",
                confidence_score=0.85,
                why_matched="Complements room ambiance",
            ),
        ],
        unmatched=[
            UnmatchedItem(
                category="Rug",
                search_keywords="modern geometric area rug 5x7",
                google_shopping_url="https://www.google.com/search?tbm=shop&q=modern+geometric+rug+5x7",
            ),
        ],
        total_estimated_cost_cents=33998,
    )
    _mock_pending_shopping[project_id] = (
        time.monotonic() + MOCK_SHOPPING_DELAY,
        shopping_list,
    )
    state.step = "shopping"
    return ActionResponse()


# --- Retry ---


@router.post(
    "/projects/{project_id}/retry",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}},
)
async def retry_failed_step(project_id: str):
    """Clear error and retry the failed step."""
    state = _get_state(project_id)
    if err := _check_step(state, None, "retry"):
        return err
    assert state is not None
    state.error = None
    return ActionResponse()
