"""Tests for DesignProjectWorkflow — verifies signal/query/transition behavior.

Success metric: Workflow transitions through all steps with test signals.
"""

import asyncio
import contextlib
import uuid

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.activities.mock_stubs import (
    analyze_room_photos,
    edit_design,
    generate_designs,
    generate_shopping_list,
    purge_project_data,
)
from app.models.contracts import (
    AnnotationRegion,
    DesignBrief,
    DesignOption,
    EditDesignInput,
    EditDesignOutput,
    GenerateDesignsInput,
    GenerateDesignsOutput,
    GenerateShoppingListInput,
    GenerateShoppingListOutput,
    InspirationNote,
    PhotoData,
    ProductMatch,
    RoomDimensions,
    ScanData,
)
from app.workflows.design_project import DesignProjectWorkflow

ALL_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]

pytestmark = pytest.mark.asyncio(loop_scope="module")


# ---------------------------------------------------------------------------
# Unit tests for private helpers (no Temporal needed)
# ---------------------------------------------------------------------------


class TestEditInputGuards:
    """Verify ValueError guards on _edit_input and _extract_instructions."""

    def _make_workflow(self) -> DesignProjectWorkflow:
        wf = DesignProjectWorkflow.__new__(DesignProjectWorkflow)
        wf.__init__()
        wf._project_id = "test-proj"
        wf.current_image = "https://example.com/img.png"
        wf.photos = [PhotoData(photo_id="r1", storage_key="k1", photo_type="room")]
        wf.design_brief = None
        wf.chat_history_key = None
        return wf

    def test_edit_input_rejects_unknown_action_type(self) -> None:
        wf = self._make_workflow()
        with pytest.raises(ValueError, match="Unknown edit action type"):
            wf._edit_input("bogus", "payload")

    def test_extract_instructions_rejects_unknown_action_type(self) -> None:
        wf = self._make_workflow()
        with pytest.raises(ValueError, match="Unknown edit action type"):
            wf._extract_instructions("bogus", "payload")


@pytest.fixture(scope="module")
async def workflow_env():
    """Module-scoped time-skipping environment — one JVM for all workflow tests."""
    async with await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter,
    ) as env:
        yield env


@pytest.fixture
def tq():
    """Per-test unique task queue — isolates workflows so time-skipping isn't blocked."""
    return f"test-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def _cleanup_workflows():
    """Terminate leftover workflows after each test to prevent time-skipping hangs.

    Without this, workflows that don't run to completion (tests that check
    intermediate state) remain 'active' on the shared time-skipping server
    and block global time advancement for subsequent tests.

    Must be sync to avoid requesting a function-scoped event loop that
    conflicts with the module-scoped loop from pytestmark.
    """
    _test_handles.clear()
    yield
    if not _test_handles:
        return

    async def _terminate_all():
        for h in _test_handles:
            with contextlib.suppress(Exception):
                await h.terminate("test cleanup")

    asyncio.get_event_loop().run_until_complete(_terminate_all())
    _test_handles.clear()


def _photo(index: int = 0) -> PhotoData:
    """Helper to create a test photo."""
    return PhotoData(
        photo_id=str(uuid.uuid4()),
        storage_key=f"projects/test/photos/room_{index}.jpg",
        photo_type="room",
    )


def _scan() -> ScanData:
    """Helper to create a test scan."""
    return ScanData(storage_key="projects/test/lidar/dimensions.json")


def _brief() -> DesignBrief:
    """Helper to create a test design brief."""
    return DesignBrief(room_type="living room")


def _annotations() -> list[dict]:
    """Helper to create a test annotation edit payload."""
    return [
        AnnotationRegion(
            region_id=1,
            center_x=0.5,
            center_y=0.5,
            radius=0.2,
            instruction="Replace the couch with a modern sectional",
        ).model_dump()
    ]


_test_handles: list = []


async def _start_workflow(env, tq):
    """Start workflow and return handle (also registers for post-test cleanup)."""
    project_id = str(uuid.uuid4())
    handle = await env.client.start_workflow(
        DesignProjectWorkflow.run,
        project_id,
        id=project_id,
        task_queue=tq,
    )
    _test_handles.append(handle)
    return handle


async def _advance_to_iteration(handle):
    """Send signals to advance workflow from photos through to iteration step."""
    await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
    await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
    await handle.signal(DesignProjectWorkflow.skip_scan)
    await handle.signal(DesignProjectWorkflow.skip_intake)
    # Wait for generation activity to complete and reach selection
    await asyncio.sleep(0.5)
    await handle.signal(DesignProjectWorkflow.select_option, 0)
    await asyncio.sleep(0.5)


# --- Failing activity stubs for error recovery tests ---


@activity.defn(name="generate_designs")
async def _failing_generate(_input) -> None:
    """Always-failing generate_designs for error testing."""
    raise RuntimeError("AI service unavailable")


@activity.defn(name="edit_design")
async def _failing_edit(_input) -> None:
    """Always-failing edit_design for error testing."""
    raise RuntimeError("Edit service error")


@activity.defn(name="generate_shopping_list")
async def _failing_shopping(_input) -> None:
    """Always-failing generate_shopping_list for error testing."""
    raise RuntimeError("Shopping service error")


_FAILING_GENERATION_ACTIVITIES = [
    analyze_room_photos,
    _failing_generate,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]

_FAILING_EDIT_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    _failing_edit,
    generate_shopping_list,
    purge_project_data,
]

_FAILING_SHOPPING_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    edit_design,
    _failing_shopping,
    purge_project_data,
]

# --- Flaky stubs: fail first workflow-level attempt, succeed on retry ---

_flaky_generate_calls = 0


@activity.defn(name="generate_designs")
async def _flaky_generate(_input) -> GenerateDesignsOutput:
    """Fails first 2 calls (exhausting Temporal RetryPolicy), succeeds after.

    With maximum_attempts=2, the first workflow-level attempt consumes 2 activity
    calls that both fail, raising ActivityError.  After retry_failed_step, the
    second workflow-level attempt succeeds.
    """
    global _flaky_generate_calls
    _flaky_generate_calls += 1
    if _flaky_generate_calls <= 2:
        raise RuntimeError("Temporary AI service error")
    return GenerateDesignsOutput(
        options=[
            DesignOption(image_url="https://r2.example.com/retry/opt0.png", caption="Retry A"),
            DesignOption(image_url="https://r2.example.com/retry/opt1.png", caption="Retry B"),
        ]
    )


_FLAKY_GENERATION_ACTIVITIES = [
    analyze_room_photos,
    _flaky_generate,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]


_flaky_shopping_calls = 0


@activity.defn(name="generate_shopping_list")
async def _flaky_shopping(_input) -> GenerateShoppingListOutput:
    """Fails first 2 calls, succeeds after — for shopping retry testing."""
    global _flaky_shopping_calls
    _flaky_shopping_calls += 1
    if _flaky_shopping_calls <= 2:
        raise RuntimeError("Temporary shopping service error")
    return GenerateShoppingListOutput(
        items=[
            ProductMatch(
                category_group="Furniture",
                product_name="Retry Chair",
                retailer="Retry Store",
                price_cents=9999,
                product_url="https://example.com/retry-chair",
                confidence_score=0.8,
                why_matched="Retry match",
            ),
        ],
        total_estimated_cost_cents=9999,
    )


_FLAKY_SHOPPING_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    edit_design,
    _flaky_shopping,
    purge_project_data,
]


_flaky_edit_calls = 0


@activity.defn(name="edit_design")
async def _flaky_edit(_input) -> EditDesignOutput:
    """Fails first 2 calls (exhausting RetryPolicy), succeeds after.

    Used to test the retry→approve flow: first attempt fails (error surfaces),
    user retries, second attempt succeeds (no error), user can then approve.
    """
    global _flaky_edit_calls
    _flaky_edit_calls += 1
    if _flaky_edit_calls <= 2:
        raise RuntimeError("Temporary edit service error")
    return EditDesignOutput(
        revised_image_url="https://r2.example.com/retry/edit.png",
        chat_history_key="chat/retry-key.json",
    )


_FLAKY_EDIT_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    _flaky_edit,
    generate_shopping_list,
    purge_project_data,
]


# --- Capturing stub: records generation input for verification ---

_captured_generation_input: GenerateDesignsInput | None = None


@activity.defn(name="generate_designs")
async def _capturing_generate(input: GenerateDesignsInput) -> GenerateDesignsOutput:
    """Captures generation input for verification, then returns mock output."""
    global _captured_generation_input
    _captured_generation_input = input
    return GenerateDesignsOutput(
        options=[
            DesignOption(image_url="https://r2.example.com/cap/opt0.png", caption="Cap A"),
            DesignOption(image_url="https://r2.example.com/cap/opt1.png", caption="Cap B"),
        ]
    )


