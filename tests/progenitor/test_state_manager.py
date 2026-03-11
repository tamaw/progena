"""Tests for state manager."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import (
    ConversationMessage,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


class TestStateManager:
    """Tests for the StateManager class."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary state and logs directories."""
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_initialize_creates_files(self, temp_dirs):
        """Test that initialization creates necessary files."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # build_state.json replaces both current_state.json and backlog.json
        assert (state_dir / "build_state.json").exists()
        assert (state_dir.parent / "config" / "agents.json").exists()
        assert (state_dir.parent / "config" / "mcp.json").exists()
        assert (state_dir / "schedule.json").exists()

    @pytest.mark.asyncio
    async def test_save_and_get_backlog(self, temp_dirs):
        """Test saving and retrieving backlog."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        work_item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            description="Test item",
        )

        await manager.save_backlog([work_item])
        backlog = await manager.get_backlog()

        assert len(backlog) == 1
        assert backlog[0].id == "test-1"

    @pytest.mark.asyncio
    async def test_add_work_item(self, temp_dirs):
        """Test adding a single work item."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        item1 = WorkItem(
            id="item-1",
            item_type=WorkItemType.A2A_CALL,
            description="First item",
        )
        item2 = WorkItem(
            id="item-2",
            item_type=WorkItemType.MCP_CALL,
            description="Second item",
        )

        await manager.add_work_item(item1)
        await manager.add_work_item(item2)

        backlog = await manager.get_backlog()
        assert len(backlog) == 2

    @pytest.mark.asyncio
    async def test_update_work_item(self, temp_dirs):
        """Test updating a work item."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        item = WorkItem(
            id="update-test",
            item_type=WorkItemType.BUILD_AGENT,
            description="Original description",
        )
        await manager.add_work_item(item)

        # Update the item
        item.status = WorkItemStatus.IN_PROGRESS
        item.description = "Updated description"
        await manager.update_work_item(item)

        backlog = await manager.get_backlog()
        assert len(backlog) == 1

        updated = backlog[0]
        # Check status - handle both enum and string
        status_val = updated.status.value if hasattr(updated.status, 'value') else updated.status
        assert status_val == "in_progress"
        assert updated.description == "Updated description"

    @pytest.mark.asyncio
    async def test_schedule_management(self, temp_dirs):
        """Test schedule save and retrieval (simplified model)."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        from app.progenitor.runtime.time_utils import utcnow

        entry = ScheduleEntry(
            id="schedule-1",
            message="Do the daily task",
            cron_expression="0 9 * * *",
            created_at=utcnow(),
        )

        await manager.save_schedule([entry])
        schedule = await manager.get_schedule()

        assert len(schedule) == 1
        assert schedule[0].id == "schedule-1"

    @pytest.mark.asyncio
    async def test_append_jsonl(self, temp_dirs):
        """Test appending to JSONL logs."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Log some entries
        await manager.append_jsonl("conversation", {"role": "user", "content": "Hello"})
        await manager.append_jsonl("conversation", {"role": "assistant", "content": "Hi!"})

        # Read log file
        log_path = logs_dir / "conversation.jsonl"
        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 2
        entry1 = json.loads(lines[0])
        assert entry1["role"] == "user"

    @pytest.mark.asyncio
    async def test_log_conversation(self, temp_dirs):
        """Test conversation logging."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        message = ConversationMessage(
            id="msg-1",
            role="user",
            content="Test message",
        )

        await manager.log_conversation(message)

        history = await manager.read_conversation_log()
        assert len(history) == 1
        assert history[0]["content"] == "Test message"

    @pytest.mark.asyncio
    async def test_atomic_write(self, temp_dirs):
        """Test that writes are atomic."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Create multiple items
        items = [
            WorkItem(
                id=f"item-{i}",
                item_type=WorkItemType.DIRECT_ANSWER,
                description=f"Item {i}",
            )
            for i in range(10)
        ]

        await manager.save_backlog(items)

        # Verify all items saved
        backlog = await manager.get_backlog()
        assert len(backlog) == 10

    @pytest.mark.asyncio
    async def test_atomic_write_recreates_missing_parent_dir(self, temp_dirs):
        """Atomic writes recreate missing parent directories before writing."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Simulate the mounted state directory disappearing at runtime.
        for file_path in state_dir.iterdir():
            if file_path.is_file():
                file_path.unlink()
        state_dir.rmdir()

        await manager.save_current_state({"run_id": "restored", "cycle_count": 1})

        assert state_dir.exists()
        assert (state_dir / "current_state.json").exists()

    @pytest.mark.asyncio
    async def test_commit_state(self, temp_dirs):
        """Test committing all state."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Make some changes
        await manager.save_build_state({
            "cycle_count": 5,
            "run_id": "test-run",
        })

        await manager.commit_state()

        # Verify state persisted
        state = await manager.get_build_state()
        assert state.get("cycle_count") == 5

    @pytest.mark.asyncio
    async def test_file_lock_blocks_across_manager_instances(self, temp_dirs):
        """Separate manager instances should coordinate through the OS lock file."""
        state_dir, logs_dir = temp_dirs
        manager1 = StateManager(state_dir, logs_dir)
        manager2 = StateManager(state_dir, logs_dir)
        await manager1.initialize()
        await manager2.initialize()

        entered_second_lock = asyncio.Event()

        async def acquire_second_lock() -> None:
            async with manager2._file_lock("current_state"):
                entered_second_lock.set()

        async with manager1._file_lock("current_state"):
            waiter = asyncio.create_task(acquire_second_lock())
            await asyncio.sleep(0.1)
            assert not entered_second_lock.is_set()

        await asyncio.wait_for(waiter, timeout=1.0)
        assert entered_second_lock.is_set()


