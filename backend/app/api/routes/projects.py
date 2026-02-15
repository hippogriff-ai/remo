"""Project API endpoints — thin proxy to Temporal workflow.

Mock mode (use_temporal=False): returns realistic stub data so T1 (iOS)
can develop against it without real infrastructure.

Temporal mode (use_temporal=True): forwards signals/queries to Temporal
workflows. State lives in Temporal, not in-memory.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

import structlog
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

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
    RoomContext,
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
MAX_SCAN_BYTES = 1 * 1024 * 1024  # 1 MB — LiDAR JSON is typically <100 KB
MAX_INSPIRATION_PHOTOS = 3


def _r2_configured() -> bool:
    """Check if all required R2 credentials are available for photo storage."""
    return bool(
        settings.r2_account_id
        and settings.r2_access_key_id
        and settings.r2_secret_access_key
        and settings.r2_bucket_name
    )


# In-memory mock state store (used when use_temporal=False)
_mock_states: dict[str, WorkflowState] = {}


@dataclass
class _IntakeSession:
    """Per-project intake conversation state."""

    mode: Literal["quick", "full", "open"]
    history: list[ChatMessage] = field(default_factory=list)
    last_partial_brief: DesignBrief | None = None
    loaded_skill_ids: list[str] = field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Temporal helpers — used only when settings.use_temporal is True
# ---------------------------------------------------------------------------


async def _query_temporal_state(request: Request, project_id: str) -> WorkflowState | None:
    """Query workflow state from Temporal. Returns None if workflow not found."""
    from temporalio.service import RPCError, RPCStatusCode

    from app.workflows.design_project import DesignProjectWorkflow

    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(project_id)
    try:
        state: WorkflowState = await handle.query(DesignProjectWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        logger.error(
            "temporal_query_failed",
            project_id=project_id,
            rpc_status=e.status.name if e.status else "UNKNOWN",
        )
        raise


async def _signal_workflow(request: Request, project_id: str, signal, *args) -> JSONResponse | None:
    """Send a signal to the Temporal workflow.

    Returns None on success, or a JSONResponse (404) if the workflow is not found.
    """
    from temporalio.service import RPCError, RPCStatusCode

    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(project_id)
    signal_name = getattr(signal, "__name__", str(signal))
    try:
        if args:
            await handle.signal(signal, *args)
        else:
            await handle.signal(signal)
        return None
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return _error(404, *_NOT_FOUND)
        logger.error(
            "temporal_signal_failed",
            project_id=project_id,
            signal=signal_name,
            rpc_status=e.status.name if e.status else "UNKNOWN",
        )
        raise


async def _resolve_state(request: Request, project_id: str) -> WorkflowState | None:
    """Get project state from either Temporal or mock store."""
    if settings.use_temporal:
        return await _query_temporal_state(request, project_id)
    return _get_state(project_id)


# --- Project lifecycle ---


@router.post("/projects", status_code=201, response_model=CreateProjectResponse)
async def create_project(body: CreateProjectRequest, request: Request) -> CreateProjectResponse:
    """Start a new design project. Creates a Temporal workflow."""
    project_id = str(uuid.uuid4())

    if settings.use_temporal:
        from temporalio.service import RPCError

        from app.workflows.design_project import DesignProjectWorkflow

        client = request.app.state.temporal_client
        try:
            await client.start_workflow(
                DesignProjectWorkflow.run,
                project_id,
                id=project_id,
                task_queue=settings.temporal_task_queue,
            )
        except RPCError:
            logger.exception("workflow_start_failed", project_id=project_id)
            raise
    else:
        _mock_states[project_id] = WorkflowState(step="photos")

    logger.info("project_created", project_id=project_id, has_lidar=body.has_lidar)
    return CreateProjectResponse(project_id=project_id)


@router.get(
    "/projects/{project_id}",
    response_model=WorkflowState,
    responses={404: {"model": ErrorResponse}},
)
async def get_project_state(project_id: str, request: Request):
    """Query current workflow state. iOS polls this endpoint."""
    state = await _resolve_state(request, project_id)
    if state is None:
        return _error(404, *_NOT_FOUND)
    return state


@router.delete(
    "/projects/{project_id}",
    status_code=204,
    responses={404: {"model": ErrorResponse}},
)
async def delete_project(project_id: str, request: Request):
    """Cancel project and purge data."""
    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(request, project_id, DesignProjectWorkflow.cancel_project):
            return err
    else:
        if project_id not in _mock_states:
            return _error(404, *_NOT_FOUND)
        del _mock_states[project_id]
        _mock_pending_generation.pop(project_id, None)
        _mock_pending_shopping.pop(project_id, None)

    _intake_sessions.pop(project_id, None)
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
    request: Request,
    photo_type: Literal["room", "inspiration"] = "room",
    note: str | None = None,
):
    """Upload photo -> validate -> store (R2 in Temporal mode) -> add to state."""
    state = await _resolve_state(request, project_id)
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
        storage_key = f"projects/{project_id}/photos/{photo_type}_{photo_id}.jpg"
        photo = PhotoData(
            photo_id=photo_id,
            storage_key=storage_key,
            photo_type=photo_type,
            note=note,
        )

        if settings.use_temporal:
            from app.workflows.design_project import DesignProjectWorkflow

            if _r2_configured():
                from app.utils.r2 import delete_object, upload_object

                content_type = file.content_type or "image/jpeg"
                try:
                    await asyncio.to_thread(upload_object, storage_key, image_data, content_type)
                except Exception:
                    logger.exception(
                        "r2_upload_failed",
                        storage_key=storage_key,
                        project_id=project_id,
                        content_type=content_type,
                        size_bytes=len(image_data),
                    )
                    raise
            else:
                logger.warning(
                    "r2_not_configured_skipping_upload",
                    storage_key=storage_key,
                    project_id=project_id,
                )
            if err := await _signal_workflow(
                request, project_id, DesignProjectWorkflow.add_photo, photo
            ):
                if _r2_configured():
                    # Rollback: remove orphaned R2 object if signal failed
                    from app.utils.r2 import delete_object

                    try:
                        await asyncio.to_thread(delete_object, storage_key)
                    except Exception:
                        logger.error(
                            "r2_rollback_failed",
                            storage_key=storage_key,
                            project_id=project_id,
                            exc_info=True,
                        )
                return err
        else:
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
async def delete_photo(project_id: str, photo_id: str, request: Request):
    """Remove a photo from the project."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, ("photos", "scan"), "delete photo"):
        return err
    assert state is not None

    # Verify photo exists in state before signaling
    photo_exists = any(p.photo_id == photo_id for p in state.photos)
    if not photo_exists:
        return _error(404, "photo_not_found", f"Photo {photo_id} not found")

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        # R2 object is NOT deleted here — purge_project_data activity handles R2 cleanup
        # on project delete/abandon. Individual photo removal only updates workflow state.
        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.remove_photo, photo_id
        ):
            return err
    else:
        for i, photo in enumerate(state.photos):
            if photo.photo_id == photo_id:
                state.photos.pop(i)
                break

    logger.info("photo_deleted", project_id=project_id, photo_id=photo_id)