_CAPTURING_GEN_ACTIVITIES = [
    analyze_room_photos,
    _capturing_generate,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]

# --- Capturing stub: records shopping input for verification ---

_captured_shopping_input: GenerateShoppingListInput | None = None


@activity.defn(name="generate_shopping_list")
async def _capturing_shopping(input: GenerateShoppingListInput) -> GenerateShoppingListOutput:
    """Captures shopping input for verification, then returns mock output."""
    global _captured_shopping_input
    _captured_shopping_input = input
    return GenerateShoppingListOutput(
        items=[
            ProductMatch(
                category_group="Furniture",
                product_name="Captured Chair",
                retailer="Test Store",
                price_cents=5000,
                product_url="https://example.com/captured",
                confidence_score=0.85,
                why_matched="Test match",
            ),
        ],
        total_estimated_cost_cents=5000,
    )


_CAPTURING_SHOPPING_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    edit_design,
    _capturing_shopping,
    purge_project_data,
]

# --- Capturing stub: records edit_design input for verification ---

_captured_edit_input: EditDesignInput | None = None


@activity.defn(name="edit_design")
async def _capturing_edit(
    input: EditDesignInput,
) -> EditDesignOutput:
    """Captures edit_design input for verification, then returns mock output."""
    global _captured_edit_input
    _captured_edit_input = input
    return EditDesignOutput(
        revised_image_url="https://r2.example.com/cap/edit.png",
        chat_history_key="chat/cap-key.json",
    )


_CAPTURING_EDIT_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    _capturing_edit,
    generate_shopping_list,
    purge_project_data,
]


# --- Slow stubs: simulate in-flight activities for signal-during-activity tests ---


@activity.defn(name="edit_design")
async def _slow_edit(_input: EditDesignInput) -> EditDesignOutput:
    """Slow edit_design that gives time for signals to arrive during execution."""
    await asyncio.sleep(2)
    return EditDesignOutput(
        revised_image_url="https://r2.example.com/slow/edit.png",
        chat_history_key="chat/slow-key.json",
    )


_SLOW_EDIT_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    _slow_edit,
    generate_shopping_list,
    purge_project_data,
]


@activity.defn(name="generate_designs")
async def _slow_generate(_input: GenerateDesignsInput) -> GenerateDesignsOutput:
    """Slow generate_designs that gives time for signals to arrive during execution."""
    await asyncio.sleep(2)
    return GenerateDesignsOutput(
        options=[
            DesignOption(image_url="https://r2.example.com/slow/option_0.png", caption="Slow A"),
            DesignOption(image_url="https://r2.example.com/slow/option_1.png", caption="Slow B"),
        ]
    )


_SLOW_GENERATE_ACTIVITIES = [
    analyze_room_photos,
    _slow_generate,
    edit_design,
    generate_shopping_list,
    purge_project_data,
]


# --- Failing purge stub: simulates R2 outage during purge ---


@activity.defn(name="purge_project_data")
async def _failing_purge(_project_id: str) -> None:
    """Always-failing purge for resilience testing."""
    raise RuntimeError("R2 storage unavailable")


_FAILING_PURGE_ACTIVITIES = [
    analyze_room_photos,
    generate_designs,
    edit_design,
    generate_shopping_list,
    _failing_purge,
]


# --- Tests ---


