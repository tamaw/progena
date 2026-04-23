"""Tests for state manager."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import (
    A2ATaskRecord,
    ConversationMessage,
    ScheduleEntry,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)
from app.progenitor.state.persistence import PERSISTED_STATE_SCHEMA_VERSION, StatePersistence


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
        assert (state_dir / "a2a_tasks.json").exists()
        assert (state_dir.parent / "config" / "agents.json").exists()
        assert (state_dir.parent / "config" / "mcp.json").exists()
        assert (state_dir.parent / "config" / "schedule.json").exists()

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
    async def test_get_backlog_rejects_unknown_item_type(self, temp_dirs):
        """Persisted backlog parsing should fail on unknown work item types."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        build_state = await manager.get_build_state()
        build_state["backlog"] = [
            {
                "id": "bad-item",
                "item_type": "hallucinated_action",
                "description": "Bad persisted item",
            }
        ]
        await manager.save_build_state(build_state)
        manager._state_cache.pop("build_state", None)

        with pytest.raises(ValidationError):
            await manager.get_backlog()

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
        status_val = updated.status.value if hasattr(updated.status, "value") else updated.status
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
    async def test_a2a_task_persistence(self, temp_dirs):
        """Tracked A2A task records are persisted and can be updated."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        record = A2ATaskRecord(
            task_id="observe_123",
            agent_name="observe_agent",
            agent_url="http://localhost:8108",
            context_id="ctx-123",
            source="call_agent",
            request_text="Inspect logs",
        )

        await manager.upsert_a2a_task(record)
        loaded = await manager.get_a2a_task("observe_123")
        assert loaded is not None
        assert loaded.agent_name == "observe_agent"
        assert loaded.status == "submitted"

        updated = await manager.update_a2a_task(
            "observe_123",
            {
                "status": "completed",
                "summary": "Observe task completed.",
            },
        )
        assert updated is True
        refreshed = await manager.get_a2a_task("observe_123")
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.summary == "Observe task completed."

    async def test_initialize_migrates_legacy_schedule_file(self, tmp_path):
        """Legacy schedules under data/state are copied into config on initialize."""
        state_dir = tmp_path / "data" / "state"
        logs_dir = tmp_path / "logs"
        state_dir.mkdir(parents=True)
        logs_dir.mkdir()
        legacy_schedule = [
            {
                "id": "legacy-schedule",
                "message": "Carry forward existing schedule",
                "cron_expression": "0 9 * * *",
                "created_at": "2024-01-01T00:00:00Z",
            }
        ]
        (state_dir / "schedule.json").write_text(json.dumps(legacy_schedule), encoding="utf-8")

        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        migrated = json.loads(
            (state_dir.parent / "config" / "schedule.json").read_text(encoding="utf-8")
        )
        assert migrated == legacy_schedule

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

        persisted = json.loads((state_dir / "build_state.json").read_text(encoding="utf-8"))
        assert persisted["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION

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

        await manager.save_current_state({"thread_id": "thread-restored", "cycle_count": 1})

        assert state_dir.exists()
        assert (state_dir / "current_state.json").exists()

    @pytest.mark.asyncio
    async def test_atomic_write_preserves_original_on_fsync_failure(self, temp_dirs, monkeypatch):
        """A failed fsync leaves the original state file untouched and cleans temp files."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()
        await manager.save_current_state({"thread_id": "thread-original", "cycle_count": 1})

        original = (state_dir / "current_state.json").read_text(encoding="utf-8")
        real_fsync = os.fsync
        call_count = 0

        def fail_on_write(fd: int) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError("No space left on device")
            real_fsync(fd)

        monkeypatch.setattr("app.progenitor.state.persistence.os.fsync", fail_on_write)

        with pytest.raises(OSError, match="No space left on device"):
            await manager.save_current_state({"thread_id": "thread-new", "cycle_count": 2})

        assert (state_dir / "current_state.json").read_text(encoding="utf-8") == original
        assert list(state_dir.glob(".current_state.json.*.tmp")) == []

    @pytest.mark.asyncio
    async def test_commit_state(self, temp_dirs):
        """Test committing all state."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Make some changes
        await manager.save_build_state(
            {
                "cycle_count": 5,
                "task_id": "test-run",
            }
        )

        await manager.commit_state()

        # Verify state persisted
        state = await manager.get_build_state()
        assert state.get("cycle_count") == 5

    @pytest.mark.asyncio
    async def test_get_build_state_refreshes_after_external_edit(self, temp_dirs):
        """Build-state reads should invalidate stale cache after external edits."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        initial = await manager.get_build_state()
        initial["task_id"] = "original"
        await manager.save_build_state(initial)

        build_path = state_dir / "build_state.json"
        external = json.loads(build_path.read_text(encoding="utf-8"))
        external["task_id"] = "external"
        build_path.write_text(json.dumps(external), encoding="utf-8")

        refreshed = await manager.get_build_state()

        assert refreshed["task_id"] == "external"

    @pytest.mark.asyncio
    async def test_close_does_not_overwrite_external_edits_with_clean_cache(self, temp_dirs):
        """Closing should not flush stale cache back over externally edited files."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        current_path = state_dir / "current_state.json"
        external = json.loads(current_path.read_text(encoding="utf-8"))
        external["thread_id"] = "external-thread"
        current_path.write_text(json.dumps(external), encoding="utf-8")

        await manager.close()

        persisted = json.loads(current_path.read_text(encoding="utf-8"))
        assert persisted["thread_id"] == "external-thread"

    @pytest.mark.asyncio
    async def test_update_current_state_merges_on_fresh_disk_state(self, temp_dirs):
        """Partial current-state updates should not overwrite external file edits."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        current_path = state_dir / "current_state.json"
        external = json.loads(current_path.read_text(encoding="utf-8"))
        external["thread_id"] = "external-thread"
        current_path.write_text(json.dumps(external), encoding="utf-8")

        await manager.update_current_state({"cycle_count": 9})

        persisted = json.loads(current_path.read_text(encoding="utf-8"))
        assert persisted["thread_id"] == "external-thread"
        assert persisted["cycle_count"] == 9

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

    @pytest.mark.asyncio
    async def test_stale_lockfile_does_not_block_acquisition(self, temp_dirs):
        """A leftover lockfile path without an active OS lock should not block startup."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        lock_path = state_dir / "current_state.json.lock"
        lock_path.write_text("stale", encoding="utf-8")

        async with manager._file_lock("current_state"):
            assert lock_path.exists()

    @pytest.mark.asyncio
    async def test_initialize_rotates_broken_current_state_file(self, temp_dirs):
        """Malformed current_state.json is quarantined and rebuilt from defaults."""
        state_dir, logs_dir = temp_dirs
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "current_state.json").write_text('{"thread_id": "broken"', encoding="utf-8")

        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        broken_files = sorted(state_dir.glob("current_state.json.broken-*"))
        assert len(broken_files) == 1
        assert broken_files[0].read_text(encoding="utf-8") == '{"thread_id": "broken"'

        current_state = await manager.get_current_state()
        assert current_state["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION
        assert "thread_id" in current_state

    @pytest.mark.asyncio
    async def test_initialize_migrates_legacy_current_state_schema(self, temp_dirs):
        """Legacy unversioned current_state.json is migrated in place on startup."""
        state_dir, logs_dir = temp_dirs
        state_dir.mkdir(parents=True, exist_ok=True)
        legacy_state = {"thread_id": "legacy-thread", "cycle_count": 4}
        (state_dir / "current_state.json").write_text(
            json.dumps(legacy_state),
            encoding="utf-8",
        )

        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        current_state = await manager.get_current_state()
        assert current_state["thread_id"] == "legacy-thread"
        assert current_state["cycle_count"] == 4
        assert current_state["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION

        persisted = json.loads((state_dir / "current_state.json").read_text(encoding="utf-8"))
        assert persisted["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION


class TestStateManagerCacheReads:
    """Tests that state reads refresh when on-disk content changes."""

    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_get_build_state_refreshes_when_disk_changes(self, temp_dirs):
        """get_build_state should invalidate stale cache after an external edit."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Save state to populate cache
        await manager.save_build_state({"build_in_progress": True, "custom_key": 42})

        # Edit the file on disk - the next read should refresh from disk.
        with open(state_dir / "build_state.json", "w") as f:
            f.write('{"build_in_progress": false, "custom_key": 0}')

        state = await manager.get_build_state()
        assert state["custom_key"] == 0
        assert state["build_in_progress"] is False

    @pytest.mark.asyncio
    async def test_get_current_state_refreshes_when_disk_changes(self, temp_dirs):
        """get_current_state should invalidate stale cache after an external edit."""
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        # Save state to populate cache
        await manager.save_current_state({"thread_id": "thread-cached", "cycle_count": 7})

        # Edit the file on disk - the next read should refresh from disk.
        with open(state_dir / "current_state.json", "w") as f:
            f.write('{"thread_id": "thread-disk", "cycle_count": 0}')

        state = await manager.get_current_state()
        assert state["thread_id"] == "thread-disk"
        assert state["cycle_count"] == 0

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


