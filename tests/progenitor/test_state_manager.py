"""Tests for active StateManager persistence paths."""

import asyncio
import copy
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.progenitor.state.langgraph_store import (
    LOG_NAMESPACE_PREFIX,
    STATE_NAMESPACE,
    LangGraphRuntimeStore,
)
from app.progenitor.state.manager import StateManager
from app.progenitor.state.models import (
    A2ATaskRecord,
    AsyncSubagentTaskRecord,
    BackgroundTaskEntry,
    ConversationMessage,
    ScheduleEntry,
    ScheduleRunRecord,
)


class FakeLangGraphStore:
    def __init__(self) -> None:
        self.items: dict[tuple[tuple[str, ...], str], dict] = {}
        self.search_queries: list[str | None] = []

    async def aget(self, namespace, key, **kwargs):
        value = self.items.get((tuple(namespace), key))
        return SimpleNamespace(value=copy.deepcopy(value)) if value is not None else None

    async def aput(self, namespace, key, value, **kwargs):
        self.items[(tuple(namespace), key)] = copy.deepcopy(value)

    async def asearch(
        self, namespace_prefix, *, query=None, filter=None, limit=10, offset=0, **kwargs
    ):
        self.search_queries.append(query)
        prefix = tuple(namespace_prefix)
        rows = []
        for (namespace, key), value in self.items.items():
            if namespace[: len(prefix)] != prefix:
                continue
            if filter and any(value.get(name) != expected for name, expected in filter.items()):
                continue
            rows.append(SimpleNamespace(namespace=namespace, key=key, value=value))
        return rows[offset : offset + limit]