class TestWorkflowHappyPath:
    """Full happy-path: photos -> scan -> intake -> generate -> select -> iterate -> approve."""

    async def test_full_happy_path(self, workflow_env, tq):
        """Verifies the complete workflow transitions through all steps to completion."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Photos phase — need 2 photos
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "photos"

            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            # Scan phase
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"

            await handle.signal(DesignProjectWorkflow.complete_scan, _scan())
            await asyncio.sleep(0.5)

            # Intake phase — complete_intake stores the DesignBrief
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

            brief = _brief()
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(0.5)

            # Generation happens automatically, then selection
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.generated_options) == 2
            assert state.design_brief is not None
            assert state.design_brief.room_type == brief.room_type

            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            # Iteration phase — current_image matches selected option
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.current_image == state.generated_options[0].image_url

            # Approve from iteration
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(0.5)

            # Shopping then completed — verify structure
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.shopping_list is not None
            assert len(state.shopping_list.items) > 0
            assert state.shopping_list.total_estimated_cost_cents > 0


class TestPhotoPhase:
    """Tests for the photo upload phase."""

    async def test_stays_in_photos_with_one_photo(self, workflow_env, tq):
        """Verifies workflow stays in photos step until minimum 2 photos received."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))

            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "photos"
            assert len(state.photos) == 1

    async def test_advances_to_scan_with_two_photos(self, workflow_env, tq):
        """Verifies workflow advances to scan step after 2 photos."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"
            assert len(state.photos) == 2

    async def test_mixed_photo_types_stored_correctly(self, workflow_env, tq):
        """Verifies room and inspiration photos are stored with correct types.

        Requires 2 room photos to advance; inspiration photos are optional.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            room1 = PhotoData(
                photo_id="room-1",
                storage_key="photos/room1.jpg",
                photo_type="room",
            )
            room2 = PhotoData(
                photo_id="room-2",
                storage_key="photos/room2.jpg",
                photo_type="room",
            )
            inspo = PhotoData(
                photo_id="inspo-1",
                storage_key="photos/inspo.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room1)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo)
            await handle.signal(DesignProjectWorkflow.add_photo, room2)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"
            assert len(state.photos) == 3
            room_photos = [p for p in state.photos if p.photo_type == "room"]
            inspo_photos = [p for p in state.photos if p.photo_type == "inspiration"]
            assert len(room_photos) == 2
            assert len(inspo_photos) == 1

    async def test_late_photo_accepted_after_scan_step(self, workflow_env, tq):
        """Verifies add_photo signal works even after workflow advances past photos.

        Temporal signals have no step gate — add_photo simply appends to
        the photos list regardless of current step. This tests that a 3rd
        photo sent during the scan phase is correctly stored. The real
        workflow is more tolerant than the mock API (which rejects photos
        after step="scan" via _check_step).
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"
            assert len(state.photos) == 2

            # Add a 3rd photo during scan step
            late_photo = PhotoData(
                photo_id="late-001",
                storage_key="photos/late.jpg",
                photo_type="room",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, late_photo)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 3
            assert state.photos[2].photo_id == "late-001"


class TestRemovePhoto:
    """Tests for the remove_photo signal — INT-3."""

    async def test_remove_photo_signal(self, workflow_env, tq):
        """Sending remove_photo signal removes the photo from workflow state.

        Covers INT-3 TDD criterion: Workflow signal removes photo from state.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 1

            await handle.signal(DesignProjectWorkflow.remove_photo, state.photos[0].photo_id)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 0

    async def test_remove_photo_preserves_others(self, workflow_env, tq):
        """Removing one photo preserves other photos in state."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            photo_a = PhotoData(
                photo_id="keep-a",
                storage_key="photos/a.jpg",
                photo_type="room",
            )
            photo_b = PhotoData(
                photo_id="remove-b",
                storage_key="photos/b.jpg",
                photo_type="room",
            )
            photo_c = PhotoData(
                photo_id="keep-c",
                storage_key="photos/c.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, photo_a)
            await handle.signal(DesignProjectWorkflow.add_photo, photo_b)
            await handle.signal(DesignProjectWorkflow.add_photo, photo_c)
            await asyncio.sleep(0.3)

            await handle.signal(DesignProjectWorkflow.remove_photo, "remove-b")
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 2
            ids = [p.photo_id for p in state.photos]
            assert "keep-a" in ids
            assert "keep-c" in ids
            assert "remove-b" not in ids

    async def test_remove_nonexistent_photo_is_noop(self, workflow_env, tq):
        """Removing a nonexistent photo_id is a safe no-op in the workflow."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await asyncio.sleep(0.3)

            await handle.signal(DesignProjectWorkflow.remove_photo, "nonexistent-id")
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 1

    async def test_remove_photo_during_scan_does_not_regress_step(self, workflow_env, tq):
        """Removing a photo during scan does NOT regress step back to 'photos'.

        The workflow's step model is forward-only: once _run_phases advances
        past the >= 2 room-photo wait, it never re-evaluates that condition.
        This codifies the expected Temporal behavior (differs from mock API,
        which does regress scan→photos when room count drops below 2).
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            photo_a = PhotoData(
                photo_id="scan-keep",
                storage_key="photos/a.jpg",
                photo_type="room",
            )
            photo_b = PhotoData(
                photo_id="scan-remove",
                storage_key="photos/b.jpg",
                photo_type="room",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, photo_a)
            await handle.signal(DesignProjectWorkflow.add_photo, photo_b)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"
            assert len(state.photos) == 2

            # Remove one room photo — drops below 2
            await handle.signal(DesignProjectWorkflow.remove_photo, "scan-remove")
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.photos) == 1
            # Key assertion: step stays "scan", does NOT regress to "photos"
            assert state.step == "scan"


class TestScanPhase:
    """Tests for the scan phase (complete or skip)."""

    async def test_skip_scan(self, workflow_env, tq):
        """Verifies skip_scan signal advances to intake."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

    async def test_complete_scan(self, workflow_env, tq):
        """Verifies complete_scan signal advances to intake with scan data."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            scan = ScanData(
                storage_key="projects/test/lidar/scan.json",
                room_dimensions=RoomDimensions(
                    width_m=4.5,
                    length_m=6.0,
                    height_m=2.7,
                ),
            )
            await handle.signal(DesignProjectWorkflow.complete_scan, scan)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.scan_data is not None
            assert state.scan_data.room_dimensions is not None
            assert state.scan_data.room_dimensions.width_m == 4.5


class TestIntakePhase:
    """Tests for intake (complete or skip)."""

    async def test_skip_intake_generates_options(self, workflow_env, tq):
        """Verifies skip_intake triggers generation and advances to selection."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.generated_options) == 2

    async def test_complete_intake_stores_brief(self, workflow_env, tq):
        """Verifies complete_intake stores the brief and advances to generation.

        Unlike skip_intake which passes no brief, complete_intake stores a
        DesignBrief in workflow state. T1 iOS sends this after the user
        completes the intake chat. The brief is forwarded to generation.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)

            brief = DesignBrief(
                room_type="kitchen",
                pain_points=["poor layout", "outdated appliances"],
            )
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert state.design_brief is not None
            assert state.design_brief.room_type == "kitchen"
            assert state.design_brief.pain_points == [
                "poor layout",
                "outdated appliances",
            ]
            assert len(state.generated_options) == 2


class TestGenerationInput:
    """Tests verifying the workflow correctly builds generation activity input."""

    async def test_generation_input_separates_photo_types(self, workflow_env, tq):
        """Verifies room and inspiration photos are correctly separated in generation input.

        Uses a capturing stub that records the GenerateDesignsInput. The workflow
        should put room photos in room_photo_urls and inspiration photos in
        inspiration_photo_urls.
        """
        global _captured_generation_input
        _captured_generation_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_GEN_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            room1 = PhotoData(
                photo_id="room-1",
                storage_key="photos/room_0.jpg",
                photo_type="room",
            )
            room2 = PhotoData(
                photo_id="room-2",
                storage_key="photos/room_1.jpg",
                photo_type="room",
            )
            inspo = PhotoData(
                photo_id="inspo-1",
                storage_key="photos/inspo_0.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room1)
            await handle.signal(DesignProjectWorkflow.add_photo, room2)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            assert _captured_generation_input is not None
            assert _captured_generation_input.room_photo_urls == [
                "photos/room_0.jpg",
                "photos/room_1.jpg",
            ]
            assert _captured_generation_input.inspiration_photo_urls == ["photos/inspo_0.jpg"]
            # Skipped intake → no brief, fallback to empty inspiration_notes
            assert _captured_generation_input.design_brief is None
            assert _captured_generation_input.inspiration_notes == []
            # Skipped scan → no dimensions
            assert _captured_generation_input.room_dimensions is None

    async def test_generation_input_includes_brief_and_dimensions(self, workflow_env, tq):
        """Verifies design brief and room dimensions are passed to generation.

        Uses complete_scan (with RoomDimensions) and complete_intake (with brief)
        instead of skip signals, then inspects the captured generation input.
        """
        global _captured_generation_input
        _captured_generation_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_GEN_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            scan = ScanData(
                storage_key="projects/test/lidar/scan.json",
                room_dimensions=RoomDimensions(
                    width_m=4.5,
                    length_m=6.0,
                    height_m=2.7,
                ),
            )
            await handle.signal(DesignProjectWorkflow.complete_scan, scan)
            await asyncio.sleep(0.3)

            brief = DesignBrief(
                room_type="bedroom",
                pain_points=["too dark"],
                inspiration_notes=[
                    InspirationNote(photo_index=0, note="Love the lighting"),
                ],
            )
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            assert _captured_generation_input is not None
            assert _captured_generation_input.design_brief is not None
            assert _captured_generation_input.design_brief.room_type == "bedroom"
            assert _captured_generation_input.design_brief.pain_points == ["too dark"]
            assert _captured_generation_input.room_dimensions is not None
            assert _captured_generation_input.room_dimensions.width_m == 4.5
            assert len(_captured_generation_input.inspiration_notes) == 1
            assert _captured_generation_input.inspiration_notes[0].note == "Love the lighting"

    async def test_generation_input_with_null_dimensions(self, workflow_env, tq):
        """Verifies generation input passes None dimensions when scan has no room data.

        Real-world scenario: LiDAR scan captured but dimension parsing failed
        (e.g. device couldn't extract room boundaries). ScanData exists with a
        storage_key but room_dimensions=None. The generation input builder must
        forward None (not crash or default), so T2's activity can handle both cases.
        """
        global _captured_generation_input
        _captured_generation_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_GEN_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            # Scan with storage_key but NO room_dimensions
            scan = ScanData(
                storage_key="projects/test/lidar/scan.json",
                room_dimensions=None,
            )
            await handle.signal(DesignProjectWorkflow.complete_scan, scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            # scan_data exists but dimensions are None
            assert state.scan_data is not None
            assert state.scan_data.room_dimensions is None

            assert _captured_generation_input is not None
            assert _captured_generation_input.room_dimensions is None

    async def test_photo_notes_fallback_when_intake_skipped(self, workflow_env, tq):
        """IMP-7: Photo notes are passed to generation when intake is skipped.

        When the user uploads inspiration photos with notes but skips intake,
        the design brief is None. The workflow should fall back to extracting
        InspirationNote objects from PhotoData.note so the generation activity
        still gets the user's style preferences.
        """
        global _captured_generation_input
        _captured_generation_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_GEN_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            room1 = PhotoData(
                photo_id="room-1",
                storage_key="photos/room_0.jpg",
                photo_type="room",
            )
            room2 = PhotoData(
                photo_id="room-2",
                storage_key="photos/room_1.jpg",
                photo_type="room",
            )
            inspo_with_note = PhotoData(
                photo_id="inspo-1",
                storage_key="photos/inspo_0.jpg",
                photo_type="inspiration",
                note="Love the warm lighting",
            )
            inspo_no_note = PhotoData(
                photo_id="inspo-2",
                storage_key="photos/inspo_1.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room1)
            await handle.signal(DesignProjectWorkflow.add_photo, room2)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo_with_note)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo_no_note)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            assert _captured_generation_input is not None
            assert _captured_generation_input.design_brief is None
            # Only the photo with a note should produce an InspirationNote
            assert len(_captured_generation_input.inspiration_notes) == 1
            assert _captured_generation_input.inspiration_notes[0].photo_index == 0
            assert _captured_generation_input.inspiration_notes[0].note == "Love the warm lighting"
            # Both inspiration photos should still be in the URL list
            assert len(_captured_generation_input.inspiration_photo_urls) == 2


class TestShoppingInput:
    """Tests verifying the workflow correctly builds shopping list activity input."""

    async def test_shopping_input_includes_revision_history_and_context(self, workflow_env, tq):
        """Verifies shopping input gets full context: revisions, brief, dimensions, current image.

        Uses a capturing shopping stub to inspect GenerateShoppingListInput.
        After 2 annotation edits + approve, the shopping activity should receive:
        - design_image_url = last revised image (from iteration 2)
        - original_room_photo_urls = room photo storage keys
        - design_brief from intake
        - revision_history with 2 entries
        - room_dimensions from scan
        """
        global _captured_shopping_input
        _captured_shopping_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_SHOPPING_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Photos (need 2 room to advance)
            room1 = PhotoData(
                photo_id="room-1",
                storage_key="photos/room_0.jpg",
                photo_type="room",
            )
            room2 = PhotoData(
                photo_id="room-2",
                storage_key="photos/room_1.jpg",
                photo_type="room",
            )
            inspo = PhotoData(
                photo_id="inspo-1",
                storage_key="photos/inspo_0.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room1)
            await handle.signal(DesignProjectWorkflow.add_photo, room2)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo)
            await asyncio.sleep(0.5)

            # Scan with dimensions
            scan = ScanData(
                storage_key="projects/test/lidar/scan.json",
                room_dimensions=RoomDimensions(width_m=3.5, length_m=5.0, height_m=2.4),
            )
            await handle.signal(DesignProjectWorkflow.complete_scan, scan)
            await asyncio.sleep(0.3)

            # Intake with brief
            brief = DesignBrief(room_type="living room", pain_points=["cluttered"])
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(1.0)

            # Select + 2 annotation edits
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)

            # Approve → triggers shopping
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"

            assert _captured_shopping_input is not None
            # Current image should be the last revision (not the original selection)
            assert _captured_shopping_input.design_image_url == state.current_image
            # Only room photos, not inspiration
            assert _captured_shopping_input.original_room_photo_urls == [
                "photos/room_0.jpg",
                "photos/room_1.jpg",
            ]
            # Brief forwarded
            assert _captured_shopping_input.design_brief is not None
            assert _captured_shopping_input.design_brief.room_type == "living room"
            # Revision history accumulated from iterations
            assert len(_captured_shopping_input.revision_history) == 2
            assert _captured_shopping_input.revision_history[0].type == "annotation"
            assert _captured_shopping_input.revision_history[1].type == "annotation"
            # Instructions must contain the original signal payload strings
            # (T3's shopping agent uses these to understand what was changed)
            assert _captured_shopping_input.revision_history[0].instructions == [
                "Replace the couch with a modern sectional"
            ]
            assert _captured_shopping_input.revision_history[1].instructions == [
                "Replace the couch with a modern sectional"
            ]
            # Room dimensions forwarded
            assert _captured_shopping_input.room_dimensions is not None
            assert _captured_shopping_input.room_dimensions.width_m == 3.5
            # Room context forwarded (analysis ran during scan)
            assert _captured_shopping_input.room_context is not None
            assert _captured_shopping_input.room_context.enrichment_sources == ["photos", "lidar"]

    async def test_shopping_input_with_minimal_state(self, workflow_env, tq):
        """Verifies shopping input handles skipped scan + skipped intake.

        The minimal path (skip scan, skip intake) leaves design_brief=None
        and room_dimensions=None. The shopping input builder must forward
        these as None. T3's real shopping activity must handle this path.
        Also verifies room photos still flow through and revision_history
        is empty when user approves immediately.
        """
        global _captured_shopping_input
        _captured_shopping_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_SHOPPING_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"

            assert _captured_shopping_input is not None
            assert _captured_shopping_input.design_brief is None
            assert _captured_shopping_input.room_dimensions is None
            assert _captured_shopping_input.revision_history == []
            assert _captured_shopping_input.design_image_url != ""
            assert len(_captured_shopping_input.original_room_photo_urls) == 2
            # Room context still available (analysis runs even without LiDAR)
            assert _captured_shopping_input.room_context is not None
            assert _captured_shopping_input.room_context.enrichment_sources == ["photos"]


class TestEditInput:
    """Tests verifying edit_design input builder passes correct data to T2 activity."""

    async def test_annotation_edit_input_has_base_image_and_annotations(self, workflow_env, tq):
        """Verifies annotation edit input contains the current image and annotations.

        The edit_design activity receives `base_image_url` (the selected/current design)
        and `annotations` (annotation regions with instructions). This test captures the
        input and verifies both fields are correctly populated from workflow state.
        Critical for P2: T2's real edit_design activity depends on these exact fields.
        """
        global _captured_edit_input
        _captured_edit_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            regions = [
                {
                    "region_id": 1,
                    "center_x": 0.5,
                    "center_y": 0.5,
                    "radius": 0.2,
                    "instruction": "Replace the sofa with a modern sectional",
                },
            ]
            await handle.signal(
                DesignProjectWorkflow.submit_annotation_edit,
                regions,
            )
            await asyncio.sleep(1.0)

            assert _captured_edit_input is not None
            # project_id forwarded from workflow (critical for T2 to scope edits)
            assert _captured_edit_input.project_id != ""
            # base_image_url is the selected option's image
            assert _captured_edit_input.base_image_url != ""
            # Room photos forwarded (T2 uses for context)
            assert len(_captured_edit_input.room_photo_urls) == 2
            # No inspiration photos in this test (advance helper uses room-only)
            assert _captured_edit_input.inspiration_photo_urls == []
            # Annotations forwarded as AnnotationRegion objects
            assert len(_captured_edit_input.annotations) == 1
            assert _captured_edit_input.annotations[0].region_id == 1
            assert "sectional" in _captured_edit_input.annotations[0].instruction

    async def test_feedback_edit_input_includes_brief_feedback_and_history(self, workflow_env, tq):
        """Verifies feedback edit input contains room photos, brief, feedback, and history.

        The edit_design activity needs `room_photo_urls` (original room photos only),
        `design_brief`, `base_image_url`, `feedback`, and the workflow tracks history.
        This test does an annotation edit first (building revision_history), then feedback,
        verifying the accumulated state flows correctly to the activity.
        """
        global _captured_edit_input
        _captured_edit_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            # Add room + inspiration photos (need 2 room to advance)
            room1 = PhotoData(
                photo_id="room-r1",
                storage_key="photos/room_r1.jpg",
                photo_type="room",
            )
            room2 = PhotoData(
                photo_id="room-r2",
                storage_key="photos/room_r2.jpg",
                photo_type="room",
            )
            inspo = PhotoData(
                photo_id="inspo-r1",
                storage_key="photos/inspo_r1.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room1)
            await handle.signal(DesignProjectWorkflow.add_photo, room2)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo)
            await asyncio.sleep(0.5)

            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)

            # Intake with a brief
            brief = DesignBrief(
                room_type="office",
                pain_points=["poor lighting"],
            )
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(1.0)

            # Select option
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            # Do an annotation edit first to build revision history
            await handle.signal(
                DesignProjectWorkflow.submit_annotation_edit,
                _annotations(),
            )
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 1

            # Now text feedback — this should capture the input
            await handle.signal(
                DesignProjectWorkflow.submit_text_feedback,
                "Make it brighter and more modern",
            )
            await asyncio.sleep(1.0)

            assert _captured_edit_input is not None
            # project_id forwarded from workflow
            assert _captured_edit_input.project_id != ""
            # Room photos forwarded (not inspiration)
            assert _captured_edit_input.room_photo_urls == [
                "photos/room_r1.jpg",
                "photos/room_r2.jpg",
            ]
            # Inspiration photos forwarded separately (T2 uses for style reference)
            assert _captured_edit_input.inspiration_photo_urls == ["photos/inspo_r1.jpg"]
            # Brief forwarded
            assert _captured_edit_input.design_brief is not None
            assert _captured_edit_input.design_brief.room_type == "office"
            # Base image is the current image (last revision's output)
            assert _captured_edit_input.base_image_url != ""
            # Feedback forwarded
            assert _captured_edit_input.feedback == "Make it brighter and more modern"
            # Chat history key forwarded from prior edit
            assert _captured_edit_input.chat_history_key is not None

    async def test_feedback_edit_input_with_skipped_intake(self, workflow_env, tq):
        """Verifies feedback edit input has design_brief=None when intake was skipped.

        When the user skips intake, no DesignBrief is stored. The edit input
        builder must forward None (not crash or default). T2's real edit_design
        activity must handle both DesignBrief and None — this test proves
        the builder doesn't assume a brief exists.
        """
        global _captured_edit_input
        _captured_edit_input = None

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_CAPTURING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            await handle.signal(
                DesignProjectWorkflow.submit_text_feedback,
                "Make it more modern",
            )
            await asyncio.sleep(1.0)

            assert _captured_edit_input is not None
            assert _captured_edit_input.design_brief is None
            assert _captured_edit_input.feedback == "Make it more modern"
            assert _captured_edit_input.base_image_url != ""


class TestStartOver:
    """Tests for the start-over flow."""

    async def test_start_over_resets_to_intake(self, workflow_env, tq):
        """Verifies start_over signal resets state and returns to intake."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            # Advance to selection
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            # Start over
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert len(state.generated_options) == 0
            assert state.selected_option is None
            assert state.design_brief is None

    async def test_start_over_then_complete_second_cycle(self, workflow_env, tq):
        """Verifies the full second cycle after start_over completes to iteration.

        After start_over, the while-True loop re-enters intake. This test:
        1. Advances to selection (first cycle)
        2. Sends start_over → returns to intake
        3. Completes a full second cycle: intake → generation → selection → iteration
        4. Verifies the second cycle produced fresh generated_options (not stale)
        5. Approves and verifies shopping list generated

        Catches bugs where the loop fails to fully reset state between cycles.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            # First cycle: advance to selection
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            # Start over
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

            # Second cycle: provide a new brief this time (not skip)
            brief = DesignBrief(room_type="bedroom", pain_points=["too dark"])
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(1.0)

            # Second generation + selection
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.generated_options) == 2
            assert state.design_brief is not None
            assert state.design_brief.room_type == "bedroom"

            # Select and approve
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"

            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.shopping_list is not None

    async def test_start_over_preserves_photos_and_scan(self, workflow_env, tq):
        """Verifies start_over preserves photos, scan_data, and scan_skipped.

        The while-True loop resets design-related state (generated_options,
        selected_option, design_brief, intake_skipped) but must NOT touch
        photos or scan data — users should never need to re-upload or rescan.
        A refactor accidentally clearing these fields would break the UX.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Upload photos (2 room + 1 inspiration)
            room_photo1 = PhotoData(
                photo_id="room-001",
                storage_key="projects/test/room.jpg",
                photo_type="room",
            )
            room_photo2 = PhotoData(
                photo_id="room-002",
                storage_key="projects/test/room2.jpg",
                photo_type="room",
            )
            inspo_photo = PhotoData(
                photo_id="inspo-001",
                storage_key="projects/test/inspo.jpg",
                photo_type="inspiration",
            )
            await handle.signal(DesignProjectWorkflow.add_photo, room_photo1)
            await handle.signal(DesignProjectWorkflow.add_photo, room_photo2)
            await handle.signal(DesignProjectWorkflow.add_photo, inspo_photo)
            await asyncio.sleep(0.5)

            # Complete scan with dimensions
            scan = ScanData(
                storage_key="scans/test.json",
                room_dimensions=RoomDimensions(
                    width_m=5.0,
                    length_m=7.0,
                    height_m=2.8,
                ),
            )
            await handle.signal(DesignProjectWorkflow.complete_scan, scan)
            await asyncio.sleep(0.3)

            # Skip intake → generation → selection
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.photos) == 3
            assert state.scan_data is not None

            # Start over
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

            # Photos preserved
            assert len(state.photos) == 3
            assert state.photos[0].photo_id == "room-001"
            assert state.photos[1].photo_id == "room-002"
            assert state.photos[2].photo_id == "inspo-001"
            assert state.photos[0].photo_type == "room"
            assert state.photos[1].photo_type == "room"
            assert state.photos[2].photo_type == "inspiration"

            # Scan data preserved
            assert state.scan_data is not None
            assert state.scan_data.room_dimensions is not None
            assert state.scan_data.room_dimensions.width_m == 5.0

            # Design-related fields reset
            assert len(state.generated_options) == 0
            assert state.selected_option is None
            assert state.design_brief is None

    async def test_start_over_clears_stale_error(self, workflow_env, tq):
        """Verifies start_over clears any pre-existing error from the previous cycle.

        If a user gets a generation error or invalid selection error and chooses
        to start over instead of retrying, the stale error must be cleared.
        Otherwise T1 iOS would show a confusing error message in the intake step.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            # Trigger an error via invalid selection
            await handle.signal(DesignProjectWorkflow.select_option, 99)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is not None
            assert "Invalid selection" in state.error.message

            # Start over instead of retrying
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.error is None  # Error cleared by start_over

    async def test_start_over_from_generation_error(self, workflow_env, tq):
        """Verifies start_over unblocks the generation error wait.

        When generation fails and the user is stuck with a retryable error,
        start_over should unblock the error wait and restart the cycle.
        Previously the generation error wait only checked `self.error is None`,
        so start_over had no effect — the user had to retry first.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_GENERATION_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(2.0)

            # Generation failed — error is set
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"
            assert state.error is not None
            assert "Design generation failed" in state.error.message

            # Start over instead of retrying
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.error is None

    async def test_start_over_from_generation_error_completes_second_cycle(self, workflow_env, tq):
        """Verifies the full second cycle after start_over from a generation error.

        Uses the flaky generate stub: first cycle generation fails (calls 1-2
        exhausting retry policy), start_over sends user back to intake, second
        cycle generation succeeds (call 3+). Proves the D48 start_over-from-error
        fix works end-to-end through selection, iteration, and approval.
        """
        global _flaky_generate_calls
        _flaky_generate_calls = 0

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FLAKY_GENERATION_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(2.0)

            # First cycle: generation failed
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"
            assert state.error is not None

            # Start over → back to intake
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

            # Second cycle: provide brief, generation succeeds
            brief = DesignBrief(room_type="den", pain_points=["too cramped"])
            await handle.signal(DesignProjectWorkflow.complete_intake, brief)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.generated_options) == 2
            assert state.generated_options[0].caption == "Retry A"
            assert state.design_brief is not None
            assert state.design_brief.room_type == "den"

            # Select → iterate → approve → completed
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.shopping_list is not None

    async def test_start_over_from_iteration_restarts_at_intake(self, workflow_env, tq):
        """Verifies start_over during iteration phase returns workflow to intake.

        This tests the fix where _restart_requested is now checked in the
        iteration wait condition, allowing the workflow to break out of
        iteration and loop back to intake.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.current_image is not None

            # Start over from iteration
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.current_image is None
            assert state.iteration_count == 0
            assert state.revision_history == []
            assert state.generated_options == []
            assert state.selected_option is None

    async def test_start_over_ignored_after_approval(self, workflow_env, tq):
        """Verifies start_over is a no-op once the design has been approved.

        After approval the workflow proceeds to shopping. Allowing start_over
        at this point would corrupt state (restart loop with approved=True).
        The signal handler should silently ignore the request.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Approve the design
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.approved is True
            # Should be at shopping or completed (shopping mock is fast)
            assert state.step in ("shopping", "completed")

            # Attempt start_over — should be ignored
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            # Still approved, did not restart
            assert state.approved is True
            assert state.step in ("shopping", "completed")

    async def test_start_over_from_approval_restarts_at_intake(self, workflow_env, tq):
        """Verifies start_over during approval phase returns workflow to intake.

        When the user hits the 5-iteration cap and lands on the approval step,
        they should be able to start over instead of being forced to approve.
        The approval wait condition must observe _restart_requested.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Exhaust all 5 iteration rounds to reach approval
            for i in range(5):
                await handle.signal(DesignProjectWorkflow.submit_text_feedback, f"change {i}")
                await asyncio.sleep(0.5)

            await asyncio.sleep(0.5)
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "approval"
            assert state.approved is False

            # Start over from approval
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.approved is False
            assert state.iteration_count == 0

    async def test_start_over_during_inflight_activity_discards_result(self, workflow_env, tq):
        """Verifies stale activity results are discarded after start_over.

        When start_over fires while an iteration activity is in-flight, the
        signal clears cycle state. When the activity returns, its result must
        NOT be applied (revision_history, current_image, iteration_count should
        stay clean for the next cycle).
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_SLOW_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"

            # Submit an annotation edit — the slow stub takes 2s
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            # Give the activity time to start but not finish
            await asyncio.sleep(0.3)

            # Fire start_over while activity is in-flight
            await handle.signal(DesignProjectWorkflow.start_over)

            # Wait for activity to complete and restart to process
            await asyncio.sleep(3.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            # Stale result must NOT have been applied
            assert state.iteration_count == 0
            assert state.revision_history == []
            assert state.current_image is None

    async def test_start_over_during_inflight_generation_discards_result(self, workflow_env, tq):
        """Verifies stale generation results are discarded after start_over.

        Most likely real user scenario: Gemini generation takes 30-60s in production.
        If the user signals start_over mid-generation, the workflow must discard the
        stale result and restart the cycle cleanly. Tests the `if self._restart_requested:
        continue` check after `execute_activity(generate_designs, ...)`.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_SLOW_GENERATE_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Skip to intake, then skip intake to trigger generation
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            # slow_generate takes 2s — give it time to start but not finish
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"

            # Fire start_over while generation is in-flight
            await handle.signal(DesignProjectWorkflow.start_over)

            # Wait for activity to complete and restart cycle to process
            await asyncio.sleep(3.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            # Stale generation result must NOT have been applied
            assert state.generated_options == []
            assert state.selected_option is None
            assert state.design_brief is None
            assert state.error is None


class TestSelectionValidation:
    """Tests for selection signal validation."""

    async def test_invalid_selection_surfaces_error(self, workflow_env, tq):
        """Verifies invalid select_option index surfaces a WorkflowError.

        iOS polls for errors and can show retry UI. Without error surfacing,
        an invalid selection would leave the user stuck in selection step.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            # Send invalid index
            await handle.signal(DesignProjectWorkflow.select_option, 99)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"  # Still waiting
            assert state.selected_option is None
            assert state.error is not None
            assert "Invalid selection" in state.error.message

            # Clear error and send valid selection — workflow recovers
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(0.2)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.error is None

    async def test_select_option_ignored_during_iteration(self, workflow_env, tq):
        """IMP-30: select_option signal during iteration is silently ignored.

        The mock API guards select_option with _check_step(state, "selection").
        The workflow must mirror this — a late signal should not corrupt
        selected_option or set an error.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.selected_option == 0
            original_image = state.current_image

            # Send a late select_option signal with a different index
            await handle.signal(DesignProjectWorkflow.select_option, 1)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            # selected_option and current_image must not change
            assert state.selected_option == 0
            assert state.current_image == original_image
            # No error should be set — the signal is simply ignored
            assert state.error is None


class TestIterationPhase:
    """Tests for the iteration (annotation/feedback) phase."""

    async def test_annotation_edit_creates_revision(self, workflow_env, tq):
        """Verifies annotation edit signal creates a revision record."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"

            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 1
            assert len(state.revision_history) == 1
            rev = state.revision_history[0]
            assert rev.type == "annotation"
            assert rev.revision_number == 1
            assert rev.base_image_url != ""
            assert rev.revised_image_url != rev.base_image_url
            assert state.current_image == rev.revised_image_url

    async def test_text_feedback_creates_revision(self, workflow_env, tq):
        """Verifies text feedback signal creates a revision record."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            await handle.signal(DesignProjectWorkflow.submit_text_feedback, "Make it warmer")
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 1
            assert len(state.revision_history) == 1
            rev = state.revision_history[0]
            assert rev.type == "feedback"
            assert rev.revision_number == 1
            assert rev.base_image_url != ""
            assert rev.revised_image_url != rev.base_image_url
            assert state.current_image == rev.revised_image_url

    async def test_five_iterations_moves_to_approval(self, workflow_env, tq):
        """Verifies workflow transitions to approval after 5 iteration rounds."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            # Do 5 iterations
            for i in range(5):
                await handle.signal(DesignProjectWorkflow.submit_text_feedback, f"Feedback {i}")
                await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 5
            assert state.step == "approval"

    async def test_approve_after_five_iterations_completes(self, workflow_env, tq):
        """Verifies the max-iterations path completes through shopping to done.

        After 5 iterations the workflow auto-transitions to the 'approval'
        step and waits for an explicit approve_design signal. This test
        proves the full path: 5 iterations → approval wait → approve →
        shopping → completed. Previously only tested the step transition
        (test_five_iterations_moves_to_approval) but not completion.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Do 5 iterations (mixed types to be realistic)
            for _ in range(3):
                await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
                await asyncio.sleep(1.0)
            for j in range(2):
                await handle.signal(DesignProjectWorkflow.submit_text_feedback, f"Feedback {j}")
                await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "approval"
            assert state.iteration_count == 5

            # Approve from the explicit approval step
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.shopping_list is not None
            assert len(state.shopping_list.items) > 0
            assert state.iteration_count == 5
            assert len(state.revision_history) == 5

    async def test_mixed_annotation_and_feedback_iterations(self, workflow_env, tq):
        """Verifies mixed annotation + feedback iterations are all tracked correctly."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Annotation, Annotation, Feedback, Annotation
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.submit_text_feedback, "Make it warmer")
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 4
            assert len(state.revision_history) == 4
            types = [r.type for r in state.revision_history]
            assert types == ["annotation", "annotation", "feedback", "annotation"]
            # Each revision number is sequential
            nums = [r.revision_number for r in state.revision_history]
            assert nums == [1, 2, 3, 4]
            # Instructions extracted correctly for both action types
            # (T3's shopping agent uses these to understand changes)
            assert state.revision_history[0].instructions == [
                "Replace the couch with a modern sectional"
            ]
            assert state.revision_history[2].instructions == ["Make it warmer"]

    async def test_revision_chain_integrity(self, workflow_env, tq):
        """Verifies revision_history forms a proper image chain.

        Each revision's base_image_url must equal the previous revision's
        revised_image_url (or the selected option's image for the first).
        This chain lets T1 iOS display a revision timeline and T2's
        edit activity receives the correct base image.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # 3 iterations: annotation, feedback, annotation
            await handle.signal(
                DesignProjectWorkflow.submit_annotation_edit,
                _annotations(),
            )
            await asyncio.sleep(1.0)
            await handle.signal(
                DesignProjectWorkflow.submit_text_feedback,
                "Make it brighter",
            )
            await asyncio.sleep(1.0)
            await handle.signal(
                DesignProjectWorkflow.submit_annotation_edit,
                _annotations(),
            )
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert len(state.revision_history) == 3

            # First revision's base is the selected option's image
            r0 = state.revision_history[0]
            assert r0.base_image_url != ""

            # Each subsequent revision's base = previous revision's output
            for i in range(1, len(state.revision_history)):
                prev = state.revision_history[i - 1]
                curr = state.revision_history[i]
                assert curr.base_image_url == prev.revised_image_url, (
                    f"Revision {curr.revision_number} base "
                    f"({curr.base_image_url}) doesn't chain from "
                    f"revision {prev.revision_number} output "
                    f"({prev.revised_image_url})"
                )

            # current_image is the last revision's output
            assert state.current_image == state.revision_history[-1].revised_image_url


class TestApproval:
    """Tests for the approval phase."""

    async def test_approve_immediately_after_selection(self, workflow_env, tq):
        """Verifies approve works with zero iterations — shopping list still generated."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Approve immediately without any edits
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.iteration_count == 0
            assert state.revision_history == []
            assert state.shopping_list is not None
            assert len(state.shopping_list.items) > 0

    async def test_approve_from_iteration(self, workflow_env, tq):
        """Verifies approve signal from iteration step leads to shopping then completed."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True
            assert state.shopping_list is not None

    async def test_approve_ignored_during_active_error(self, workflow_env, tq):
        """Verifies approve_design is rejected when an error is active.

        If an iteration activity fails, the user sees an error state. They
        must clear it (retry or start_over) before approving. Allowing
        approval during an error would skip the retry wait and proceed
        to shopping with potentially inconsistent state.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Submit an annotation edit that will fail (2 retry attempts)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            # Wait for both retry attempts to exhaust + error to surface
            await asyncio.sleep(3.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is not None, f"Expected error, got step={state.step}"
            assert state.step == "iteration"

            # Try to approve — should be ignored because error is active
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.approved is False
            assert state.error is not None  # still has error
            assert state.step == "iteration"  # still stuck

    async def test_approve_ignored_during_generation(self, workflow_env, tq):
        """IMP-31: approve_design during generation step is silently ignored.

        Without the step guard, a premature approve would set self.approved=True,
        and the iteration loop (`while count < 5 and not self.approved`) would
        exit immediately — skipping iteration entirely and going straight to
        shopping. The step guard ensures approve only takes effect during
        iteration or approval steps.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)

            # Generation is now in progress — send premature approve
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            # Should be in selection (generation completed, approve was ignored)
            assert state.step == "selection"
            assert state.approved is False
            assert len(state.generated_options) == 2

    async def test_approve_ignored_during_selection(self, workflow_env, tq):
        """IMP-31: approve_design during selection step is silently ignored.

        User must select a design option before approving. A premature approve
        during selection would skip the iteration phase.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

            # Premature approve during selection
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(0.3)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"  # Still waiting for option selection
            assert state.approved is False

            # Normal flow still works — select then approve from iteration
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.approved is False  # Premature approve was truly ignored


class TestCancellation:
    """Tests for project cancellation."""

    async def test_cancel_from_iteration_preserves_state(self, workflow_env, tq):
        """Verifies cancel from iteration preserves revision history and photos.

        When a user cancels mid-iteration, the workflow should still retain
        their photos and revision history for potential recovery or analytics.
        The purge timer handles cleanup separately.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Do one iteration before cancelling
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.iteration_count == 1

            # Cancel mid-iteration
            await handle.signal(DesignProjectWorkflow.cancel_project)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"
            assert len(state.photos) == 2
            assert state.iteration_count == 1
            assert len(state.revision_history) == 1

    async def test_cancel_from_selection_preserves_generated_options(self, workflow_env, tq):
        """Verifies cancel from selection step preserves generated options.

        The selection wait has a compound condition (selected_option or
        _restart_requested). Cancel must take precedence via the _cancelled
        check in _wait. Generated options should be preserved in the final
        state for analytics/debugging.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"
            assert len(state.generated_options) == 2

            # Cancel from selection
            await handle.signal(DesignProjectWorkflow.cancel_project)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"
            assert len(state.generated_options) == 2
            assert state.selected_option is None

    async def test_cancel_terminates_workflow(self, workflow_env, tq):
        """Verifies cancel_project signal sets step to 'abandoned' and completes."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.cancel_project)
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

            result = await handle.result()
            assert result is None

    async def test_temporal_cancel_sets_cancelled_step(self, workflow_env, tq):
        """Verifies Temporal-level cancellation (handle.cancel()) sets step='cancelled'.

        Distinct from cancel_project signal which sets step='abandoned'.
        Temporal cancellation raises asyncio.CancelledError from the current
        await point.  The workflow catches it, sets step='cancelled', attempts
        purge (best-effort), and re-raises so Temporal marks the execution as
        cancelled.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Temporal-level cancellation (e.g. operator cancels via Temporal UI)
            await handle.cancel()

            # result() raises WorkflowFailureError for cancelled workflows
            with pytest.raises(WorkflowFailureError):
                await handle.result()

            # Query still works after cancellation — shows final state
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "cancelled"

    async def test_cancel_during_generation_error_abandons(self, workflow_env, tq):
        """Verifies cancel_project escapes the generation error wait.

        When generation fails and the user is stuck with a retryable error,
        cancel_project should immediately abandon the workflow. The _wait
        helper checks _cancelled alongside the error condition, so
        cancel_project takes effect even from the error wait state.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_GENERATION_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"
            assert state.error is not None

            # Cancel instead of retrying
            await handle.signal(DesignProjectWorkflow.cancel_project)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

    async def test_cancel_during_iteration_error_abandons(self, workflow_env, tq):
        """Verifies cancel_project escapes the iteration error wait.

        When an edit_design activity fails and the user is stuck with a
        retryable error, cancel_project should abandon instead of waiting
        for retry. Tests the same _cancelled check in _wait but from the
        iteration error wait context.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.error is not None

            # Cancel instead of retrying
            await handle.signal(DesignProjectWorkflow.cancel_project)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

    async def test_cancel_during_shopping_error_abandons(self, workflow_env, tq):
        """Verifies cancel_project escapes the shopping error wait.

        When shopping list generation fails and the user is stuck with a
        retryable error, cancel_project should abandon the workflow.
        Completes the cancellation symmetry: generation error, iteration
        error, and now shopping error all tested.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_SHOPPING_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "shopping"
            assert state.error is not None

            # Cancel instead of retrying
            await handle.signal(DesignProjectWorkflow.cancel_project)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

    async def test_cancel_completes_when_purge_fails(self, workflow_env, tq):
        """Verifies cancel_project still abandons when purge_project_data fails.

        The _wait helper calls _try_purge before raising _AbandonedError.
        If R2 is down, the BaseException catch in _try_purge must swallow
        the purge error so the workflow can still reach step='abandoned'.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_PURGE_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.cancel_project)

            # Wait for workflow to complete — purge retries then fails,
            # but _try_purge swallows the error and abandonment proceeds
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

    async def test_cancel_from_completed_triggers_prompt_purge(self, workflow_env, tq):
        """Verifies cancel_project during completed phase wakes the retention timer.

        After completion, the workflow sleeps 24h before purging. If cancel is
        signaled during this window, it should purge and finish promptly rather
        than waiting out the full retention period.
        """
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Approve → shopping → completed
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"

            # Cancel during the 24h retention window
            await handle.signal(DesignProjectWorkflow.cancel_project)

            # Workflow should finish promptly (not wait 24h)
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            # Completed-phase cancel triggers purge and finishes normally
            assert state.step == "completed"


