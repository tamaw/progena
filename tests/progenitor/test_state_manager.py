"""Tests for active StateManager persistence paths (runtime-store-only)."""

import asyncio
import copy
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
from tests.progenitor.conftest import _FakeRuntimeStore


class FakeLangGraphStore:
    """In-memory fake implementing the LangGraph native store interface."""

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

    async def adelete(self, namespace, key, **kwargs):
        self.items.pop((tuple(namespace), key), None)


class SlowFakeLangGraphStore(FakeLangGraphStore):
    """Fake store with tiny artificial delay to expose races."""

    async def aget(self, namespace, key, **kwargs):
        await asyncio.sleep(0)
        return await super().aget(namespace, key, **kwargs)

    async def aput(self, namespace, key, value, **kwargs):
        await asyncio.sleep(0)
        await super().aput(namespace, key, value, **kwargs)


class TestStateManager:
    """Tests for the StateManager class (runtime-store-only)."""

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_initialize_creates_documents(self):
        """StateManager.initialize() populates the runtime store with document defaults."""
        store = _FakeRuntimeStore()
        manager = StateManager(runtime_store=store)
        await manager.initialize()

        assert "current_state" in store._documents
        assert "a2a_tasks" in store._documents
        assert "async_subagent_tasks" in store._documents
        assert "background_tasks" in store._documents
        assert "mcp" in store._documents
        assert "schedule_runs" in store._documents
        assert "health" in store._documents

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_schedule_management(self):
        manager = StateManager(runtime_store=_FakeRuntimeStore())
        await manager.initialize()

        from app.progenitor.utils import utcnow

        entry = ScheduleEntry(
            id="schedule-1",
            message="Do the daily task",
            cron_expression="0 9 * * *",
            created_at=utcnow(),
        )

        await manager.save_schedule({entry.id: entry})
        schedule = await manager.get_schedule()

        assert len(schedule) == 1
        assert schedule["schedule-1"].id == "schedule-1"

    # ------------------------------------------------------------------
    # A2A tasks
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_a2a_task_persistence(self):
        manager = StateManager(runtime_store=_FakeRuntimeStore())
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

    # ------------------------------------------------------------------
    # Background task queue
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_background_task_queue_persistence(self):
        manager = StateManager(runtime_store=_FakeRuntimeStore())
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

    # ------------------------------------------------------------------
    # Schedule runs
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_schedule_run_persistence(self):
        manager = StateManager(runtime_store=_FakeRuntimeStore())
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

    # ------------------------------------------------------------------
    # Native-store document round-trip
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_native_runtime_store_replaces_file_documents(self):
        """StateManager writes to the native store; current_state round-trips."""
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        message = ConversationMessage(id="msg-native", role="user", content="Hello native store")
        await manager.update_current_state(
            {
                "messages": [message.model_dump(mode="json")],
                "tool_calls": [{"id": "tool-1", "name": "demo"}],
                "attachments": [{"id": "att-1", "content": b"bytes".hex()}],
            }
        )

        state = await manager.get_current_state()
        assert state["messages"][0]["content"] == "Hello native store"
        assert state["tool_calls"] == [{"id": "tool-1", "name": "demo"}]
        assert state["attachments"] == [{"id": "att-1", "content": b"bytes".hex()}]

        await manager.commit_state()
        current_state = native_store.items[(STATE_NAMESPACE, "current_state")]["payload"]
        assert current_state["messages"][0]["id"] == "msg-native"

    # ------------------------------------------------------------------
    # MCP config cross-process write visibility
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_mcp_config_reflects_cross_process_store_writes(self):
        """get_mcp_config reads fresh from the store, not from a stale cache."""
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        # Startup seeds an empty config and caches it.
        assert (await manager.get_mcp_config())["mcpServers"] == {}

        # Simulate the backend writing the same shared document directly (as the
        # UI does). A cached read would hide this; get_mcp_config must read fresh.
        await native_store.aput(
            STATE_NAMESPACE,
            "mcp",
            {"payload": {"mcpServers": {"jina": {"url": "https://x", "enabled": True}}}},
        )

        config = await manager.get_mcp_config()
        assert "jina" in config["mcpServers"]
        assert config["mcpServers"]["jina"]["url"] == "https://x"

    @pytest.mark.asyncio
    async def test_knowledge_config_reads_and_writes_shared_store(self):
        """Knowledge settings use the same runtime-store document as the backend."""
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        assert await manager.get_knowledge_config() == {"bases": []}

        knowledge_document = {"bases": [{"id": "docs", "name": "Docs"}]}
        await native_store.aput(
            STATE_NAMESPACE,
            "knowledge",
            {"payload": knowledge_document},
        )
        assert await manager.get_knowledge_config() == knowledge_document

        await manager.save_knowledge_config({"bases": []})
        assert await manager.get_knowledge_config() == {"bases": []}

    # ------------------------------------------------------------------
    # Serialized mutations (concurrent tool calls, same turn)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_native_runtime_store_serializes_current_state_mutations(self):
        """Concurrent add_message / add_tool_call / add_attachment are serialized."""
        native_store = SlowFakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        await asyncio.gather(
            manager.update_current_state(
                {
                    "messages": [
                        ConversationMessage(
                            id="msg-concurrent", role="user", content="Concurrent"
                        ).model_dump(mode="json")
                    ],
                }
            ),
            manager.update_current_state(
                {"tool_calls": [{"id": "tool-concurrent", "name": "demo"}]}
            ),
            manager.update_current_state(
                {"attachments": [{"id": "att-concurrent", "content": "payload"}]}
            ),
        )

        state = await manager.get_current_state()
        assert [message["id"] for message in state["messages"]] == ["msg-concurrent"]
        assert state["tool_calls"] == [{"id": "tool-concurrent", "name": "demo"}]
        assert state["attachments"] == [{"id": "att-concurrent", "content": "payload"}]

    # ------------------------------------------------------------------
    # Async subagent task
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_async_subagent_task_persistence(self):
        manager = StateManager(runtime_store=_FakeRuntimeStore())
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

    # ------------------------------------------------------------------
    # LangGraphRuntimeStore low-level document round-trip
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_langgraph_runtime_store_round_trips_documents(self):
        native_store = FakeLangGraphStore()
        store = LangGraphRuntimeStore(native_store)

        assert await store.get_document("current_state", {"messages": []}) == {"messages": []}
        await store.save_document("current_state", {"messages": [], "cycle_count": 2})

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
        rows = [
            value
            for (namespace, _key), value in native_store.items.items()
            if namespace == (*LOG_NAMESPACE_PREFIX, "tool")
        ]
        assert len(rows) == 1
        assert rows[0]["message"] == "started"
        assert rows[0]["payload"]["api_key"] == "[REDACTED]"
        assert native_store.search_queries == []

    # ------------------------------------------------------------------
    # Append JSONL (via runtime store logs)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_log_conversation_round_trip(self):
        """log_conversation writes log rows into the runtime store, not files."""
        store = _FakeRuntimeStore()
        manager = StateManager(runtime_store=store)
        await manager.initialize()

        await manager.log_conversation(
            ConversationMessage(id="msg-1", role="user", content="Hello")
        )
        await manager.log_conversation(
            ConversationMessage(id="msg-2", role="assistant", content="Hi!")
        )

        logs = store._logs.get("conversation", [])
        assert len(logs) == 2
        assert logs[0]["payload"]["role"] == "user"
        assert logs[1]["payload"]["role"] == "assistant"

    # ------------------------------------------------------------------
    # Conversation log round-trip
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_log_conversation(self):
        """log_conversation persists a message and read_conversation_log retrieves it."""
        manager = StateManager(runtime_store=_FakeRuntimeStore())
        await manager.initialize()

        message = ConversationMessage(
            id="msg-1",
            role="user",
            content="Test message",
        )

        await manager.log_conversation(message)

        history = manager._runtime_store._logs["conversation"]
        assert len(history) == 1
        assert history[0]["payload"]["content"] == "Test message"

    # ------------------------------------------------------------------
    # Native store log rows (not state documents)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_native_runtime_store_logs_write_rows_instead_of_documents(self):
        """Appending JSONL with a native store writes log rows, not state documents."""
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        await manager.log_schedule_event({"event": "schedule_created", "schedule_id": "sched-1"})

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
    async def test_native_runtime_store_conversation_logs_round_trip(self):
        """Conversation messages are logged to the audit trail only (they live
        in the LangGraph checkpoint, not in current_state)."""
        native_store = FakeLangGraphStore()
        runtime_store = LangGraphRuntimeStore(native_store)
        manager = StateManager(runtime_store=runtime_store)
        await manager.initialize()

        message = ConversationMessage(id="msg-native-log", role="user", content="Log this natively")

        await manager.log_conversation(message)

        history = [
            value
            for (namespace, _key), value in native_store.items.items()
            if namespace == (*LOG_NAMESPACE_PREFIX, "conversation")
        ]
        assert len(history) == 1
        assert history[0]["payload"]["content"] == "Log this natively"

    # ------------------------------------------------------------------
    # commit_state flushes cached state to the runtime store
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_commit_state(self):
        """commit_state persists cached state documents into the runtime store."""
        store = _FakeRuntimeStore()
        manager = StateManager(runtime_store=store)
        await manager.initialize()

        await manager.save_current_state({"thread_id": "thread-commit", "cycle_count": 5})
        await manager.commit_state()

        doc = store._documents.get("current_state")
        assert doc is not None
        assert doc.get("cycle_count") == 5
        assert doc.get("thread_id") == "thread-commit"


class TestStateManagerCacheReads:
    """Tests that get_current_state returns from cache."""

    @pytest.mark.asyncio
    async def test_get_current_state_returns_from_cache(self):
        """get_current_state reads from the in-memory cache, not a stale store value."""
        store = _FakeRuntimeStore()
        manager = StateManager(runtime_store=store)
        await manager.initialize()

        await manager.save_current_state({"thread_id": "thread-cached", "cycle_count": 7})

        # Simulate a cross-process / cross-instance store write that bypasses
        # the cache.  A cache-miss would pick this up; a cache-hit must not.
        store._documents["current_state"] = {"thread_id": "thread-store", "cycle_count": 0}

        state = await manager.get_current_state()
        assert state["thread_id"] == "thread-cached"
        assert state["cycle_count"] == 7
