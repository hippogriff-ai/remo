"""DesignProjectWorkflow — one instance per design project.

Workflow ID = project_id. Owns all state transitions.
Signals drive user actions; queries expose state for polling.
Activities are real AI services (T2 image gen, T3 AI agents).
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

# Activity imports are Temporal function references for execute_activity().
# The actual implementation is determined by which activities the Worker registers
# (mock_stubs for local dev, real modules for production — see worker.py).
with workflow.unsafe.imports_passed_through():
    from app.activities.edit import edit_design
    from app.activities.generate import generate_designs
    from app.activities.purge import purge_project_data
    from app.activities.shopping import generate_shopping_list
    from app.models.contracts import (
        AnnotationRegion,
        DesignBrief,
        DesignOption,
        EditDesignInput,
        GenerateDesignsInput,
        GenerateShoppingListInput,
        InspirationNote,
        PhotoData,
        RevisionRecord,
        ScanData,
        WorkflowError,
        WorkflowState,
    )


# Retry policies per activity type — tune counts when real activities are wired.
_GENERATION_RETRY = RetryPolicy(maximum_attempts=2)
_EDIT_RETRY = RetryPolicy(maximum_attempts=2)
_SHOPPING_RETRY = RetryPolicy(maximum_attempts=2)
_PURGE_RETRY = RetryPolicy(maximum_attempts=2)

_ABANDONMENT_TIMEOUT = timedelta(hours=48)


class _AbandonedError(Exception):
    pass


@workflow.defn
class DesignProjectWorkflow:
    """One instance per design project. Workflow ID = project_id."""

    def __init__(self) -> None:
        self._project_id = ""
        self.step = "photos"
        self.photos: list[PhotoData] = []
        self.scan_data: ScanData | None = None
        self.scan_skipped = False
        self.intake_skipped = False
        self.design_brief: DesignBrief | None = None
        self.generated_options: list[DesignOption] = []
        self.selected_option: int | None = None
        self.current_image: str | None = None
        self.revision_history: list[RevisionRecord] = []
        self.iteration_count = 0
        self.shopping_list = None
        self.approved = False
        self.error: WorkflowError | None = None
        self.chat_history_key: str | None = None
        self._action_queue: list[tuple[str, Any]] = []
        self._restart_requested = False
        self._cancelled = False

    @workflow.run
    async def run(self, project_id: str) -> None:
        self._project_id = project_id
        try:
            await self._run_phases()
        except _AbandonedError:
            workflow.logger.info(
                "Project %s abandoned at step '%s'",
                self._project_id,
                self.step,
            )
            self.step = "abandoned"
        except asyncio.CancelledError:
            workflow.logger.info(
                "Project %s cancelled externally at step '%s'",
                self._project_id,
                self.step,
            )
            self.step = "cancelled"
            await self._try_purge()
            raise

    async def _run_phases(self) -> None:
        # --- Phase: Photos (need >= 2 room photos) ---
        await self._wait(lambda: sum(1 for p in self.photos if p.photo_type == "room") >= 2)

        # --- Phase: Scan ---
        self.step = "scan"
        await self._wait(lambda: self.scan_data is not None or self.scan_skipped)

        # --- Phase: Intake -> Generation -> Selection -> Iteration (with Start Over loop) ---
        while True:
            self.step = "intake"
            self._restart_requested = False
            await self._wait(lambda: self.design_brief is not None or self.intake_skipped)

            # --- Generation ---
            self.step = "generation"
            try:
                result = await workflow.execute_activity(
                    generate_designs,
                    self._generation_input(),
                    start_to_close_timeout=timedelta(minutes=3),
                    retry_policy=_GENERATION_RETRY,
                )
                if self._restart_requested:
                    continue
                self.generated_options = result.options
                self.error = None
            except ActivityError as exc:
                workflow.logger.error(
                    "generate_designs failed: %s",
                    exc,
                )
                self.error = WorkflowError(
                    message="Design generation failed — please retry",
                    retryable=True,
                )
                await self._wait(lambda: self.error is None or self._restart_requested)
                continue

            # --- Selection ---
            self.step = "selection"
            await self._wait(lambda: self.selected_option is not None or self._restart_requested)
            if self._restart_requested:
                continue

            assert self.selected_option is not None  # guaranteed by wait condition
            self.current_image = self.generated_options[self.selected_option].image_url

            # --- Iteration (up to 5 rounds) ---
            self.step = "iteration"
            while self.iteration_count < 5 and not self.approved:
                await self._wait(
                    lambda: len(self._action_queue) > 0 or self.approved or self._restart_requested
                )
                if self.approved or self._restart_requested:
                    break

                action_type, payload = self._action_queue.pop(0)
                try:
                    result = await workflow.execute_activity(
                        edit_design,
                        self._edit_input(action_type, payload),
                        start_to_close_timeout=timedelta(minutes=3),
                        retry_policy=_EDIT_RETRY,
                    )
                    # If start_over was signaled while the activity was
                    # in-flight, state has been cleared — discard the stale
                    # result so the next cycle starts clean.
                    if self._restart_requested:
                        break
                    revision_num = self.iteration_count + 1
                    self.revision_history.append(
                        RevisionRecord(
                            revision_number=revision_num,
                            type=action_type,
                            base_image_url=self.current_image or "",
                            revised_image_url=result.revised_image_url,
                            instructions=self._extract_instructions(action_type, payload),
                        )
                    )
                    self.current_image = result.revised_image_url
                    self.chat_history_key = result.chat_history_key
                    self.iteration_count = revision_num
                    self.error = None
                except ActivityError as exc:
                    workflow.logger.error(
                        "Iteration %s failed: %s",
                        action_type,
                        exc,
                    )
                    self._action_queue.insert(0, (action_type, payload))
                    self.error = WorkflowError(
                        message="Revision failed — please retry",
                        retryable=True,
                    )
                    await self._wait(lambda: self.error is None or self._restart_requested)
                except (ValueError, TypeError) as exc:
                    # Input validation (e.g. malformed annotations, invalid
                    # feedback) — not retryable with same payload, so discard the
                    # action (don't re-queue).  Pydantic's ValidationError is a
                    # ValueError subclass, so this catches model construction failures
                    # while letting workflow bugs (AttributeError, etc.) crash the
                    # task for investigation.
                    workflow.logger.error(
                        "Invalid %s input: %s",
                        action_type,
                        exc,
                    )
                    self.error = WorkflowError(
                        message="Invalid edit request — please resubmit",
                        retryable=True,
                    )
                    await self._wait(lambda: self.error is None or self._restart_requested)

            if self._restart_requested:
                continue  # back to intake

            # --- Phase: Approval ---
            if not self.approved:
                self.step = "approval"
                await self._wait(lambda: self.approved or self._restart_requested)
                if self._restart_requested:
                    continue  # back to intake

            break  # proceed to shopping

        # --- Phase: Shopping List ---
        self.step = "shopping"
        while self.shopping_list is None:
            try:
                self.shopping_list = await workflow.execute_activity(
                    generate_shopping_list,
                    self._shopping_input(),
                    start_to_close_timeout=timedelta(minutes=5),
                    retry_policy=_SHOPPING_RETRY,
                )
                self.error = None
            except ActivityError as exc:
                workflow.logger.error(
                    "generate_shopping_list failed: %s",
                    exc,
                )
                self.error = WorkflowError(
                    message="Shopping list failed — please retry",
                    retryable=True,
                )
                await self._wait(lambda: self.error is None)

        # --- Phase: Completed + 24h purge timer ---
        self.step = "completed"
        with contextlib.suppress(TimeoutError):
            await workflow.wait_condition(lambda: self._cancelled, timeout=timedelta(hours=24))
        await self._try_purge()

    async def _wait(self, condition: Any, timeout: timedelta = _ABANDONMENT_TIMEOUT) -> None:
        try:
            await workflow.wait_condition(lambda: condition() or self._cancelled, timeout=timeout)
        except TimeoutError:
            await self._try_purge()
            raise _AbandonedError() from None
        if self._cancelled:
            await self._try_purge()
            raise _AbandonedError()

    async def _try_purge(self) -> None:
        """Best-effort purge — logs failure but never blocks abandonment.

        Uses ``except BaseException`` because ``asyncio.CancelledError`` is a
        ``BaseException`` (not ``Exception``) in Python 3.9+. The purge must
        not prevent the caller from completing abandonment or cancellation.
        """
        try:
            await workflow.execute_activity(
                purge_project_data,
                self._project_id,
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_PURGE_RETRY,
            )
        except BaseException as exc:
            workflow.logger.error(
                "purge_project_data failed for project %s: %s: %s",
                self._project_id,
                type(exc).__name__,
                exc,
            )

    # --- Signals ---

    @workflow.signal
    async def add_photo(self, photo: PhotoData) -> None:
        self.photos.append(photo)

    @workflow.signal
    async def remove_photo(self, photo_id: str) -> None:
        before = len(self.photos)
        self.photos = [p for p in self.photos if p.photo_id != photo_id]
        if len(self.photos) == before:
            workflow.logger.warning(
                "remove_photo: photo_id %s not found for project %s",
                photo_id,
                self._project_id,
            )

    @workflow.signal
    async def complete_scan(self, scan: ScanData) -> None:
        self.scan_data = scan

    @workflow.signal
    async def skip_scan(self) -> None:
        self.scan_skipped = True

    @workflow.signal
    async def complete_intake(self, brief: DesignBrief) -> None:
        self.design_brief = brief

    @workflow.signal
    async def skip_intake(self) -> None:
        self.intake_skipped = True

    @workflow.signal
    async def select_option(self, index: int) -> None:
        if self.step != "selection":
            workflow.logger.warning(
                "select_option ignored: step is '%s' (expected 'selection') for project %s",
                self.step,
                self._project_id,
            )
            return
        if 0 <= index < len(self.generated_options):
            self.selected_option = index
        else:
            workflow.logger.warning(
                "select_option: index %d out of range (have %d options)",
                index,
                len(self.generated_options),
            )
            self.error = WorkflowError(
                message=f"Invalid selection: option {index} does not exist",
                retryable=True,
            )

    @workflow.signal
    async def start_over(self) -> None:
        if self.approved or self.step in ("shopping", "completed", "abandoned", "cancelled"):
            workflow.logger.warning(
                "start_over ignored: already at step '%s' (approved=%s) for project %s",
                self.step,
                self.approved,
                self._project_id,
            )
            return
        self._restart_requested = True
        # Clear all cycle state so the while-loop restarts cleanly from
        # any phase (intake, generation error, selection, or iteration).
        # Note: photos, scan_data, and scan_skipped are preserved across
        # restarts since re-scanning is expensive and photos are reusable.
        self.approved = False
        self.generated_options = []
        self.selected_option = None
        self.current_image = None
        self.design_brief = None
        self.intake_skipped = False
        self.revision_history = []
        self.iteration_count = 0
        self.chat_history_key = None
        self._action_queue.clear()
        self.error = None

    @workflow.signal
    async def submit_annotation_edit(self, annotations: list[dict[str, Any]]) -> None:
        self._action_queue.append(("annotation", annotations))

    @workflow.signal
    async def submit_text_feedback(self, feedback: str) -> None:
        self._action_queue.append(("feedback", feedback))

    @workflow.signal
    async def approve_design(self) -> None:
        if self.step not in ("iteration", "approval"):
            workflow.logger.warning(
                "approve_design ignored: step is '%s' for project %s",
                self.step,
                self._project_id,
            )
            return
        if self.error is not None:
            workflow.logger.warning(
                "approve_design ignored: active error for project %s",
                self._project_id,
            )
            return
        self.approved = True

    @workflow.signal
    async def retry_failed_step(self) -> None:
        self.error = None

    @workflow.signal
    async def cancel_project(self) -> None:
        self._cancelled = True

    # --- Query ---

    @workflow.query
    def get_state(self) -> WorkflowState:
        return WorkflowState(
            step=self.step,
            photos=self.photos,
            scan_data=self.scan_data,
            design_brief=self.design_brief,
            generated_options=self.generated_options,
            selected_option=self.selected_option,
            current_image=self.current_image,
            revision_history=self.revision_history,
            iteration_count=self.iteration_count,
            shopping_list=self.shopping_list,
            approved=self.approved,
            error=self.error,
            chat_history_key=self.chat_history_key,
        )

    # --- Input builders ---

    def _generation_input(self) -> GenerateDesignsInput:
        inspiration_photos = [p for p in self.photos if p.photo_type == "inspiration"]
        # Prefer agent-assembled notes from the design brief; fall back to
        # raw photo notes (e.g. when intake is skipped but user attached notes
        # to inspiration photos via PHOTO-7).
        if self.design_brief and self.design_brief.inspiration_notes:
            notes = self.design_brief.inspiration_notes
        else:
            notes = [
                InspirationNote(photo_index=i, note=p.note)
                for i, p in enumerate(inspiration_photos)
                if p.note
            ]
        return GenerateDesignsInput(
            room_photo_urls=[p.storage_key for p in self.photos if p.photo_type == "room"],
            inspiration_photo_urls=[p.storage_key for p in inspiration_photos],
            inspiration_notes=notes,
            design_brief=self.design_brief,
            room_dimensions=self.scan_data.room_dimensions if self.scan_data else None,
        )

    def _edit_input(self, action_type: str, payload: Any) -> EditDesignInput:
        assert self.current_image is not None
        base = EditDesignInput(
            project_id=self._project_id,
            base_image_url=self.current_image,
            room_photo_urls=[p.storage_key for p in self.photos if p.photo_type == "room"],
            inspiration_photo_urls=[
                p.storage_key for p in self.photos if p.photo_type == "inspiration"
            ],
            design_brief=self.design_brief,
            chat_history_key=self.chat_history_key,
        )
        if action_type == "annotation":
            base.annotations = [
                AnnotationRegion(**r) if isinstance(r, dict) else r for r in payload
            ]
        elif action_type == "feedback":
            base.feedback = payload
        else:
            raise ValueError(f"Unknown edit action type: {action_type!r}")
        return base

    def _extract_instructions(self, action_type: str, payload: Any) -> list[str]:
        if action_type == "annotation":
            return [(r["instruction"] if isinstance(r, dict) else r.instruction) for r in payload]
        if action_type == "feedback":
            return [payload]
        raise ValueError(f"Unknown edit action type: {action_type!r}")

    def _shopping_input(self) -> GenerateShoppingListInput:
        assert self.current_image is not None
        return GenerateShoppingListInput(
            design_image_url=self.current_image,
            original_room_photo_urls=[p.storage_key for p in self.photos if p.photo_type == "room"],
            design_brief=self.design_brief,
            revision_history=self.revision_history,
            room_dimensions=self.scan_data.room_dimensions if self.scan_data else None,
        )