class TestAbandonmentTimeout:
    """Tests for the 48-hour abandonment timeout."""

    async def test_workflow_abandons_after_48h_inactivity(self, workflow_env, tq):
        """Verifies the workflow auto-abandons after 48h with no signals.

        The _wait helper uses workflow.wait_condition with a 48h timeout.
        In the time-skipping environment, time advances automatically.
        When the timeout fires, _AbandonedError is raised, which sets
        step='abandoned' and completes the workflow normally (returns None).
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Don't send any signals — let the 48h timeout fire
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"

    async def test_mid_flow_abandonment_at_scan_step(self, workflow_env, tq):
        """Verifies abandonment works at the scan step (not just photos).

        Advances past photos, then goes idle at scan. After 48h timeout,
        the workflow should abandon with step='abandoned' and the scan_data
        should still be None.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "scan"

            # Go idle at scan step — let 48h timeout fire
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"
            assert state.scan_data is None

    async def test_abandonment_completes_when_purge_fails(self, workflow_env, tq):
        """Verifies abandonment still completes when purge_project_data fails.

        During the 48h timeout abandonment path, _try_purge is called before
        raising _AbandonedError. If R2 is down, the purge failure must not
        prevent the workflow from reaching step='abandoned'.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_PURGE_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)

            # Let 48h timeout fire — purge will fail, but abandonment should succeed
            result = await handle.result()
            assert result is None

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "abandoned"


class TestCompletionPurge:
    """Tests for the 24-hour purge timer after completion."""

    async def test_workflow_completes_after_24h_purge_timer(self, workflow_env, tq):
        """Verifies workflow runs 24h purge timer after approval then terminates.

        After approval + shopping list, the workflow sets step='completed',
        sleeps 24h, calls purge, then exits. In the time-skipping environment,
        the 24h sleep advances automatically. The workflow completing (result()
        returns None) proves the entire post-approval flow ran.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            # Verify completed state before 24h timer fires
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.approved is True

            # Wait for workflow to fully complete (24h sleep + purge in time-skipping mode)
            result = await handle.result()
            assert result is None

    async def test_purge_failure_does_not_block_completion(self, workflow_env, tq):
        """Verifies workflow completes normally even when purge_project_data fails.

        In production, R2 might be down when the 24h timer fires. The _try_purge
        handler catches all exceptions (including asyncio.CancelledError via
        BaseException) and logs the error. The workflow must still exit normally.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_PURGE_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"

            # Workflow should complete even though purge fails
            result = await handle.result()
            assert result is None


class TestQueryState:
    """Tests for the get_state query."""

    async def test_initial_state(self, workflow_env, tq):
        """Verifies initial workflow state is correct."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            state = await handle.query(DesignProjectWorkflow.get_state)

            assert state.step == "photos"
            assert state.photos == []
            assert state.scan_data is None
            assert state.design_brief is None
            assert state.generated_options == []
            assert state.selected_option is None
            assert state.current_image is None
            assert state.revision_history == []
            assert state.iteration_count == 0
            assert state.shopping_list is None
            assert state.approved is False
            assert state.error is None

    def test_get_state_maps_all_workflow_state_fields(self):
        """Verifies get_state() explicitly sets every WorkflowState field.

        If a field is added to WorkflowState but omitted from get_state(),
        it silently uses the Pydantic default. This structural test catches
        that drift by checking the source code of get_state().
        """
        import inspect

        from app.models.contracts import WorkflowState

        source = inspect.getsource(DesignProjectWorkflow.get_state)
        for field_name in WorkflowState.model_fields:
            assert f"{field_name}=" in source, (
                f"WorkflowState field '{field_name}' is not explicitly set in get_state(). "
                "Add it to the WorkflowState() constructor in get_state()."
            )