class SlowFakeLangGraphStore(FakeLangGraphStore):
    async def aget(self, namespace, key, **kwargs):
        await asyncio.sleep(0)
        return await super().aget(namespace, key, **kwargs)

    async def aput(self, namespace, key, value, **kwargs):
        await asyncio.sleep(0)
        await super().aput(namespace, key, value, **kwargs)


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
        assert (state_dir / "async_subagent_tasks.json").exists()
        assert (state_dir / "background_tasks.json").exists()
        assert (state_dir.parent / "config" / "agents.json").exists()
        assert (state_dir.parent / "config" / "mcp.json").exists()
        assert not (state_dir / "build_state.json").exists()

    @pytest.mark.asyncio
    async def test_schedule_management(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        from app.progenitor.utils import utcnow

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

    @pytest.mark.asyncio
    async def test_background_task_queue_persistence(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        entry = BackgroundTaskEntry(
            id="bg-1",
            content="Reply exactly BG_TOKEN",
            metadata={"thread_id": "thread-bg", "notify_on_completion": True},
        )

        await manager.enqueue_background_task(entry)
        queued = await manager.get_background_tasks()
        assert len(queued) == 1
        assert queued[0].id == "bg-1"

        dequeued = await manager.dequeue_background_task()
        assert dequeued is not None
        assert dequeued.content == "Reply exactly BG_TOKEN"
        assert await manager.get_background_tasks() == []

    @pytest.mark.asyncio
    async def test_schedule_run_persistence(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        record = ScheduleRunRecord(
            run_id="schedrun_123",
            schedule_id="cron_test123",
            thread_id="cron_test123_abcd1234",
            message="Reply exactly TEST",
        )

        await manager.upsert_schedule_run(record)
        loaded = await manager.get_schedule_run("schedrun_123")
        assert loaded is not None
        assert loaded.status == "queued"
        assert loaded.thread_id == "cron_test123_abcd1234"

        updated = await manager.update_schedule_run(
            "schedrun_123",
            {
                "status": "completed",
                "result_status": "completed",
                "response": "TEST",
            },
        )
        assert updated is True
        refreshed = await manager.get_schedule_run("schedrun_123")
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.response == "TEST"

    @pytest.mark.asyncio
    async def test_native_runtime_store_replaces_file_documents(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(state_dir, logs_dir, runtime_store=runtime_store)
        await manager.initialize()

        message = ConversationMessage(id="msg-native", role="user", content="Hello native store")
        await manager.add_message(message)
        await manager.add_tool_call({"id": "tool-1", "name": "demo"})
        await manager.add_attachment({"id": "att-1", "content": b"bytes".hex()})

        state = await manager.get_current_state()
        assert not (state_dir / "current_state.json").exists()
        assert state["messages"][0]["content"] == "Hello native store"
        assert state["tool_calls"] == [{"id": "tool-1", "name": "demo"}]
        assert state["attachments"] == [{"id": "att-1", "content": b"bytes".hex()}]

        await manager.commit_state()
        current_state = native_store.items[(STATE_NAMESPACE, "current_state")]["payload"]
        assert current_state["messages"][0]["id"] == "msg-native"

        await manager.close()

    @pytest.mark.asyncio
    async def test_native_runtime_store_serializes_current_state_mutations(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        native_store = SlowFakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(state_dir, logs_dir, runtime_store=runtime_store)
        await manager.initialize()

        await asyncio.gather(
            manager.add_message(
                ConversationMessage(id="msg-concurrent", role="user", content="Concurrent")
            ),
            manager.add_tool_call({"id": "tool-concurrent", "name": "demo"}),
            manager.add_attachment({"id": "att-concurrent", "content": "payload"}),
        )

        state = await manager.get_current_state()
        assert [message["id"] for message in state["messages"]] == ["msg-concurrent"]
        assert state["tool_calls"] == [{"id": "tool-concurrent", "name": "demo"}]
        assert state["attachments"] == [{"id": "att-concurrent", "content": "payload"}]

    @pytest.mark.asyncio
    async def test_async_subagent_task_persistence(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        record = AsyncSubagentTaskRecord(
            task_id="async-task-1",
            agent_name="hello-world-async",
            assistant_id="hello-world-async",
            agent_url="http://127.0.0.1:9501/agent-protocol",
            parent_thread_id="thread-main",
            run_id="run-1",
            request_text="Return the hello token later.",
        )

        await manager.upsert_async_subagent_task(record)
        loaded = await manager.get_async_subagent_task("async-task-1")
        assert loaded is not None
        assert loaded.agent_name == "hello-world-async"
        assert loaded.status == "running"

        updated = await manager.update_async_subagent_task(
            "async-task-1",
            {
                "status": "success",
                "summary": "HELLO_FROM_ASYNC_SUBAGENT",
            },
        )
        assert updated is True
        refreshed = await manager.get_async_subagent_task("async-task-1")
        assert refreshed is not None
        assert refreshed.status == "success"
        assert refreshed.summary == "HELLO_FROM_ASYNC_SUBAGENT"

    @pytest.mark.asyncio
    async def test_langgraph_runtime_store_round_trips_documents(self):
        native_store = FakeLangGraphStore()
        store = LangGraphRuntimeStore(native_store)

        assert await store.get_document("current_state", {"messages": []}) == {"messages": []}
        await store.update_document("current_state", {"cycle_count": 2}, {"messages": []})

        assert await store.get_document("current_state", {}) == {
            "messages": [],
            "cycle_count": 2,
        }
        assert native_store.items[(STATE_NAMESPACE, "current_state")] == {
            "payload": {"messages": [], "cycle_count": 2}
        }

    @pytest.mark.asyncio
    async def test_langgraph_runtime_store_appends_logs_as_native_items(self):
        native_store = FakeLangGraphStore()
        store = LangGraphRuntimeStore(native_store)

        await store.write_log(
            source="tool",
            message="started",
            payload={"event": "tool_started", "api_key": "sk-secret"},
            timestamp="2024-01-01T00:00:00+00:00",
        )

        rows = await store.query_logs(source="tool", search="tool_started")
        assert len(rows) == 1
        assert rows[0]["message"] == "started"
        assert rows[0]["payload"]["api_key"] == "[REDACTED]"
        assert native_store.search_queries == [None]
        assert next(iter(native_store.items))[0][:3] == (*LOG_NAMESPACE_PREFIX, "tool")

    async def test_initialize_keeps_schedule_only_in_memory_without_runtime_store(self, tmp_path):
        state_dir = tmp_path / "data" / "state"
        logs_dir = tmp_path / "logs"
        state_dir.mkdir(parents=True)
        logs_dir.mkdir()

        manager = StateManager(state_dir, logs_dir)
        await manager.initialize()

        assert await manager.get_schedule() == []
        assert not (state_dir / "schedule.json").exists()
        assert not (state_dir.parent / "config" / "schedule.json").exists()

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
    async def test_native_runtime_store_logs_write_rows_instead_of_documents(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(
            state_dir,
            logs_dir,
            runtime_store=runtime_store,
        )
        await manager.initialize()

        await manager.append_jsonl(
            "schedule", {"event": "schedule_created", "schedule_id": "sched-1"}
        )

        assert (STATE_NAMESPACE, "log_schedule") not in native_store.items
        log_entries = [
            item
            for (namespace, _key), item in native_store.items.items()
            if namespace == (*LOG_NAMESPACE_PREFIX, "schedule")
        ]
        assert log_entries == [
            {
                "timestamp": log_entries[0]["timestamp"],
                "level": "info",
                "source": "schedule",
                "message": "schedule_created",
                "payload": {"event": "schedule_created", "schedule_id": "sched-1"},
                "relative_path": "schedule.jsonl",
            }
        ]

    @pytest.mark.asyncio
    async def test_native_runtime_store_conversation_logs_round_trip(self, temp_dirs):
        state_dir, logs_dir = temp_dirs
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(
            state_dir,
            logs_dir,
            runtime_store=runtime_store,
        )
        await manager.initialize()

        message = ConversationMessage(id="msg-native-log", role="user", content="Log this natively")

        await manager.log_conversation(message)

        history = await manager.read_conversation_log()
        assert len(history) == 1
        assert history[0]["content"] == "Log this natively"
        current_state = native_store.items[(STATE_NAMESPACE, "current_state")]["payload"]
        assert current_state["messages"][0]["id"] == "msg-native-log"

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
