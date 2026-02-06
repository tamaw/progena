"""
Tests for dual state file separation.

Validates that:
- build_state.json only contains agent build data
- current_state.json only contains progenitor agent operational state (user session)
- schedule.json contains scheduled tasks (system feature, persists beyond sessions)
- State is properly separated and not cross-contaminated
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import (
    ConversationMessage,
    WorkItem,
    WorkItemType,
    WorkItemStatus,
    ScheduleEntry,
)


@pytest.fixture
async def state_manager():
    """Create a StateManager with temp directories."""
    with tempfile.TemporaryDirectory() as state_dir:
        with tempfile.TemporaryDirectory() as logs_dir:
            manager = StateManager(
                state_dir=Path(state_dir),
                logs_dir=Path(logs_dir),
            )
            await manager.initialize()
            yield manager
            await manager.close()


class TestBuildStateIsolation:
    """Test that build_state.json only contains build-related data."""

    async def test_build_state_default_fields(self, state_manager):
        """Test default build state contains build fields with new nested structure."""
        build_state = await state_manager.get_build_state()

        # Required core fields (top-level)
        core_fields = {
            "build_in_progress",
            "build_id",
            "build_started_at",
            "build_phase",
            "task_id",
            "ideal_state",
            "should_iterate",
            "iteration_stop_reason",
            "backlog",
            "build_settings",
        }

        # Nested structure fields
        nested_fields = {
            "task",
            "iteration",
            "spec",
            "phases",
            "history",
            "best",
            "clarification",
        }

        expected_fields = core_fields | nested_fields

        assert set(build_state.keys()) == expected_fields

    async def test_build_state_no_chat_fields(self, state_manager):
        """Ensure build_state doesn't have chat/operational fields."""
        build_state = await state_manager.get_build_state()

        forbidden_fields = [
            "messages",
            "schedule",
            "tool_calls",
            "attachments",
            "current_input",
            "pending_response",
            "current_decision",
        ]

        for field in forbidden_fields:
            assert field not in build_state, f"build_state should not contain '{field}'"

    async def test_save_backlog_to_build_state(self, state_manager):
        """Test that backlog is saved to build_state."""
        work_item = WorkItem(
            id="test-1",
            item_type=WorkItemType.BUILD_AGENT,
            status=WorkItemStatus.PENDING,
            description="Build a test agent",
        )

        await state_manager.save_backlog([work_item])

        build_state = await state_manager.get_build_state()
        assert len(build_state["backlog"]) == 1
        assert build_state["backlog"][0]["id"] == "test-1"

        # Verify current_state doesn't have backlog
        current_state = await state_manager.get_current_state()
        assert "backlog" not in current_state