class TestErrorRecovery:
    """Tests for workflow error handling and recovery."""

    async def test_generation_error_is_retryable(self, workflow_env, tq):
        """Verifies generation failure sets a retryable error with user-facing message."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_GENERATION_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"
            assert state.error is not None
            assert "Design generation failed" in state.error.message
            assert state.error.retryable is True

    async def test_generation_retry_succeeds_after_transient_failure(self, workflow_env, tq):
        """Verifies the full retry flow: fail → error → retry_failed_step → succeed.

        Uses a flaky generate stub that fails the first workflow-level attempt
        (exhausting Temporal's 2-attempt retry policy) but succeeds on the second
        attempt after the user sends retry_failed_step.  Confirms the workflow
        advances to selection with generated options.
        """
        global _flaky_generate_calls
        _flaky_generate_calls = 0

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FLAKY_GENERATION_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(0.3)
            await handle.signal(DesignProjectWorkflow.skip_intake)
            await asyncio.sleep(2.0)

            # First attempt failed — error surfaced
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "generation"
            assert state.error is not None
            assert state.error.retryable is True

            # User retries — second attempt succeeds
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is None
            assert state.step == "selection"
            assert len(state.generated_options) == 2
            assert state.generated_options[0].caption == "Retry A"

    async def test_iteration_error_blocks_until_retry(self, workflow_env, tq):
        """Verifies iteration failure sets error and waits (doesn't skip ahead)."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"

            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is not None
            assert "Revision failed" in state.error.message
            assert state.iteration_count == 0
            assert state.step == "iteration"

    async def test_iteration_retry_clears_error_and_accepts_approval(self, workflow_env, tq):
        """Verifies retry → approve escapes an iteration error loop.

        Uses a flaky edit stub that fails first (surfacing an error),
        then succeeds on retry.  After retry succeeds the error is cleared,
        so approve_design is accepted and the workflow proceeds to shopping.
        """
        global _flaky_edit_calls
        _flaky_edit_calls = 0

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FLAKY_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is not None
            assert state.step == "iteration"

            # Retry clears the error; flaky stub succeeds on the next
            # workflow-level attempt.  Once the activity succeeds, the
            # iteration loop waits for the next action/approve/restart.
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is None
            assert state.iteration_count == 1

            # No active error → approve is accepted.
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.approved is True
            assert state.step == "completed"

    async def test_queued_action_during_error_runs_after_retry(self, workflow_env, tq):
        """Verifies action ordering when user submits edits during an active error.

        Real user pattern: submit annotation edit → it fails → while error is
        showing, user submits text feedback → retries → the original annotation
        processes first (re-queued at index 0), then the queued feedback processes.
        This validates the _action_queue.insert(0, ...) re-queuing semantics.
        """
        global _flaky_edit_calls
        _flaky_edit_calls = 0

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FLAKY_EDIT_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Submit annotation edit — flaky stub fails (calls 1-2 exhausted by Temporal)
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is not None
            assert state.iteration_count == 0

            # While error is active, user submits feedback (queued at index 1)
            await handle.signal(
                DesignProjectWorkflow.submit_text_feedback,
                "Make it warmer and more inviting",
            )

            # Retry — clears error, workflow processes re-queued annotation first
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(2.0)

            # Both actions should have processed: annotation (call 3) + feedback (call 4)
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is None
            assert state.iteration_count == 2
            assert len(state.revision_history) == 2
            # Original failed annotation was re-queued first, so it processes first
            assert state.revision_history[0].type == "annotation"
            assert state.revision_history[1].type == "feedback"

    async def test_shopping_error_is_retryable(self, workflow_env, tq):
        """Verifies shopping failure sets a retryable error with user-facing message."""

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FAILING_SHOPPING_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "shopping"
            assert state.error is not None
            assert "Shopping list failed" in state.error.message
            assert state.error.retryable is True

    async def test_malformed_annotations_surfaces_error(self, workflow_env, tq):
        """Verifies malformed annotations surface an error instead of crashing.

        When a signal delivers invalid annotation data (e.g. missing required
        fields), the Pydantic ValidationError during input construction must be
        caught and surfaced as a WorkflowError, not left to crash the workflow
        task into infinite retry.
        """

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)

            # Send malformed annotations: missing required fields, instruction too short
            bad_regions = [{"region_id": 99, "instruction": "short"}]
            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, bad_regions)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "iteration"
            assert state.error is not None
            assert "Invalid edit request" in state.error.message
            assert state.iteration_count == 0  # No iteration consumed

            # Verify workflow recovers: clear error, submit valid action
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(0.3)

            await handle.signal(DesignProjectWorkflow.submit_annotation_edit, _annotations())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.error is None
            assert state.iteration_count == 1  # Valid action succeeded

    async def test_shopping_retry_succeeds_after_transient_failure(self, workflow_env, tq):
        """Verifies shopping list retry succeeds after initial failure.

        Uses _flaky_shopping stub: fails first 2 calls (exhausting Temporal
        retry policy), then succeeds. Flow: approve → shopping fails (error
        surfaced) → retry_failed_step → shopping succeeds → step=completed.
        """
        global _flaky_shopping_calls
        _flaky_shopping_calls = 0

        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=_FLAKY_SHOPPING_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await _advance_to_iteration(handle)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(2.0)

            # Should be in shopping with error
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "shopping"
            assert state.error is not None

            # Retry — flaky stub succeeds on 3rd+ call
            await handle.signal(DesignProjectWorkflow.retry_failed_step)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            assert state.error is None
            assert state.shopping_list is not None
            assert len(state.shopping_list.items) > 0