# --- Photo note ---


class UpdatePhotoNoteRequest(BaseModel):
    note: str | None = None


@router.patch(
    "/projects/{project_id}/photos/{photo_id}/note",
    response_model=ActionResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def update_photo_note(
    project_id: str, photo_id: str, body: UpdatePhotoNoteRequest, request: Request
):
    """Update or clear the note on an inspiration photo."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, ("photos", "scan", "intake"), "update photo note"):
        return err
    assert state is not None

    photo = next((p for p in state.photos if p.photo_id == photo_id), None)
    if photo is None:
        return _error(404, "photo_not_found", f"Photo {photo_id} not found")
    if photo.photo_type != "inspiration":
        return _error(422, "note_not_allowed", "Notes are only allowed on inspiration photos")
    if body.note is not None and len(body.note) > 200:
        return _error(
            422, "note_too_long", "Inspiration photo note must be 200 characters or fewer"
        )

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.update_photo_note, photo_id, body.note
        ):
            return err
    else:
        photo.note = body.note

    logger.info("photo_note_updated", project_id=project_id, photo_id=photo_id)
    return ActionResponse()


# --- Scan ---


@router.post(
    "/projects/{project_id}/scan",
    response_model=ActionResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        413: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def upload_scan(project_id: str, body: dict, request: Request):
    """Upload LiDAR scan data -> parse dimensions -> store -> signal workflow."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "scan", "upload scan"):
        return err
    assert state is not None

    # G8: Best-effort size check via Content-Length header. Body is already parsed
    # by FastAPI, so this doesn't prevent memory allocation — it catches well-behaved
    # clients early. True defense requires ASGI-level body limits (e.g. uvicorn config).
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            cl_int = int(content_length)
            if cl_int < 0:
                return _error(400, "bad_request", "Invalid Content-Length header")
            if cl_int > MAX_SCAN_BYTES:
                mb = MAX_SCAN_BYTES // (1024 * 1024)
                return _error(413, "scan_too_large", f"Scan data exceeds {mb} MB limit")
        except ValueError:
            return _error(400, "bad_request", "Invalid Content-Length header")

    try:
        dimensions = parse_room_dimensions(body)
    except LidarParseError as exc:
        logger.warning("scan_parse_failed", project_id=project_id, error=str(exc))
        return _error(422, "invalid_scan_data", str(exc))

    scan_data = ScanData(
        storage_key=f"projects/{project_id}/lidar/scan.json",
        room_dimensions=dimensions,
    )

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.complete_scan, scan_data
        ):
            return err
    else:
        state.scan_data = scan_data
        # Mirror _build_room_context(): merge analysis + LiDAR into RoomContext.
        # The real workflow early-returns when room_analysis is None, so we do
        # the same. In typical mock usage (no photo analysis), room_context stays
        # None. When room_analysis IS set (e.g., by a test or future mock
        # enhancement), we build the full context with ["photos", "lidar"].
        if state.room_analysis is not None:
            state.room_analysis.estimated_dimensions = (
                f"{dimensions.width_m:.1f}m x {dimensions.length_m:.1f}m "
                f"(ceiling {dimensions.height_m:.1f}m)"
            )
            state.room_context = RoomContext(
                photo_analysis=state.room_analysis,
                room_dimensions=dimensions,
                enrichment_sources=["photos", "lidar"],
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
async def skip_scan(project_id: str, request: Request):
    """Skip LiDAR scan -> signal workflow."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "scan", "skip scan"):
        return err
    assert state is not None

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(request, project_id, DesignProjectWorkflow.skip_scan):
            return err
    else:
        state.step = "intake"

    return ActionResponse()


# --- Intake ---


@router.post(
    "/projects/{project_id}/intake/start",
    response_model=IntakeChatOutput,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_intake(project_id: str, body: IntakeStartRequest, request: Request):
    """Begin intake conversation with selected mode.

    Returns a static welcome message and initializes conversation tracking.
    The first real agent call happens on send_intake_message.
    """
    state = await _resolve_state(request, project_id)
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
            QuickReplyOption(number=3, label="Kitchen", value="kitchen"),
            QuickReplyOption(number=4, label="Bathroom", value="bathroom"),
            QuickReplyOption(number=5, label="Dining Room", value="dining room"),
            QuickReplyOption(number=6, label="Home Office", value="home office"),
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
async def send_intake_message(project_id: str, body: IntakeMessageRequest, request: Request):
    """Send user message to intake agent.

    When use_mock_activities=False, calls T3's _run_intake_core with the
    project context and accumulated conversation history.
    When use_mock_activities=True, returns canned mock responses.
    """
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "intake", "send intake message"):
        return err
    assert state is not None

    # Reconstruct session from client-provided history if API restarted mid-conversation
    if project_id not in _intake_sessions and body.conversation_history:
        _intake_sessions[project_id] = _IntakeSession(
            mode=body.mode or "quick",
            history=list(body.conversation_history),
        )

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
    from app.utils.r2 import generate_presigned_url

    inspiration_photos = [p for p in state.photos if p.photo_type == "inspiration"]
    room_keys = [p.storage_key for p in state.photos if p.photo_type == "room"]
    inspo_keys = [p.storage_key for p in inspiration_photos]

    # Convert storage keys to pre-signed HTTPS URLs for the Claude vision API
    room_urls = [await asyncio.to_thread(generate_presigned_url, k) for k in room_keys]
    inspo_urls = [await asyncio.to_thread(generate_presigned_url, k) for k in inspo_keys]

    project_context: dict = {
        "room_photos": room_urls,
        "inspiration_photos": inspo_urls,
        "inspiration_notes": [
            {"photo_index": i, "note": p.note} for i, p in enumerate(inspiration_photos) if p.note
        ],
    }
    if session.last_partial_brief is not None:
        project_context["previous_brief"] = session.last_partial_brief.model_dump()
    if state.room_analysis is not None:
        project_context["room_analysis"] = state.room_analysis.model_dump()
    if state.room_context is not None:
        project_context["room_context"] = state.room_context.model_dump()
    if session.loaded_skill_ids:
        project_context["loaded_skill_ids"] = session.loaded_skill_ids

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
    if result.requested_skills:
        from app.activities.skill_loader import cap_skills

        # cap_skills handles dedup + logging internally
        combined = session.loaded_skill_ids + result.requested_skills
        session.loaded_skill_ids = cap_skills(combined)

    return result


@router.post(
    "/projects/{project_id}/intake/confirm",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def confirm_intake(project_id: str, body: IntakeConfirmRequest, request: Request):
    """Confirm design brief and move to generation."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "intake", "confirm intake"):
        return err
    assert state is not None

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.complete_intake, body.brief
        ):
            return err
    else:
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
async def skip_intake(project_id: str, request: Request):
    """Skip intake and use defaults.

    INTAKE-3a: skipping is only allowed if at least 1 inspiration photo
    was uploaded — without inspiration, the generator has no style signal.
    """
    state = await _resolve_state(request, project_id)
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

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(request, project_id, DesignProjectWorkflow.skip_intake):
            return err
    else:
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
async def select_option(project_id: str, body: SelectOptionRequest, request: Request):
    """Select one of the generated design options."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "selection", "select"):
        return err
    assert state is not None
    if body.index >= len(state.generated_options):
        return _error(422, "invalid_selection", f"Option index {body.index} out of range")

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.select_option, body.index
        ):
            return err
    else:
        state.selected_option = body.index
        state.current_image = state.generated_options[body.index].image_url
        state.step = "iteration"

    return ActionResponse()


@router.post(
    "/projects/{project_id}/start-over",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def start_over(project_id: str, request: Request):
    """Go back to intake and start the design process over."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, None, "start over"):
        return err
    assert state is not None
    if state.approved or state.step in ("shopping", "completed", "abandoned", "cancelled"):
        return _error(409, "wrong_step", f"Cannot start over in step '{state.step}'")

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(request, project_id, DesignProjectWorkflow.start_over):
            return err
    else:
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
        # Match workflow (lines 412-428): clear analysis/context so intake
        # re-fires analysis. Workflow also clears intake_skipped and
        # _action_queue, which are internal loop state not in the mock.
        state.room_analysis = None
        state.room_context = None
        state.step = "intake"
        _mock_pending_generation.pop(project_id, None)
        _mock_pending_shopping.pop(project_id, None)

    _intake_sessions.pop(project_id, None)
    return ActionResponse()


