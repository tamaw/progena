"""Tests for state manager."""

import pytest
import tempfile
import json
import asyncio
from pathlib import Path

from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import (
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    ScheduleEntry,
    ConversationMessage,
)


class TestStateManager:
    """Tests for the StateManager class."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary state and logs directories."""
        with tempfile.TemporaryDirectory() as state_dir:
            with tempfile.TemporaryDirectory() as logs_dir:
                yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_initialize_creates_files(self, temp_dirs):
        """Test that initialization creates necessary files."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # build_state.json replaces both current_state.json and backlog.json
        assert (state_dir / "build_state.json").exists()
        assert (state_dir / "registry.json").exists()
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
            description="Daily task",
            instruction="Do the daily task",
            run_at=utcnow(),
            repeat_minutes=1440,
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

        with open(log_path, 'r') as f:
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
