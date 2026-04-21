"""TileProjectWorkflow — one instance per Replace-Material project.

Parallel to DesignProjectWorkflow. Phases:
  replace_material_scan   → wait for >=1 surface (via set_surfaces signal)
  replace_material_specs  → wait for wall + floor materials (set_materials)
  replace_material_estimate → compute estimate inline (pure math), then wait
                              for confirm_estimate before rendering
  replace_material_render → call render_tile_design activity (stub in PR 1)
  replace_material_export → call generate_cut_sheet_pdf activity (stub in PR 1)
  completed               → 24h purge timer

Signals map 1:1 to the iOS flow. `set_policies` can be sent repeatedly during
the estimate phase to persist the user's chosen overage + starting-point rule.
The client-side live toggle re-runs the math locally; the server uses
whichever policy is current when render is requested.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.activities.purge import purge_project_data
    from app.activities.tile_cutsheet import generate_cut_sheet_pdf
    from app.activities.tile_pack import build_tile_estimate
    from app.activities.tile_render import render_tile_design
    from app.models.contracts import (
        ContractorTier,
        GenerateCutSheetInput,
        MaterialSpec,
        OveragePolicy,
        RenderTileInput,
        StartingPointRule,
        SurfacePatch,
        TileModeEstimate,
        TileModeInput,
        TileWorkflowState,
        WorkflowError,
    )


_ESTIMATE_RETRY = RetryPolicy(maximum_attempts=2)
_RENDER_RETRY = RetryPolicy(maximum_attempts=2)
_CUTSHEET_RETRY = RetryPolicy(maximum_attempts=2)
_PURGE_RETRY = RetryPolicy(maximum_attempts=2)

_ABANDONMENT_TIMEOUT = timedelta(hours=48)


class _AbandonedError(Exception):
    pass


@workflow.defn
class TileProjectWorkflow:
    """One instance per tile-mode project. Workflow ID = project_id."""

    def __init__(self) -> None:
        self._project_id = ""
        self.step = "replace_material_scan"
        self.surfaces: list[SurfacePatch] = []
        self.wall_material: MaterialSpec | None = None
        self.floor_material: MaterialSpec | None = None
        self.overage_policy: OveragePolicy = OveragePolicy.FLAT_10
        self.contractor_tier: ContractorTier | None = None
        self.starting_point_rule: StartingPointRule = StartingPointRule.CENTERED_FOCAL_WALL
        self.estimate: TileModeEstimate | None = None
        self.render_image_url: str | None = None
        self.cut_sheet_pdf_url: str | None = None
        self.error: WorkflowError | None = None
        self._render_requested = False
        self._render_retry_requested = False
        self._export_requested = False
        self._estimate_confirmed = False
        self._cancelled = False
        # Incremented every time a render or cut-sheet activity fails. Surfaced
        # via the query state so iOS can show "Retry (2/3)" and PR 5 can cap it.
        self.render_attempt_count = 0
        self.export_attempt_count = 0

    @workflow.run
    async def run(self, project_id: str) -> None:
        self._project_id = project_id
        try:
            await self._run_phases()
        except _AbandonedError:
            workflow.logger.info(
                "Tile project %s abandoned at step '%s'",
                self._project_id,
                self.step,
            )
            self.step = "abandoned"
        except asyncio.CancelledError:
            workflow.logger.info(
                "Tile project %s cancelled externally at step '%s'",
                self._project_id,
                self.step,
            )
            self.step = "cancelled"
            await self._try_purge()
            raise

    async def _run_phases(self) -> None:
        # --- Phase: Scan — wait for at least one surface ---
        self.step = "replace_material_scan"
        await self._wait(lambda: len(self.surfaces) > 0)

        # --- Phase: Specs — wait for both wall + floor materials ---
        self.step = "replace_material_specs"
        await self._wait(lambda: self.wall_material is not None and self.floor_material is not None)

        # --- Phase: Estimate — compute inline, wait for render request ---
        # confirm_estimate is a soft lock (surfaced via query so UI can show
        # "locked"), NOT a phase trigger. Only request_render moves us forward
        # — consuming a render credit must require an explicit user action.
        self.step = "replace_material_estimate"
        self._recompute_estimate()
        await self._wait(lambda: self._render_requested)

        # --- Phase: Render — call Gemini mask-edit (stub in PR 1) ---
        self.step = "replace_material_render"
        while True:
            self.render_attempt_count += 1
            try:
                render_output = await workflow.execute_activity(
                    render_tile_design,
                    self._render_input(),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_RENDER_RETRY,
                )
                self.render_image_url = render_output.image_url
                self.error = None
                break
            except Exception as exc:
                workflow.logger.warning("render_tile_design failed: %s", exc)
                self.error = WorkflowError(
                    message="Render failed — retry or cancel.",
                    retryable=True,
                )
                # Park until the client sends a retry or cancels. Export is
                # NOT a valid escape from a failed render.
                self._render_retry_requested = False
                await self._wait(lambda: self._render_retry_requested or self._cancelled)
                if self._cancelled:
                    break

        # --- Phase: Export — generate cut-sheet PDF (stub in PR 1) ---
        # Only enter export if the render actually succeeded. A cancelled
        # project exits the loop above with error still set; short-circuit
        # straight to completed.
        if not self._cancelled and self.render_image_url is not None:
            self.step = "replace_material_export"
            # Gate export on the user explicitly requesting it (tapping
            # "Download Cut Sheet"). PR 5 may auto-fire on render completion.
            await self._wait(lambda: self._export_requested or self._cancelled)
            while not self._cancelled:
                self.export_attempt_count += 1
                try:
                    cutsheet_output = await workflow.execute_activity(
                        generate_cut_sheet_pdf,
                        self._cutsheet_input(),
                        start_to_close_timeout=timedelta(minutes=5),
                        retry_policy=_CUTSHEET_RETRY,
                    )
                    self.cut_sheet_pdf_url = cutsheet_output.pdf_url
                    self.error = None
                    break
                except Exception as exc:
                    workflow.logger.warning("generate_cut_sheet_pdf failed: %s", exc)
                    self.error = WorkflowError(
                        message="Cut-sheet PDF failed — retry or cancel.",
                        retryable=True,
                    )
                    self._export_requested = False
                    await self._wait(lambda: self._export_requested or self._cancelled)

        # --- Phase: Completed + 24h purge timer ---
        self.step = "completed"
        with contextlib.suppress(TimeoutError):
            await workflow.wait_condition(lambda: self._cancelled, timeout=timedelta(hours=24))
        await self._try_purge()

    def _recompute_estimate(self) -> None:
        """Recompute the estimate in-process. Pure deterministic math — safe
        to call inside the workflow without going through an activity.
        """
        assert self.wall_material is not None
        assert self.floor_material is not None
        tile_input = TileModeInput(
            surfaces=self.surfaces,
            wall_material=self.wall_material,
            floor_material=self.floor_material,
            overage_policy=self.overage_policy,
            contractor_tier=self.contractor_tier or ContractorTier.PRO,
            starting_point_rule=self.starting_point_rule,
        )
        self.estimate = build_tile_estimate(tile_input)

    def _render_input(self) -> RenderTileInput:
        assert self.wall_material is not None
        assert self.floor_material is not None
        return RenderTileInput(
            project_id=self._project_id,
            surfaces=self.surfaces,
            wall_material=self.wall_material,
            floor_material=self.floor_material,
        )

    def _cutsheet_input(self) -> GenerateCutSheetInput:
        assert self.estimate is not None
        return GenerateCutSheetInput(
            project_id=self._project_id,
            estimate=self.estimate,
            surfaces=self.surfaces,
        )

    async def _wait(
        self,
        condition: Any,
        timeout: timedelta = _ABANDONMENT_TIMEOUT,
    ) -> None:
        try:
            await workflow.wait_condition(lambda: condition() or self._cancelled, timeout=timeout)
        except TimeoutError:
            await self._try_purge()
            raise _AbandonedError() from None
        if self._cancelled:
            await self._try_purge()
            raise _AbandonedError()

    async def _try_purge(self) -> None:
        try:
            await workflow.execute_activity(
                purge_project_data,
                self._project_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_PURGE_RETRY,
            )
        except BaseException as exc:
            workflow.logger.error(
                "purge_project_data failed for tile project %s: %s: %s",
                self._project_id,
                type(exc).__name__,
                exc,
            )

    # --- Signals ---

    @workflow.signal
    async def set_surfaces(self, surfaces: list[SurfacePatch]) -> None:
        self.surfaces = surfaces
        # If we've already entered estimate phase, recompute.
        if self.wall_material is not None and self.floor_material is not None:
            self._recompute_estimate()

    @workflow.signal
    async def set_materials(self, wall: MaterialSpec, floor: MaterialSpec) -> None:
        self.wall_material = wall
        self.floor_material = floor
        # Same recompute-in-every-phase rule as set_policies and set_surfaces
        # (Codex P1 on commit 25e0b35 — materials can change during render/
        # export, and the stored estimate must track the stored materials).
        if len(self.surfaces) > 0:
            self._recompute_estimate()

    @workflow.signal
    async def set_policies(
        self,
        overage_policy: OveragePolicy,
        starting_point_rule: StartingPointRule,
        contractor_tier: ContractorTier | None = None,
    ) -> None:
        self.overage_policy = overage_policy
        self.starting_point_rule = starting_point_rule
        self.contractor_tier = contractor_tier
        # Recompute whenever materials are known — the saved estimate must
        # never be stale relative to the policy the client sees. iOS can send
        # set_policies during render/export (e.g. user edits the cut sheet
        # policy after seeing the render) and expects a fresh estimate.
        if self.wall_material is not None and self.floor_material is not None:
            self._recompute_estimate()

    @workflow.signal
    async def confirm_estimate(self) -> None:
        self._estimate_confirmed = True

    @workflow.signal
    async def request_render(self) -> None:
        self._render_requested = True

    @workflow.signal
    async def request_render_retry(self) -> None:
        """Sent after a render failure to re-attempt the Gemini call."""
        self._render_retry_requested = True

    @workflow.signal
    async def request_export(self) -> None:
        self._export_requested = True

    @workflow.signal
    async def cancel_project(self) -> None:
        self._cancelled = True

    # --- Query ---

    @workflow.query
    def get_state(self) -> TileWorkflowState:
        return TileWorkflowState(
            step=self.step,
            surfaces=self.surfaces,
            wall_material=self.wall_material,
            floor_material=self.floor_material,
            overage_policy=self.overage_policy,
            contractor_tier=self.contractor_tier,
            starting_point_rule=self.starting_point_rule,
            estimate=self.estimate,
            render_image_url=self.render_image_url,
            cut_sheet_pdf_url=self.cut_sheet_pdf_url,
            error=self.error,
            render_attempt_count=self.render_attempt_count,
            export_attempt_count=self.export_attempt_count,
            estimate_confirmed=self._estimate_confirmed,
        )