class TestStatePersistence:
    """Tests for the low-level StatePersistence helper."""

    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_read_quarantines_broken_json(self, temp_dirs):
        """Malformed JSON is rotated aside instead of crashing reads."""
        state_dir, logs_dir = temp_dirs
        persistence = StatePersistence(state_dir, logs_dir)
        await persistence.initialize()

        broken_path = state_dir / "current_state.json"
        broken_path.write_text('{"thread_id": "broken"', encoding="utf-8")

        loaded = await persistence.read("current_state.json")

        assert loaded is None
        broken_files = sorted(state_dir.glob("current_state.json.broken-*"))
        assert len(broken_files) == 1
        assert broken_files[0].read_text(encoding="utf-8") == '{"thread_id": "broken"'

    @pytest.mark.asyncio
    async def test_read_migrates_legacy_schema_version(self, temp_dirs):
        """Legacy runtime-state payloads are migrated and rewritten on read."""
        state_dir, logs_dir = temp_dirs
        persistence = StatePersistence(state_dir, logs_dir)
        await persistence.initialize()

        current_state_path = state_dir / "current_state.json"
        current_state_path.write_text(
            json.dumps({"thread_id": "legacy-thread", "cycle_count": 1}),
            encoding="utf-8",
        )

        loaded = await persistence.read("current_state.json")

        assert loaded["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION
        persisted = json.loads(current_state_path.read_text(encoding="utf-8"))
        assert persisted["schema_version"] == PERSISTED_STATE_SCHEMA_VERSION
