"""
Integration tests for the scheduler lifecycle: create → update → pause → resume → delete.

These tests use a real StateManager backed by temporary directories and the
real Scheduler class.  No LLM or network access is required.
"""

import pytest

from app.progenitor.runtime.scheduler import Scheduler
from app.progenitor.runtime.time_utils import utcnow
from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.tools.schedule import (
    add_scheduled_task,
    delete_scheduled_task,
    pause_scheduled_task,
    resume_scheduled_task,
    update_scheduled_task,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def state_manager(tmp_path):
    """Real StateManager backed by a temporary directory."""
    state_dir = tmp_path / "state"
    logs_dir = tmp_path / "logs"
    state_dir.mkdir()
    logs_dir.mkdir()
    manager = StateManager(state_dir, logs_dir)
    await manager.initialize()
    return manager


@pytest.fixture
def scheduler():
    """Real Scheduler instance."""
    return Scheduler()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _add_and_persist(state_manager: StateManager) -> str:
    """Create a recurring task and persist it; return the schedule_id."""
    result, entry_data = await add_scheduled_task(
        user_request="test lifecycle task",
        time_expression="every day at 9am",
        instruction="Run the lifecycle test.",
        description="Lifecycle test task",
    )
    assert result.success, f"add_scheduled_task failed: {result.message}"
    assert entry_data is not None

    # Persist via state_manager (mirrors what ScheduleTaskTool.execute does)
    entry = ScheduleEntry(**entry_data)
    await state_manager.add_schedule_entry(entry)
    return result.schedule_id


# ---------------------------------------------------------------------------
# Full lifecycle test
# ---------------------------------------------------------------------------


class TestSchedulerLifecycle:
    """Exercises the full create → update → pause → resume → delete flow."""

    @pytest.mark.asyncio
    async def test_full_lifecycle(self, state_manager: StateManager, scheduler: Scheduler):
        # ---- 1. CREATE -------------------------------------------------------
        schedule_id = await _add_and_persist(state_manager)

        schedule = await state_manager.get_schedule()
        ids = [e.id for e in schedule]
        assert schedule_id in ids, "Entry should exist after creation"

        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.paused is False
        assert entry.cron_expression == "0 9 * * *"

        # ---- 2. UPDATE -------------------------------------------------------
        result = await update_scheduled_task(
            schedule_id,
            {"message": "Updated lifecycle task", "cron_expression": "0 10 * * *"},
            state_manager=state_manager,
        )
        assert result.success, f"update failed: {result.message}"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.message == "Updated lifecycle task"
        assert entry.cron_expression == "0 10 * * *"

        # ---- 3. PAUSE --------------------------------------------------------
        result = await pause_scheduled_task(schedule_id, state_manager=state_manager)
        assert result.success, f"pause failed: {result.message}"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.paused is True, "Entry should be paused after pause_scheduled_task"

        # Confirm Scheduler skips paused entries
        now = utcnow()
        entry_dict = entry.model_dump()
        due = scheduler.get_due_items([entry_dict], now)
        assert schedule_id not in [d["id"] for d in due], (
            "Paused entry must not appear in due items"
        )

        # ---- 4. RESUME -------------------------------------------------------
        result = await resume_scheduled_task(schedule_id, state_manager=state_manager)
        assert result.success, f"resume failed: {result.message}"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.paused is False, "Entry should not be paused after resume"

        # ---- 5. DELETE -------------------------------------------------------
        result = await delete_scheduled_task(schedule_id, state_manager=state_manager)
        assert result.success, f"delete failed: {result.message}"

        schedule = await state_manager.get_schedule()
        ids_after = [e.id for e in schedule]
        assert schedule_id not in ids_after, "Entry should be absent after deletion"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_failure(self, state_manager: StateManager):
        result = await delete_scheduled_task("sched_does_not_exist", state_manager=state_manager)
        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_failure(self, state_manager: StateManager):
        result = await update_scheduled_task(
            "sched_does_not_exist",
            {"message": "irrelevant"},
            state_manager=state_manager,
        )
        assert result.success is False
        assert "not found" in result.message.lower()

    @pytest.mark.asyncio
    async def test_pause_nonexistent_returns_failure(self, state_manager: StateManager):
        result = await pause_scheduled_task("sched_does_not_exist", state_manager=state_manager)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_resume_nonexistent_returns_failure(self, state_manager: StateManager):
        result = await resume_scheduled_task("sched_does_not_exist", state_manager=state_manager)
        assert result.success is False

    @pytest.mark.asyncio
    async def test_paused_entry_skipped_by_scheduler(
        self, state_manager: StateManager, scheduler: Scheduler
    ):
        """A paused entry must not appear in get_due_items even when past its run time."""
        schedule_id = await _add_and_persist(state_manager)
        await pause_scheduled_task(schedule_id, state_manager=state_manager)

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        entry_dict = entry.model_dump()

        # Force entry to appear as overdue
        entry_dict["last_run"] = None
        entry_dict["created_at"] = "2000-01-01T00:00:00+00:00"

        due = scheduler.get_due_items([entry_dict], utcnow())
        assert schedule_id not in [d["id"] for d in due]

    @pytest.mark.asyncio
    async def test_resumed_entry_eligible_for_scheduling(
        self, state_manager: StateManager, scheduler: Scheduler
    ):
        """A resumed entry with a past cron should appear in get_due_items."""
        schedule_id = await _add_and_persist(state_manager)
        await pause_scheduled_task(schedule_id, state_manager=state_manager)
        await resume_scheduled_task(schedule_id, state_manager=state_manager)

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        entry_dict = entry.model_dump()

        # Force entry to be overdue
        entry_dict["last_run"] = None
        entry_dict["created_at"] = "2000-01-01T00:00:00+00:00"

        due = scheduler.get_due_items([entry_dict], utcnow())
        assert schedule_id in [d["id"] for d in due]