class TestCurrentStateIsolation:
    """Test that current_state.json only contains operational data."""

    async def test_current_state_default_fields(self, state_manager):
        """Test default current state contains only operational fields.

        Note: schedule is NOT in current_state - it's stored in schedule.json
        as a system feature that persists beyond user sessions.
        """
        current_state = await state_manager.get_current_state()

        expected_fields = {
            "run_id",
            "cycle_count",
            "actions_this_cycle",
            "shutdown_requested",
            "last_checkpoint",
            "messages",
            # NOTE: schedule is NOT here - it's in schedule.json (system feature)
            "attachments",
            "tool_calls",
            "current_input",
            "pending_response",
            "current_decision",
            "current_build_request",
        }

        assert set(current_state.keys()) == expected_fields

    async def test_current_state_no_build_fields(self, state_manager):
        """Ensure current_state doesn't have build-specific fields."""
        current_state = await state_manager.get_current_state()

        forbidden_fields = [
            "build_in_progress",
            "build_phase",
            "build_messages",
            "ideal_state",
            "verification_score",
            "spec_doc",
            "backlog",
            "verification_reports",
            "lessons_learned",
            "artefacts",
        ]

        for field in forbidden_fields:
            assert field not in current_state, f"current_state should not contain '{field}'"

    async def test_save_messages_to_current_state(self, state_manager):
        """Test that messages are saved to current_state."""
        message = ConversationMessage(
            id="msg-1",
            role="user",
            content="Hello, progenitor!",
        )

        await state_manager.add_message(message)

        current_state = await state_manager.get_current_state()
        assert len(current_state["messages"]) == 1
        assert current_state["messages"][0]["content"] == "Hello, progenitor!"

        # Verify build_state doesn't have messages
        build_state = await state_manager.get_build_state()
        assert "messages" not in build_state or build_state.get("messages") == []

    async def test_add_tool_call_to_current_state(self, state_manager):
        """Test that tool calls are saved to current_state."""
        tool_call = {
            "tool": "file_read",
            "arguments": {"path": "/test/file.txt"},
            "result": "file contents",
            "success": True,
            "timestamp": "2024-01-01T00:00:00Z",
        }

        await state_manager.add_tool_call(tool_call)

        current_state = await state_manager.get_current_state()
        assert len(current_state["tool_calls"]) == 1
        assert current_state["tool_calls"][0]["tool"] == "file_read"

    async def test_add_attachment_to_current_state(self, state_manager):
        """Test that attachments are saved to current_state."""
        attachment = {
            "path": "/path/to/file.pdf",
            "type": "pdf",
            "added_at": "2024-01-01T00:00:00Z",
        }

        await state_manager.add_attachment(attachment)

        current_state = await state_manager.get_current_state()
        assert len(current_state["attachments"]) == 1
        assert current_state["attachments"][0]["path"] == "/path/to/file.pdf"


class TestScheduleManagement:
    """Test schedule is stored ONLY in schedule.json (system feature).

    Schedule is a system feature that persists beyond user chat sessions,
    so it's stored in schedule.json, NOT in current_state.json.
    Uses simplified ScheduleEntry model.
    """

    async def test_schedule_not_in_current_state(self, state_manager):
        """Test that schedule is NOT stored in current_state.

        Schedule is a system feature stored in schedule.json.
        """
        from app.progenitor.runtime.time_utils import utcnow
        entry = ScheduleEntry(
            id="test-schedule",
            description="Test scheduled task",
            instruction="Do something",
            run_at=utcnow(),
        )
        await state_manager.save_schedule([entry])

        # Verify schedule is NOT in current_state
        current_state = await state_manager.get_current_state()
        assert "schedule" not in current_state, "schedule should NOT be in current_state"

    async def test_schedule_entry_roundtrip(self, state_manager):
        """Test ScheduleEntry objects can be saved and retrieved from schedule.json."""
        from app.progenitor.runtime.time_utils import utcnow
        entry = ScheduleEntry(
            id="test-schedule",
            description="Run every 2 hours",
            instruction="Check something",
            run_at=utcnow(),
            repeat_minutes=120,
        )

        await state_manager.save_schedule([entry])

        retrieved = await state_manager.get_schedule()
        assert len(retrieved) == 1
        assert retrieved[0].id == "test-schedule"

    async def test_add_schedule_entry(self, state_manager):
        """Test adding a schedule entry via add_schedule_entry."""
        from app.progenitor.runtime.time_utils import utcnow
        entry = ScheduleEntry(
            id="new-schedule",
            description="New scheduled task",
            instruction="Do the new thing",
            run_at=utcnow(),
        )

        await state_manager.add_schedule_entry(entry)

        retrieved = await state_manager.get_schedule()
        assert len(retrieved) == 1
        assert retrieved[0].id == "new-schedule"

    async def test_remove_schedule_entry(self, state_manager):
        """Test removing a schedule entry."""
        from app.progenitor.runtime.time_utils import utcnow
        entry = ScheduleEntry(
            id="to-remove",
            description="Will be removed",
            instruction="Something",
            run_at=utcnow(),
        )
        await state_manager.save_schedule([entry])

        # Remove it
        result = await state_manager.remove_schedule_entry("to-remove")
        assert result is True

        # Verify it's gone
        retrieved = await state_manager.get_schedule()
        assert len(retrieved) == 0

    async def test_update_schedule_entry(self, state_manager):
        """Test updating a schedule entry."""
        from app.progenitor.runtime.time_utils import utcnow
        entry = ScheduleEntry(
            id="to-update",
            description="Original description",
            instruction="Original",
            run_at=utcnow(),
        )
        await state_manager.save_schedule([entry])

        # Update it
        result = await state_manager.update_schedule_entry(
            "to-update",
            {"description": "Updated description"}
        )
        assert result is True

        # Verify update
        retrieved = await state_manager.get_schedule()
        assert len(retrieved) == 1
        assert retrieved[0].description == "Updated description"


