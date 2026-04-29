"""Tests for active StateManager persistence paths."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import A2ATaskRecord, ConversationMessage, ScheduleEntry


class TestStateManager:
    """Tests for the StateManager class."""

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary state and logs directories."""
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_initialize_creates_files(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        assert (state_dir / "current_state.json").exists()
        assert (state_dir / "a2a_tasks.json").exists()
        assert (state_dir.parent / "config" / "agents.json").exists()
        assert (state_dir.parent / "config" / "mcp.json").exists()
        assert (state_dir.parent / "config" / "schedule.json").exists()
        assert not (state_dir / "build_state.json").exists()

    @pytest.mark.asyncio
    async def test_schedule_management(self, temp_dirs):
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
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        await manager.append_jsonl("conversation", {"role": "user", "content": "Hello"})
        await manager.append_jsonl("conversation", {"role": "assistant", "content": "Hi!"})

        log_path = logs_dir / "conversation.jsonl"
        assert log_path.exists()

        with open(log_path) as f:
            lines = f.readlines()

        assert len(lines) == 2
        entry1 = json.loads(lines[0])
        assert entry1["role"] == "user"

    @pytest.mark.asyncio
    async def test_log_conversation(self, temp_dirs):
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
    async def test_atomic_write_recreates_missing_parent_dir(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        for file_path in state_dir.iterdir():
            if file_path.is_file():
                file_path.unlink()
        state_dir.rmdir()

        await manager.save_current_state({"thread_id": "thread-restored", "cycle_count": 1})

        assert state_dir.exists()
        assert (state_dir / "current_state.json").exists()

    @pytest.mark.asyncio
    async def test_commit_state(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        await manager.save_current_state({"thread_id": "thread-commit", "cycle_count": 5})
        await manager.commit_state()

        state = await manager.get_current_state()
        assert state.get("cycle_count") == 5

    @pytest.mark.asyncio
    async def test_file_lock_blocks_across_manager_instances(self, temp_dirs):
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
    """Tests that get_current_state returns from cache."""

    @pytest.fixture
    def temp_dirs(self):
        with tempfile.TemporaryDirectory() as state_dir, tempfile.TemporaryDirectory() as logs_dir:
            yield Path(state_dir), Path(logs_dir)

    @pytest.mark.asyncio
    async def test_get_current_state_returns_from_cache(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        await manager.save_current_state({"thread_id": "thread-cached", "cycle_count": 7})

        with open(state_dir / "current_state.json", "w") as f:
            f.write('{"thread_id": "thread-disk", "cycle_count": 0}')

        state = await manager.get_current_state()
        assert state["thread_id"] == "thread-cached"
        assert state["cycle_count"] == 7
