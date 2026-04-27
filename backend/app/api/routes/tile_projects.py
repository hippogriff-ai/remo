"""Tile-project API endpoints — thin proxy to TileProjectWorkflow.

Parallel to `projects.py` but scoped to the Replace Material flow. State
lives in Temporal; these routes only forward signals and queries.
"""

from __future__ import annotations

import json
import uuid

import structlog
from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.models.contracts import (
    CreateProjectResponse,
    CreateTileProjectRequest,
    ErrorResponse,
    MaterialSpec,
    SetTileMaterialsRequest,
    SetTilePoliciesRequest,
    SetTileSurfacesRequest,
    TileSpecInput,
    TileWorkflowState,
)
from app.utils.lidar import (
    LidarParseError,
    parse_room_dimensions,
    patches_from_room_dimensions,
)

logger = structlog.get_logger()

router = APIRouter(tags=["tile-projects"])

MAX_SCAN_BYTES = 1 * 1024 * 1024  # 1 MB — same limit as design-flow scan
_NOT_FOUND = ("not_found", "Tile project not found.")
_MOCK_NOT_IMPLEMENTED = (
    "not_implemented",
    "Tile mode requires Temporal (USE_TEMPORAL=true) — mock mode has no state store.",
)


def _mock_mode_501() -> JSONResponse:
    """Uniform 501 returned by tile routes when Temporal is disabled.

    Tile mode intentionally has no in-memory mock store (unlike the design
    flow) — silent no-op signals would mask bugs during local dev. Callers
    see a clear 501 instead.
    """
    return _error(501, *_MOCK_NOT_IMPLEMENTED)


def _error(status: int, code: str, message: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(error=code, message=message, retryable=retryable).model_dump(),
    )


# ---------------------------------------------------------------------------
# Temporal helpers
# ---------------------------------------------------------------------------


async def _query_state(request: Request, project_id: str) -> TileWorkflowState | None:
    from temporalio.service import RPCError, RPCStatusCode

    from app.workflows.tile_project import TileProjectWorkflow

    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(project_id)
    try:
        state: TileWorkflowState = await handle.query(TileProjectWorkflow.get_state)
        return state
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return None
        logger.error(
            "tile_temporal_query_failed",
            project_id=project_id,
            rpc_status=e.status.name if e.status else "UNKNOWN",
        )
        raise


async def _signal(request: Request, project_id: str, signal, *args) -> JSONResponse | None:
    """Send a signal. For multi-arg signals, pass each positional arg —
    the helper forwards them via Temporal's `args=[...]` keyword.
    """
    from temporalio.service import RPCError, RPCStatusCode

    client = request.app.state.temporal_client
    handle = client.get_workflow_handle(project_id)
    signal_name = getattr(signal, "__name__", str(signal))
    try:
        if len(args) == 0:
            await handle.signal(signal)
        elif len(args) == 1:
            await handle.signal(signal, args[0])
        else:
            await handle.signal(signal, args=list(args))
        return None
    except RPCError as e:
        if e.status == RPCStatusCode.NOT_FOUND:
            return _error(404, *_NOT_FOUND)
        logger.error(
            "tile_temporal_signal_failed",
            project_id=project_id,
            signal=signal_name,
            rpc_status=e.status.name if e.status else "UNKNOWN",
        )
        raise


def _tile_spec_to_material(tile_input: TileSpecInput, *, material_id: str) -> MaterialSpec:
    """Convert iOS-facing cm-based TileSpec into a meter-based MaterialSpec."""
    return MaterialSpec(
        material_id=material_id,
        module_width_m=tile_input.width_cm / 100.0,
        module_height_m=tile_input.height_cm / 100.0,
        grout_width_mm=tile_input.grout_mm,
        modules_per_unit=tile_input.per_box,
        unit_of_sale="box",
        price_per_box_cents=(
            int(round(tile_input.price_per_box * 100))
            if tile_input.price_per_box is not None
            else None
        ),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/tile-projects",
    status_code=201,
    response_model=CreateProjectResponse,
)
async def create_tile_project(
    body: CreateTileProjectRequest, request: Request
) -> CreateProjectResponse | JSONResponse:
    """Start a new tile-mode project. Creates a TileProjectWorkflow."""
    if not settings.use_temporal:
        # Mock mode has no state store for tile projects; without a workflow
        # to signal into, returning a project_id would be a lie.
        return _mock_mode_501()
    project_id = str(uuid.uuid4())

    from temporalio.service import RPCError

    from app.workflows.tile_project import TileProjectWorkflow

    client = request.app.state.temporal_client
    try:
        await client.start_workflow(
            TileProjectWorkflow.run,
            project_id,
            id=project_id,
            task_queue=settings.temporal_task_queue,
        )
    except RPCError:
        logger.exception("tile_workflow_start_failed", project_id=project_id)
        raise

    logger.info(
        "tile_project_created",
        project_id=project_id,
        device_fingerprint=body.device_fingerprint,
    )
    return CreateProjectResponse(project_id=project_id)