class TestStateUpdateMethods:
    """Test partial update methods work correctly."""

    async def test_update_current_state_partial(self, state_manager):
        """Test partial update only changes specified fields."""
        # Get initial state
        initial = await state_manager.get_current_state()
        initial_run_id = initial["run_id"]

        # Partial update
        await state_manager.update_current_state({
            "cycle_count": 42,
            "actions_this_cycle": 5,
        })

        # Verify update
        updated = await state_manager.get_current_state()
        assert updated["cycle_count"] == 42
        assert updated["actions_this_cycle"] == 5
        # Other fields unchanged
        assert updated["run_id"] == initial_run_id

    async def test_save_build_state_full_replace(self, state_manager):
        """Test save_build_state replaces entire state."""
        new_state = {
            "build_in_progress": True,
            "build_id": "build-123",
            "build_phase": "observe",
            "build_messages": [{"role": "system", "content": "Building..."}],
            "iteration": {"current": 1},
        }

        await state_manager.save_build_state(new_state)

        retrieved = await state_manager.get_build_state()
        assert retrieved["build_in_progress"] is True
        assert retrieved["build_id"] == "build-123"


class TestStateSeparationIntegrity:
    """Test that state files don't cross-contaminate."""

    async def test_build_and_current_independent(self, state_manager):
        """Test modifying one doesn't affect the other."""
        # Modify build state
        await state_manager.save_build_state({
            "build_in_progress": True,
            "build_id": "test-build",
        })

        # Modify current state
        await state_manager.update_current_state({
            "cycle_count": 100,
        })

        # Verify independence
        build_state = await state_manager.get_build_state()
        current_state = await state_manager.get_current_state()

        assert build_state.get("build_in_progress") is True
        assert "cycle_count" not in build_state

        assert current_state.get("cycle_count") == 100
        assert "build_in_progress" not in current_state

    async def test_concurrent_writes_safe(self, state_manager):
        """Test concurrent writes to different files are safe."""
        import asyncio

        async def write_build():
            for i in range(10):
                await state_manager.save_build_state({
                    "build_in_progress": True,
                    "iteration": {"current": i},
                })

        async def write_current():
            for i in range(10):
                await state_manager.update_current_state({
                    "cycle_count": i,
                })

        # Run concurrently
        await asyncio.gather(write_build(), write_current())

        # Both should have final values
        build_state = await state_manager.get_build_state()
        current_state = await state_manager.get_current_state()

        assert build_state["iteration"]["current"] == 9
        assert current_state["cycle_count"] == 9


class TestBuildMessagesTracking:
    """Test that build_messages are properly tracked in build_state."""

    async def test_build_messages_isolation(self, state_manager):
        """Build messages should be in build_state, not current_state."""
        build_state = {
            "build_in_progress": True,
            "build_id": "test-build",
            "build_messages": [
                {"role": "system", "content": "You are building an agent."},
                {"role": "user", "content": "Build me a calculator."},
                {"role": "assistant", "content": "Creating calculator agent..."},
            ],
        }

        await state_manager.save_build_state(build_state)

        retrieved = await state_manager.get_build_state()
        assert len(retrieved["build_messages"]) == 3

        # Current state should not have build_messages
        current = await state_manager.get_current_state()
        assert "build_messages" not in current