class TestStateManagerCacheReads:
    """Tests that get_build_state/get_current_state return from cache."""

    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_get_build_state_returns_from_cache(self, temp_dirs):
        """After init, get_build_state returns cached data without hitting disk."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Save state to populate cache
        await manager.save_build_state({"build_in_progress": True, "custom_key": 42})

        # Corrupt the file on disk - cache should still return correct data
        with open(state_dir / "build_state.json", "w") as f:
            f.write('{"build_in_progress": false, "custom_key": 0}')

        state = await manager.get_build_state()
        assert state["custom_key"] == 42
        assert state["build_in_progress"] is True

    @pytest.mark.asyncio
    async def test_get_current_state_returns_from_cache(self, temp_dirs):
        """After init, get_current_state returns cached data without hitting disk."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Save state to populate cache
        await manager.save_current_state({"run_id": "cached-run", "cycle_count": 7})

        # Corrupt the file on disk - cache should still return correct data
        with open(state_dir / "current_state.json", "w") as f:
            f.write('{"run_id": "disk-run", "cycle_count": 0}')

        state = await manager.get_current_state()
        assert state["run_id"] == "cached-run"
        assert state["cycle_count"] == 7

    @pytest.mark.asyncio
    async def test_get_build_state_falls_back_to_disk_when_no_cache(self, temp_dirs):
        """If cache is empty, get_build_state reads from disk and populates cache."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Clear the cache manually to simulate missing cache entry
        manager._state_cache.pop("build_state", None)

        state = await manager.get_build_state()
        # Should have loaded from disk (defaults from init)
        assert isinstance(state, dict)
        # Should now be in cache
        assert "build_state" in manager._state_cache

    @pytest.mark.asyncio
    async def test_save_build_state_updates_cache(self, temp_dirs):
        """save_build_state updates the cache so subsequent reads are consistent."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        await manager.save_build_state({"build_in_progress": True, "task_id": "t1"})
        state = await manager.get_build_state()
        assert state["task_id"] == "t1"

        await manager.save_build_state({"build_in_progress": False, "task_id": "t2"})
        state = await manager.get_build_state()
        assert state["task_id"] == "t2"