@router.get(
    "/tile-projects/{project_id}",
    response_model=TileWorkflowState,
)
async def get_tile_project(project_id: str, request: Request) -> TileWorkflowState | JSONResponse:
    if not settings.use_temporal:
        return _mock_mode_501()
    state = await _query_state(request, project_id)
    if state is None:
        return _error(404, *_NOT_FOUND)
    return state


@router.post("/tile-projects/{project_id}/scan")
async def upload_tile_scan(project_id: str, request: Request, file: UploadFile) -> JSONResponse:
    """Upload RoomPlan JSON, parse, convert to SurfacePatches, signal workflow."""
    if not settings.use_temporal:
        return _mock_mode_501()

    # file.size is None for streamed multipart uploads — we MUST also check the
    # materialized body length after reading so a chunked client can't bypass
    # the 1 MB cap.
    if file.size is not None and file.size > MAX_SCAN_BYTES:
        return _error(413, "file_too_large", f"Scan exceeds {MAX_SCAN_BYTES // 1024} KB.")

    raw = await file.read()
    if len(raw) > MAX_SCAN_BYTES:
        return _error(413, "file_too_large", f"Scan exceeds {MAX_SCAN_BYTES // 1024} KB.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _error(422, "invalid_scan", f"Scan JSON malformed: {exc}")

    try:
        dims = parse_room_dimensions(data)
    except LidarParseError as exc:
        return _error(422, "invalid_scan", str(exc))

    patches = patches_from_room_dimensions(dims)

    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.set_surfaces, patches):
        return err

    return JSONResponse(content={"status": "ok", "surface_count": len(patches)})


@router.post("/tile-projects/{project_id}/materials")
async def set_tile_materials(
    project_id: str,
    body: SetTileMaterialsRequest,
    request: Request,
) -> JSONResponse:
    if not settings.use_temporal:
        return _mock_mode_501()
    wall_material = _tile_spec_to_material(body.wall, material_id="wall-tile")
    floor_material = _tile_spec_to_material(body.floor, material_id="floor-tile")

    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(
        request,
        project_id,
        TileProjectWorkflow.set_materials,
        wall_material,
        floor_material,
    ):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/policies")
async def set_tile_policies(
    project_id: str,
    body: SetTilePoliciesRequest,
    request: Request,
) -> JSONResponse:
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(
        request,
        project_id,
        TileProjectWorkflow.set_policies,
        body.overage_policy,
        body.starting_point_rule,
        body.contractor_tier,
    ):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/surfaces")
async def set_tile_surfaces(
    project_id: str,
    body: SetTileSurfacesRequest,
    request: Request,
) -> JSONResponse:
    """Overwrite the surfaces list — used when iOS edits masks in the scan
    step (adds user-drawn mask holes to each SurfacePatch)."""
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.set_surfaces, body.surfaces):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/render")
async def request_tile_render(project_id: str, request: Request) -> JSONResponse:
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.request_render):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/retry-render")
async def retry_tile_render(project_id: str, request: Request) -> JSONResponse:
    """Re-enter the render phase after a failed render attempt.

    The workflow parks on render failure until the client sends this signal
    (or cancels). Distinct from /render so PR 5 can distinguish a fresh
    request from a retry for telemetry + retry-count enforcement.
    """
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.request_render_retry):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/export")
async def request_tile_export(project_id: str, request: Request) -> JSONResponse:
    """Kick off cut-sheet PDF generation (post-render)."""
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.request_export):
        return err

    return JSONResponse(content={"status": "ok"})


@router.post("/tile-projects/{project_id}/confirm-estimate")
async def confirm_tile_estimate(project_id: str, request: Request) -> JSONResponse:
    """Lock in the current estimate without immediately requesting a render.

    iOS uses this when the user taps "Use This Estimate" but isn't ready to
    burn a render credit yet — later they POST /render to actually kick off
    the image generation.
    """
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.confirm_estimate):
        return err

    return JSONResponse(content={"status": "ok"})


@router.delete("/tile-projects/{project_id}")
async def cancel_tile_project(project_id: str, request: Request) -> JSONResponse:
    if not settings.use_temporal:
        return _mock_mode_501()
    from app.workflows.tile_project import TileProjectWorkflow

    if err := await _signal(request, project_id, TileProjectWorkflow.cancel_project):
        return err

    return JSONResponse(content={"status": "ok"})