# --- Iteration ---


@router.post(
    "/projects/{project_id}/iterate/annotate",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def submit_annotation_edit(project_id: str, body: AnnotationEditRequest, request: Request):
    """Submit annotation-based edit (numbered circles on design)."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "iteration", "submit annotation edit"):
        return err
    assert state is not None

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        annotations_dicts = [a.model_dump() for a in body.annotations]
        if err := await _signal_workflow(
            request,
            project_id,
            DesignProjectWorkflow.submit_annotation_edit,
            annotations_dicts,
        ):
            return err
    else:
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
async def submit_text_feedback(project_id: str, body: TextFeedbackRequest, request: Request):
    """Submit text feedback for design revision."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, "iteration", "submit feedback"):
        return err
    assert state is not None
    # REGEN-2: 10-char minimum enforced by TextFeedbackRequest(min_length=10)

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request,
            project_id,
            DesignProjectWorkflow.submit_text_feedback,
            body.feedback,
        ):
            return err
    else:
        _apply_revision(state, project_id, "feedback", instructions=[body.feedback])

    return ActionResponse()


# --- Approval ---


@router.post(
    "/projects/{project_id}/approve",
    response_model=ActionResponse,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def approve_design(project_id: str, request: Request):
    """Approve the current design and trigger shopping list generation."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, ("iteration", "approval"), "approve"):
        return err
    assert state is not None
    # Matches workflow: approve_design signal is ignored when there's an active error.
    # User must call retry_failed_step first.
    if state.error is not None:
        return _error(409, "active_error", "Resolve the error before approving")

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(request, project_id, DesignProjectWorkflow.approve_design):
            return err
    else:
        state.approved = True
        # Transition to shopping step with simulated delay (mirrors GAP-5 generation pattern).
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
async def retry_failed_step(project_id: str, request: Request):
    """Clear error and retry the failed step."""
    state = await _resolve_state(request, project_id)
    if err := _check_step(state, None, "retry"):
        return err
    assert state is not None

    if settings.use_temporal:
        from app.workflows.design_project import DesignProjectWorkflow

        if err := await _signal_workflow(
            request, project_id, DesignProjectWorkflow.retry_failed_step
        ):
            return err
    else:
        state.error = None

    return ActionResponse()


# ---------------------------------------------------------------------------
# Debug endpoint — development only
# ---------------------------------------------------------------------------


@router.post("/debug/force-failure", response_model=ActionResponse)
async def force_failure():
    """Arm a one-shot failure in the next generate_designs call.

    Only available in development. Used by E2E-11 to test the
    error → retry cycle without real AI API failures.
    """
    if settings.environment != "development":
        return _error(403, "forbidden", "Debug endpoints disabled outside development")
    if not settings.use_mock_activities:
        return _error(
            409,
            "not_applicable",
            "Error injection only works with mock activities",
        )

    from app.activities.mock_stubs import FORCE_FAILURE_SENTINEL

    FORCE_FAILURE_SENTINEL.touch()
    return ActionResponse()