# ---------------------------------------------------------------------------
# Eager analysis (Designer Brain) tests
# ---------------------------------------------------------------------------


def _scan_with_dims() -> ScanData:
    """Helper to create a scan with LiDAR dimensions."""
    return ScanData(
        storage_key="projects/test/lidar/dimensions.json",
        room_dimensions=RoomDimensions(width_m=4.0, length_m=5.0, height_m=2.7),
    )


class TestEagerAnalysis:
    """Verify the eager photo analysis fires during scan and enriches intake."""

    async def test_analysis_populates_after_scan(self, workflow_env, tq):
        """Analysis should complete by the time intake starts (ran during scan)."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            # Analysis fires after 2+ photos; scan proceeds in parallel
            await handle.signal(DesignProjectWorkflow.complete_scan, _scan_with_dims())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.room_analysis is not None
            assert state.room_analysis.room_type == "living room"
            assert state.room_analysis.photo_count == 2

    async def test_analysis_enriched_with_lidar(self, workflow_env, tq):
        """LiDAR dimensions should override photo-estimated dimensions."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.complete_scan, _scan_with_dims())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.room_context is not None
            assert state.room_context.enrichment_sources == ["photos", "lidar"]
            assert state.room_context.room_dimensions is not None
            assert state.room_context.room_dimensions.width_m == 4.0
            # Dimensions should be LiDAR-precise, not photo-estimated
            assert "4.0m x 5.0m" in state.room_analysis.estimated_dimensions

    async def test_analysis_without_lidar(self, workflow_env, tq):
        """Skip-scan path: analysis still available, but photo-only context."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.room_analysis is not None
            # Without LiDAR, context has only photo source
            assert state.room_context is not None
            assert state.room_context.enrichment_sources == ["photos"]
            assert state.room_context.room_dimensions is None

    async def test_start_over_re_fires_analysis(self, workflow_env, tq):
        """start_over clears analysis then re-fires for the next intake round."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(1.0)

            # Verify analysis populated
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.room_analysis is not None

            # Start over: clears analysis, but while-loop re-fires it
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(1.0)

            # After settling, analysis should be re-populated (same photos)
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.room_analysis is not None
            assert state.room_analysis.room_type == "living room"

    async def test_start_over_during_intake_restarts_cleanly(self, workflow_env, tq):
        """start_over during intake wait should restart without wasting generation."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"

            # Start over while waiting for intake (no brief submitted yet)
            await handle.signal(DesignProjectWorkflow.start_over)
            await asyncio.sleep(1.0)

            # Should be back at intake, not stuck or at generation
            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            assert state.design_brief is None

            # Now complete the second intake round normally
            await handle.signal(DesignProjectWorkflow.complete_intake, _brief())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

    async def test_analysis_does_not_block_intake(self, workflow_env, tq):
        """Even with analysis, workflow should reach intake step."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.skip_scan)
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "intake"
            # Can still complete intake normally
            await handle.signal(DesignProjectWorkflow.complete_intake, _brief())
            await asyncio.sleep(1.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "selection"

    async def test_full_flow_with_analysis(self, workflow_env, tq):
        """Full happy path with analysis — photos → scan → intake → generation → completed."""
        async with Worker(
            workflow_env.client,
            task_queue=tq,
            workflows=[DesignProjectWorkflow],
            activities=ALL_ACTIVITIES,
        ):
            handle = await _start_workflow(workflow_env, tq)
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(0))
            await handle.signal(DesignProjectWorkflow.add_photo, _photo(1))
            await handle.signal(DesignProjectWorkflow.complete_scan, _scan_with_dims())
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.complete_intake, _brief())
            await asyncio.sleep(1.0)
            await handle.signal(DesignProjectWorkflow.select_option, 0)
            await asyncio.sleep(0.5)
            await handle.signal(DesignProjectWorkflow.approve_design)
            await asyncio.sleep(2.0)

            state = await handle.query(DesignProjectWorkflow.get_state)
            assert state.step == "completed"
            # Analysis should persist through the full flow
            assert state.room_analysis is not None
            assert state.room_context is not None
            assert state.room_context.enrichment_sources == ["photos", "lidar"]
