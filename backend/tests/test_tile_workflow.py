"""Tests for TileProjectWorkflow — signals, phase transitions, and estimate.

Scope (PR 1): verifies the workflow advances through scan → specs → estimate
phases and exposes a populated TileWorkflowState via query. Render + export
activities are stubs (NotImplementedError) in PR 1, so those phases are not
exercised end-to-end here.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

if TYPE_CHECKING:
    from temporalio.client import WorkflowHandle

from app.activities.mock_stubs import purge_project_data
from app.activities.tile_cutsheet import generate_cut_sheet_pdf
from app.activities.tile_render import render_tile_design
from app.models.contracts import (
    ContractorTier,
    Hole,
    HoleKind,
    MaterialSpec,
    OveragePolicy,
    RenderTileInput,
    RenderTileOutput,
    StartingPointRule,
    SurfacePatch,
    Vec2,
)
from app.workflows.tile_project import TileProjectWorkflow

pytestmark = pytest.mark.asyncio(loop_scope="module")

_TILE_ACTIVITIES = [
    render_tile_design,
    generate_cut_sheet_pdf,
    purge_project_data,
]


@pytest.fixture(scope="module")
async def workflow_env():
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        yield env


@pytest.fixture
def tq() -> str:
    return f"tile-test-{uuid.uuid4()}"


_test_handles: list[WorkflowHandle] = []


@pytest.fixture(autouse=True)
def _cleanup_workflows():
    _test_handles.clear()
    yield
    if not _test_handles:
        return

    async def _terminate_all() -> None:
        for h in _test_handles:
            with contextlib.suppress(Exception):
                await h.terminate("test cleanup")

    asyncio.get_event_loop().run_until_complete(_terminate_all())
    _test_handles.clear()


def _rect_patch(
    patch_id: str,
    width_m: float,
    height_m: float,
    *,
    kind: str = "wall",
    holes: list[Hole] | None = None,
) -> SurfacePatch:
    return SurfacePatch(
        patch_id=patch_id,
        kind=kind,  # type: ignore[arg-type]
        polygon=[
            Vec2(x=0.0, y=0.0),
            Vec2(x=width_m, y=0.0),
            Vec2(x=width_m, y=height_m),
            Vec2(x=0.0, y=height_m),
        ],
        holes=holes or [],
        axis=Vec2(x=1.0, y=0.0),
        origin=Vec2(x=0.0, y=0.0),
        normal_x=0.0,
        normal_y=1.0,
        normal_z=0.0,
    )


def _wall_spec() -> MaterialSpec:
    return MaterialSpec(
        material_id="wall",
        module_width_m=0.30,
        module_height_m=0.60,
        grout_width_mm=3.0,
        modules_per_unit=10,
        price_per_box_cents=4200,
    )


def _floor_spec() -> MaterialSpec:
    return MaterialSpec(
        material_id="floor",
        module_width_m=0.60,
        module_height_m=0.60,
        grout_width_mm=3.0,
        modules_per_unit=5,
        price_per_box_cents=6800,
    )


async def _start_workflow(env: WorkflowEnvironment, tq: str) -> WorkflowHandle:
    project_id = str(uuid.uuid4())
    handle = await env.client.start_workflow(
        TileProjectWorkflow.run,
        project_id,
        id=project_id,
        task_queue=tq,
    )
    _test_handles.append(handle)
    return handle


async def test_initial_state_is_scan(workflow_env: WorkflowEnvironment, tq: str) -> None:
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        # give the workflow scheduler a moment to enter the run method
        await asyncio.sleep(0)
        state = await handle.query(TileProjectWorkflow.get_state)
        assert state.step == "replace_material_scan"
        assert state.surfaces == []
        assert state.wall_material is None
        assert state.estimate is None


async def test_set_surfaces_advances_from_scan_to_specs(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)

        surfaces = [
            _rect_patch("wall-1", 1.8, 2.4),
            _rect_patch("floor", 2.4, 1.8, kind="floor"),
        ]
        await handle.signal(TileProjectWorkflow.set_surfaces, surfaces)

        # Wait briefly for phase transition. The time-skipping env lets the
        # workflow advance deterministically once the condition is satisfied.
        for _ in range(10):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_specs":
                break
        assert state.step == "replace_material_specs"
        assert len(state.surfaces) == 2


async def test_set_materials_advances_to_estimate_and_populates_estimate(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)

        surfaces = [
            _rect_patch("wall-1", 1.8, 2.4),
            _rect_patch("wall-2", 2.4, 2.4),
            _rect_patch("floor", 2.4, 1.8, kind="floor"),
        ]
        await handle.signal(TileProjectWorkflow.set_surfaces, surfaces)
        await handle.signal(
            TileProjectWorkflow.set_materials,
            args=[_wall_spec(), _floor_spec()],
        )

        state = None
        for _ in range(20):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_estimate" and state.estimate is not None:
                break

        assert state is not None
        assert state.step == "replace_material_estimate"
        assert state.estimate is not None
        assert state.estimate.wall_boxes_total > 0
        assert state.estimate.floor_boxes_total > 0
        assert state.wall_material is not None
        assert state.wall_material.module_width_m == pytest.approx(0.30)


async def test_set_policies_recomputes_estimate(workflow_env: WorkflowEnvironment, tq: str) -> None:
    """Switching from FLAT_10 to RISK_ADJUSTED policy produces a new estimate
    (different box counts in many cases, always a valid re-computation).
    """
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        surfaces = [_rect_patch("wall-1", 1.8, 2.4), _rect_patch("floor", 2.4, 1.8, kind="floor")]
        await handle.signal(TileProjectWorkflow.set_surfaces, surfaces)
        await handle.signal(
            TileProjectWorkflow.set_materials,
            args=[_wall_spec(), _floor_spec()],
        )

        # Wait for initial estimate
        state = None
        for _ in range(20):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate is not None:
                break
        assert state is not None and state.estimate is not None
        initial_overage = state.estimate.overage_policy

        # Flip policy
        await handle.signal(
            TileProjectWorkflow.set_policies,
            args=[
                OveragePolicy.RISK_ADJUSTED,
                StartingPointRule.LARGEST_WALL_CORNER,
                None,
            ],
        )
        for _ in range(10):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate and state.estimate.overage_policy == OveragePolicy.RISK_ADJUSTED:
                break
        assert state.estimate is not None
        assert state.estimate.overage_policy == OveragePolicy.RISK_ADJUSTED
        assert initial_overage == OveragePolicy.FLAT_10


async def test_confirm_estimate_sets_flag_but_does_not_advance_phase(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    """confirm_estimate is a soft lock. It surfaces via state.estimate_confirmed
    so iOS can render a "locked" chip, but it MUST NOT move the workflow into
    the render phase — rendering consumes a Gemini credit and must require an
    explicit request_render signal.

    Regression test for Codex P1 on commit 63880c7 where the wait condition
    unblocked on either signal, accidentally burning a render on every
    confirm.
    """
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        await handle.signal(
            TileProjectWorkflow.set_surfaces,
            [_rect_patch("wall-1", 1.8, 2.4), _rect_patch("floor", 2.4, 1.8, kind="floor")],
        )
        await handle.signal(TileProjectWorkflow.set_materials, args=[_wall_spec(), _floor_spec()])

        state = None
        for _ in range(30):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_estimate" and state.estimate is not None:
                break
        assert state is not None and state.step == "replace_material_estimate"
        assert state.estimate_confirmed is False

        await handle.signal(TileProjectWorkflow.confirm_estimate)

        # Give the workflow ample time to (incorrectly) advance. If it does,
        # this test catches it.
        for _ in range(50):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate_confirmed:
                break
        assert state is not None
        assert state.estimate_confirmed is True
        # Critical: still parked in estimate, no render burned.
        assert state.step == "replace_material_estimate"
        assert state.render_attempt_count == 0
        assert state.render_image_url is None

        # Now send the actual render request and confirm the workflow DOES
        # advance — confirming that the earlier "no advance" wasn't because
        # the workflow was stuck.
        await handle.signal(TileProjectWorkflow.request_render)
        for _ in range(100):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_render":
                break
        assert state.step == "replace_material_render"


async def test_set_materials_during_render_phase_recomputes_estimate(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    """Materials edits must refresh the estimate in any phase, not just
    'replace_material_estimate'. Regression test for Codex P1 on commit
    25e0b35 — set_materials had a stale phase-gate after I fixed the same
    bug in set_policies. Parallel signal handlers must have parallel
    recompute rules.
    """
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        await handle.signal(
            TileProjectWorkflow.set_surfaces,
            [_rect_patch("wall-1", 2.4, 2.4), _rect_patch("floor", 2.4, 1.8, kind="floor")],
        )
        await handle.signal(TileProjectWorkflow.set_materials, args=[_wall_spec(), _floor_spec()])
        await handle.signal(TileProjectWorkflow.request_render)

        state = None
        for _ in range(60):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_render":
                break
        assert state is not None and state.step == "replace_material_render"
        assert state.estimate is not None
        initial_wall_tiles = state.estimate.wall_tiles_total

        # Switch to a larger tile (60x60 for walls instead of 30x60) — packed
        # count should strictly decrease since each tile covers more area.
        larger_wall = MaterialSpec(
            material_id="wall",
            module_width_m=0.60,
            module_height_m=0.60,
            grout_width_mm=3.0,
            modules_per_unit=10,
            price_per_box_cents=4200,
        )
        await handle.signal(TileProjectWorkflow.set_materials, args=[larger_wall, _floor_spec()])
        for _ in range(30):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.wall_material and state.wall_material.module_width_m == pytest.approx(0.60):
                break
        assert state.wall_material is not None
        assert state.wall_material.module_width_m == pytest.approx(0.60)
        assert state.estimate is not None
        # Estimate must reflect the new material — strictly fewer tiles needed.
        assert state.estimate.wall_tiles_total < initial_wall_tiles
        # And the packed material in the estimate must match the state material.
        assert state.estimate.wall_material.module_width_m == pytest.approx(0.60)


async def test_retry_render_actually_reinvokes_the_render_activity(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    """Stronger than "counter bumps" — this test replaces the stub render
    activity with one that counts its own invocations and verifies that:
      1. The failing activity is invoked once per request_render / retry,
         so the counter-in-state-query matches real backend work.
      2. request_render_retry re-enters the render phase (doesn't just
         flip a flag).
      3. The error field persists across the retry until a success.
    """
    from temporalio import activity

    invocation_count = 0

    @activity.defn(name="render_tile_design")  # override the stub's registration
    async def counting_render(tile_input: RenderTileInput) -> RenderTileOutput:
        nonlocal invocation_count
        invocation_count += 1
        raise NotImplementedError("intentional test failure")

    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=[counting_render, generate_cut_sheet_pdf, purge_project_data],
    ):
        handle = await _start_workflow(workflow_env, tq)
        await handle.signal(
            TileProjectWorkflow.set_surfaces,
            [_rect_patch("wall-1", 1.8, 2.4), _rect_patch("floor", 2.4, 1.8, kind="floor")],
        )
        await handle.signal(TileProjectWorkflow.set_materials, args=[_wall_spec(), _floor_spec()])
        await handle.signal(TileProjectWorkflow.request_render)

        # Wait for first render failure to park the workflow.
        state = None
        for _ in range(300):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.error is not None:
                break
        assert state is not None, "workflow never parked on render failure"
        assert state.error is not None
        assert state.render_attempt_count == 1
        # The counting activity reports >=1 (Temporal's retry policy may
        # cause multiple internal retries per exec_activity call, but
        # workflow-level attempt_count reflects distinct exec_activity calls).
        invocations_after_first = invocation_count
        assert invocations_after_first >= 1

        await handle.signal(TileProjectWorkflow.request_render_retry)
        for _ in range(300):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.render_attempt_count == 2:
                break
        assert state.render_attempt_count == 2
        # The activity was invoked again after the retry signal.
        assert invocation_count > invocations_after_first
        # Error persists (second attempt also failed) — not silently cleared.
        assert state.error is not None
        # Render URL is never populated when the activity fails.
        assert state.render_image_url is None


async def test_cancel_from_scan_phase_terminates_cleanly(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    """cancel_project sent during the scan phase should transition the
    workflow to the 'abandoned' step (the _wait helper treats cancel during
    a wait as abandonment)."""
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        await handle.signal(TileProjectWorkflow.cancel_project)

        state = None
        for _ in range(30):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step in {"abandoned", "cancelled"}:
                break
        assert state is not None
        assert state.step in {"abandoned", "cancelled"}


async def test_set_policies_during_render_phase_recomputes_estimate(
    workflow_env: WorkflowEnvironment, tq: str
) -> None:
    """Policy updates must refresh the estimate even outside the estimate
    phase (e.g. during render or export). Prior to the PR review fix,
    set_policies only recomputed when step == 'replace_material_estimate',
    which left iOS showing a stale box count after the user edited the
    overage policy post-render.
    """
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        await handle.signal(
            TileProjectWorkflow.set_surfaces,
            [_rect_patch("wall-1", 2.4, 2.4), _rect_patch("floor", 2.4, 1.8, kind="floor")],
        )
        await handle.signal(TileProjectWorkflow.set_materials, args=[_wall_spec(), _floor_spec()])
        await handle.signal(TileProjectWorkflow.request_render)

        # Advance into render phase (render activity will fail since it's a
        # stub, but we only need the workflow to be past estimate).
        state = None
        for _ in range(60):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.step == "replace_material_render":
                break
        assert state is not None and state.step == "replace_material_render"
        assert state.estimate is not None
        initial_wall_boxes = state.estimate.wall_boxes_total
        initial_wall_tiles = state.estimate.wall_tiles_total
        assert state.estimate.overage_policy is OveragePolicy.FLAT_10

        initial_starting_rule = state.estimate.starting_point_rule
        await handle.signal(
            TileProjectWorkflow.set_policies,
            args=[
                # Keep the starting rule constant — we're testing that overage
                # policy changes propagate, not that rule changes swap the pack.
                OveragePolicy.CONTRACTOR_TIER,
                initial_starting_rule,
                # Perfectionist tier = 18% overage vs FLAT_10's 10% → strictly
                # more tiles_total, and boxes_total >= FLAT_10's.
                ContractorTier.PERFECTIONIST,
            ],
        )
        for _ in range(30):
            await asyncio.sleep(0.01)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate and state.estimate.overage_policy is OveragePolicy.CONTRACTOR_TIER:
                break
        assert state.estimate is not None
        assert state.estimate.overage_policy is OveragePolicy.CONTRACTOR_TIER
        assert state.estimate.contractor_tier is ContractorTier.PERFECTIONIST
        # Same packed count (same starting rule), higher overage → more tiles.
        assert state.estimate.wall_tiles_total > initial_wall_tiles
        # Boxes can equal (if overage didn't push across a box boundary) but
        # must never be LESS.
        assert state.estimate.wall_boxes_total >= initial_wall_boxes


async def test_masks_reduce_tile_count(workflow_env: WorkflowEnvironment, tq: str) -> None:
    """A user-drawn mask on a wall should reduce the packed tile count vs.
    the same wall without the mask.
    """
    async with Worker(
        workflow_env.client,
        task_queue=tq,
        workflows=[TileProjectWorkflow],
        activities=_TILE_ACTIVITIES,
    ):
        handle = await _start_workflow(workflow_env, tq)
        no_mask = [
            _rect_patch("wall-1", 2.4, 2.4),
            _rect_patch("floor", 2.4, 1.8, kind="floor"),
        ]
        await handle.signal(TileProjectWorkflow.set_surfaces, no_mask)
        await handle.signal(
            TileProjectWorkflow.set_materials,
            args=[_wall_spec(), _floor_spec()],
        )

        state = None
        for _ in range(20):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate is not None:
                break
        assert state is not None and state.estimate is not None
        baseline_wall_tiles = state.estimate.wall_tiles_total

        # Replace surfaces with a masked version of wall-1. Mask must be
        # wider than one full tile step (0.303m) and taller than one full
        # tile step (0.603m) to guarantee ≥1 tile is fully inside it.
        with_mask = [
            _rect_patch(
                "wall-1",
                2.4,
                2.4,
                holes=[
                    Hole(
                        x_m=0.3,
                        y_m=0.3,
                        width_m=1.2,
                        height_m=1.5,
                        label="niche",
                        kind=HoleKind.MASK,
                    )
                ],
            ),
            _rect_patch("floor", 2.4, 1.8, kind="floor"),
        ]
        await handle.signal(TileProjectWorkflow.set_surfaces, with_mask)

        state = None
        for _ in range(20):
            await asyncio.sleep(0)
            state = await handle.query(TileProjectWorkflow.get_state)
            if state.estimate is not None and state.estimate.wall_tiles_total < baseline_wall_tiles:
                break
        assert state is not None and state.estimate is not None
        assert state.estimate.wall_tiles_total < baseline_wall_tiles
