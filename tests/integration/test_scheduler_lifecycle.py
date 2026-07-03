"""
Integration tests for the scheduler lifecycle: create → update → pause → resume → delete.

These tests use a real StateManager backed by temporary directories and the
real Scheduler class.  No LLM or network access is required.
"""

import uuid

import pytest

from app.progenitor.schedule import Scheduler
from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import ScheduleEntry
from app.progenitor.utils import utcnow

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
    schedule_id = f"sched_{uuid.uuid4().hex[:12]}"
    entry = ScheduleEntry(
        id=schedule_id,
        message="Lifecycle test task - Run the lifecycle test.",
        cron_expression="0 9 * * *",
        created_at=utcnow(),
    )
    await state_manager.add_schedule_entry(entry)
    return schedule_id


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
        updated = await state_manager.update_schedule_entry(
            schedule_id,
            {"message": "Updated lifecycle task", "cron_expression": "0 10 * * *"},
        )
        assert updated, "update_schedule_entry should report success"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.message == "Updated lifecycle task"
        assert entry.cron_expression == "0 10 * * *"

        # ---- 3. PAUSE --------------------------------------------------------
        paused = await state_manager.update_schedule_entry(schedule_id, {"paused": True})
        assert paused, "pause via update_schedule_entry should report success"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.paused is True, "Entry should be paused after update_schedule_entry"

        # Confirm Scheduler skips paused entries
        now = utcnow()
        entry_dict = entry.model_dump()
        due = scheduler.get_due_items([entry_dict], now)
        assert schedule_id not in [d["id"] for d in due], (
            "Paused entry must not appear in due items"
        )

        # ---- 4. RESUME -------------------------------------------------------
        resumed = await state_manager.update_schedule_entry(schedule_id, {"paused": False})
        assert resumed, "resume via update_schedule_entry should report success"

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        assert entry.paused is False, "Entry should not be paused after resume"

        # ---- 5. DELETE -------------------------------------------------------
        removed = await state_manager.remove_schedule_entry(schedule_id)
        assert removed, "remove_schedule_entry should report success"

        schedule = await state_manager.get_schedule()
        ids_after = [e.id for e in schedule]
        assert schedule_id not in ids_after, "Entry should be absent after deletion"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_failure(self, state_manager: StateManager):
        removed = await state_manager.remove_schedule_entry("sched_does_not_exist")
        assert removed is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_returns_failure(self, state_manager: StateManager):
        updated = await state_manager.update_schedule_entry(
            "sched_does_not_exist", {"message": "irrelevant"}
        )
        assert updated is False

    @pytest.mark.asyncio
    async def test_pause_nonexistent_returns_failure(self, state_manager: StateManager):
        updated = await state_manager.update_schedule_entry(
            "sched_does_not_exist", {"paused": True}
        )
        assert updated is False

    @pytest.mark.asyncio
    async def test_resume_nonexistent_returns_failure(self, state_manager: StateManager):
        updated = await state_manager.update_schedule_entry(
            "sched_does_not_exist", {"paused": False}
        )
        assert updated is False

    @pytest.mark.asyncio
    async def test_paused_entry_skipped_by_scheduler(
        self, state_manager: StateManager, scheduler: Scheduler
    ):
        """A paused entry must not appear in get_due_items even when past its run time."""
        schedule_id = await _add_and_persist(state_manager)
        await state_manager.update_schedule_entry(schedule_id, {"paused": True})

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
        await state_manager.update_schedule_entry(schedule_id, {"paused": True})
        await state_manager.update_schedule_entry(schedule_id, {"paused": False})

        schedule = await state_manager.get_schedule()
        entry = next(e for e in schedule if e.id == schedule_id)
        entry_dict = entry.model_dump()

        # Force entry to be overdue
        entry_dict["last_run"] = None
        entry_dict["created_at"] = "2000-01-01T00:00:00+00:00"

        due = scheduler.get_due_items([entry_dict], utcnow())
        assert schedule_id in [d["id"] for d in due]
