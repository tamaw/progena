"""High-level state manager façade for Progenitor persistence."""

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..utils import utcnow_isoformat
from . import (
    a2a_store,
    acp_store,
    async_subagent_store,
    background_store,
    config_store,
    log_store,
    session_store,
)
from .langgraph_store import LangGraphRuntimeStore
from .layout import CORE_FILES, LOG_FILES, core_file_path, ensure_initial_state
from .models import (
    A2ATaskRecord,
    ACPTaskRecord,
    AsyncSubagentTaskRecord,
    BackgroundTaskEntry,
    ConversationMessage,
    ScheduleEntry,
    ScheduleRunRecord,
)
from .persistence import StatePersistence

logger = logging.getLogger(__name__)


def _structured_log_message(log_name: str, payload: dict[str, Any]) -> str:
    """Extract a concise searchable message from one structured log payload."""
    for field in ("content", "message", "event", "id"):
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{log_name} event"


class StateManager:
    """Manages persistence of Progenitor state with file locking.

    Core runtime data is split across state and config files:
    - current_state.json: Progenitor agent operational state (chat, tool calls)
    - registry/mcp config files for backend-managed metadata

    Provides:
    - Atomic writes for crash safety
    - File locking for concurrent access
    - Cached state for performance
    - JSONL logging with secret redaction
    - CQ-010: Full async context manager protocol for resource handling

    Usage:
        async with StateManager(state_dir, logs_dir) as manager:
            state = await manager.get_current_state()
            # ... work with state ...
        # Resources automatically cleaned up
    """

    CORE_FILES = CORE_FILES
    LOG_FILES = LOG_FILES

    def __init__(
        self,
        state_dir: Path,
        logs_dir: Path,
        lock_timeout: int = 10,
        runtime_store: LangGraphRuntimeStore | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.config_dir = self.state_dir.parent / "config"
        self.logs_dir = Path(logs_dir)
        self.lock_timeout = lock_timeout
        self._persistence = StatePersistence(
            self.state_dir,
            self.logs_dir,
            lock_timeout,
            config_dir=self.config_dir,
        )
        self._state_cache: dict[str, Any] = {}
        self._runtime_store = runtime_store
        self._runtime_current_state_lock = asyncio.Lock()
        self._initialized = False

    def _core_file_path(self, name: str) -> Path:
        """Resolve absolute path for a core state/config file."""
        return core_file_path(name, self.state_dir, self.config_dir)

    async def __aenter__(self) -> "StateManager":
        """CQ-010: Async context manager entry - initialize resources."""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """CQ-010: Async context manager exit - cleanup resources."""
        await self.close()
        return None

    async def close(self) -> None:
        """CQ-010: Clean up resources and flush pending writes."""
        if not self._initialized:
            return

        # Flush any cached state to the active store
        try:
            for name in self.CORE_FILES:
                if name in self._state_cache:
                    if self._runtime_store is not None:
                        await self._runtime_store.save_document(name, self._state_cache[name])
                        continue
                    filepath = self._core_file_path(name)
                    await self._atomic_write_json(filepath, self._state_cache[name])
            logger.debug("StateManager: flushed cached state on close")
        except Exception as e:
            logger.warning("StateManager: error flushing state on close: %s", e)

        # Clear locks and cache
        self._persistence.clear_locks()
        if self._runtime_store is not None:
            await self._runtime_store.close()
        self._state_cache.clear()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize state directories and files."""
        if self._initialized:
            return

        if self._runtime_store is None:
            await self._persistence.initialize()
            await self._ensure_initial_state()
        else:
            await self._runtime_store.open()
            await self._ensure_runtime_store_initial_state()
        self._initialized = True

    async def _ensure_initial_state(self) -> None:
        """Ensure all state files exist with defaults."""
        self._cleanup_stale_temp_files()
        await ensure_initial_state(
            state_dir=self.state_dir,
            config_dir=self.config_dir,
            state_cache=self._state_cache,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
        )
        self._state_cache.setdefault("schedule", [])
        self._state_cache.setdefault("schedule_runs", [])

    async def _ensure_runtime_store_initial_state(self) -> None:
        """Ensure all state documents exist in the native runtime store."""
        from ..config import default_mcp_config_payload, default_schedule_config_payload
        from ..utils import utcnow_isoformat

        conversation_thread_id = utcnow_isoformat()
        defaults = {
            "current_state": {
                "thread_id": conversation_thread_id,
                "cycle_count": 0,
                "actions_this_cycle": 0,
                "shutdown_requested": False,
                "last_checkpoint": None,
                "messages": [],
                "attachments": [],
                "tool_calls": [],
                "current_input": None,
                "pending_response": None,
            },
            "a2a_tasks": [],
            "acp_tasks": [],
            "async_subagent_tasks": [],
            "background_tasks": [],
            "schedule_runs": [],
            "registry": {},
            "mcp": default_mcp_config_payload(),
            "schedule": default_schedule_config_payload(),
        }
        assert self._runtime_store is not None
        for name, default in defaults.items():
            self._state_cache[name] = await self._runtime_store.get_document(name, default)

    def _cleanup_stale_temp_files(self) -> None:
        """Remove leftover .tmp files from failed atomic writes."""
        self._persistence.cleanup_stale_temp_files(self.state_dir, self.config_dir)

    def _file_lock(self, name: str):
        """Acquire async lock for a core file."""
        return self._persistence.file_lock(name, filepath=self._core_file_path(name))

    async def _atomic_write_json(self, filepath: Path, data: Any) -> None:
        """Write JSON atomically: temp file → fsync → rename.

        This ensures that either the entire write succeeds or the original
        file remains intact. No partial writes possible.
        """
        await self._persistence.write_json(filepath, data)

    async def _read_json(self, filepath: Path) -> Any:
        """Read JSON file asynchronously."""
        return await self._persistence.read_json(filepath)

    async def _get_runtime_document(self, name: str) -> Any:
        """Return a cached native-store-backed state document."""
        if name not in self._state_cache:
            assert self._runtime_store is not None
            self._state_cache[name] = await self._runtime_store.get_document(name, {})
        return self._state_cache[name]

    async def _save_runtime_document(self, name: str, payload: Any) -> None:
        """Save and cache a native-store-backed state document."""
        assert self._runtime_store is not None
        self._state_cache[name] = payload
        await self._runtime_store.save_document(name, payload)

    async def _mutate_runtime_current_state(
        self,
        mutate: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        """Serialize native-store read/modify/write updates to current_state."""
        async with self._runtime_current_state_lock:
            state = await self._get_runtime_document("current_state")
            current = dict(state) if isinstance(state, dict) else {}
            mutate(current)
            await self._save_runtime_document("current_state", current)
            return current

    async def append_jsonl(self, log_name: str, entry: dict[str, Any]) -> None:
        """Append a single entry to a JSONL log file with secret redaction."""
        if log_name not in self.LOG_FILES:
            raise ValueError(f"Unknown log: {log_name}")
        if self._runtime_store is not None:
            payload = dict(entry)
            timestamp = payload.pop("timestamp", None)
            await self._runtime_store.write_log(
                source=log_name,
                message=_structured_log_message(log_name, payload),
                payload=payload,
                timestamp=timestamp,
                relative_path=self.LOG_FILES[log_name],
            )
            return
        await log_store.append_jsonl(
            persistence=self._persistence,
            logs_dir=self.logs_dir,
            log_files=self.LOG_FILES,
            log_name=log_name,
            entry=entry,
            timestamp_factory=utcnow_isoformat,
        )

    async def log_conversation(self, message: ConversationMessage) -> None:
        """Log a conversation message."""
        if self._runtime_store is not None:
            await self.add_message(message)
            await self.append_jsonl("conversation", message.model_dump(mode="json"))
            return
        await log_store.log_conversation(message=message, append_jsonl_fn=self.append_jsonl)

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event."""
        await log_store.log_schedule_event(event=event, append_jsonl_fn=self.append_jsonl)

    # Schedule management (database-backed in production runtime)
    async def get_schedule(self) -> list[ScheduleEntry]:
        if self._runtime_store is not None:
            # Read straight from the shared store rather than the in-memory
            # cache: schedules are also created/paused/deleted by the backend
            # (UI) writing the same Postgres document, so a cached copy would
            # hide those cross-process changes from the runtime scheduler.
            payload = await self._runtime_store.get_document("schedule", [])
            self._state_cache["schedule"] = payload
            return [ScheduleEntry.model_validate(item) for item in payload]
        payload = self._state_cache.get("schedule", [])
        rows = payload if isinstance(payload, list) else []
        return [ScheduleEntry.model_validate(item) for item in rows]

    async def save_schedule(self, schedule: list[ScheduleEntry]) -> None:
        if self._runtime_store is not None:
            # Write straight through to the shared store (and refresh the cache)
            # so backend reads and the runtime stay coherent. See get_schedule.
            payload = [entry.model_dump(mode="json") for entry in schedule]
            self._state_cache["schedule"] = payload
            await self._runtime_store.save_document("schedule", payload)
            return
        self._state_cache["schedule"] = [entry.model_dump(mode="json") for entry in schedule]

    # Registry management
    async def get_registry(self) -> dict[str, Any]:
        if self._runtime_store is not None:
            registry = await self._get_runtime_document("registry")
            return dict(registry) if isinstance(registry, dict) else {}
        return await config_store.get_registry(
            file_lock=self._file_lock,
            read_json=self._read_json,
            registry_path=self._core_file_path("registry"),
        )

    async def save_registry(self, registry: dict[str, Any]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document("registry", registry)
            return
        await config_store.save_registry(
            registry=registry,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            registry_path=self._core_file_path("registry"),
        )

    # ==========================================================================
    # Current State Management (current_state.json - Progenitor agent state)
    # ==========================================================================

    async def get_current_state(self) -> dict[str, Any]:
        if self._runtime_store is not None:
            state = await self._get_runtime_document("current_state")
            return dict(state) if isinstance(state, dict) else {}
        return await session_store.get_current_state(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def save_current_state(self, state: dict[str, Any]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document("current_state", state)
            return
        await session_store.save_current_state(
            state=state,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def update_current_state(self, updates: dict[str, Any]) -> None:
        if self._runtime_store is not None:
            await self._mutate_runtime_current_state(lambda current: current.update(updates))
            return
        await session_store.update_current_state(
            updates=updates,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    # ==========================================================================
    # A2A Task Tracking (stored in a2a_tasks.json)
    # ==========================================================================

    async def get_a2a_tasks(self) -> list[A2ATaskRecord]:
        if self._runtime_store is not None:
            payload = await self._get_runtime_document("a2a_tasks")
            return [A2ATaskRecord.model_validate(item) for item in payload]
        return await a2a_store.get_a2a_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            a2a_tasks_path=self._core_file_path("a2a_tasks"),
        )

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document(
                "a2a_tasks",
                [task.model_dump(mode="json") for task in tasks],
            )
            return
        await a2a_store.save_a2a_tasks(
            tasks=tasks,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            a2a_tasks_path=self._core_file_path("a2a_tasks"),
        )

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        return await a2a_store.get_a2a_task(task_id=task_id, get_a2a_tasks_fn=self.get_a2a_tasks)

    async def upsert_a2a_task(self, task: A2ATaskRecord) -> None:
        await a2a_store.upsert_a2a_task(
            task=task,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )

    async def update_a2a_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await a2a_store.update_a2a_task(
            task_id=task_id,
            updates=updates,
            get_a2a_tasks_fn=self.get_a2a_tasks,
            save_a2a_tasks_fn=self.save_a2a_tasks,
        )

    # ==========================================================================
    # ACP Task Tracking (stored in acp_tasks.json)
    # ==========================================================================

    async def get_acp_tasks(self) -> list[ACPTaskRecord]:
        if self._runtime_store is not None:
            payload = await self._get_runtime_document("acp_tasks")
            return [ACPTaskRecord.model_validate(item) for item in payload]
        return await acp_store.get_acp_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            acp_tasks_path=self._core_file_path("acp_tasks"),
        )

    async def save_acp_tasks(self, tasks: list[ACPTaskRecord]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document(
                "acp_tasks",
                [task.model_dump(mode="json") for task in tasks],
            )
            return
        await acp_store.save_acp_tasks(
            tasks=tasks,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            acp_tasks_path=self._core_file_path("acp_tasks"),
        )

    async def get_acp_task(self, task_id: str) -> ACPTaskRecord | None:
        return await acp_store.get_acp_task(task_id=task_id, get_acp_tasks_fn=self.get_acp_tasks)

    async def upsert_acp_task(self, task: ACPTaskRecord) -> None:
        await acp_store.upsert_acp_task(
            task=task,
            get_acp_tasks_fn=self.get_acp_tasks,
            save_acp_tasks_fn=self.save_acp_tasks,
        )

    async def update_acp_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await acp_store.update_acp_task(
            task_id=task_id,
            updates=updates,
            get_acp_tasks_fn=self.get_acp_tasks,
            save_acp_tasks_fn=self.save_acp_tasks,
        )

    # ==========================================================================
    # Async Subagent Task Tracking (stored in async_subagent_tasks.json)
    # ==========================================================================

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        if self._runtime_store is not None:
            payload = await self._get_runtime_document("async_subagent_tasks")
            return [AsyncSubagentTaskRecord.model_validate(item) for item in payload]
        return await async_subagent_store.get_async_subagent_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            async_subagent_tasks_path=self._core_file_path("async_subagent_tasks"),
        )

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document(
                "async_subagent_tasks",
                [task.model_dump(mode="json") for task in tasks],
            )
            return
        await async_subagent_store.save_async_subagent_tasks(
            tasks=tasks,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            async_subagent_tasks_path=self._core_file_path("async_subagent_tasks"),
        )

    async def get_async_subagent_task(self, task_id: str) -> AsyncSubagentTaskRecord | None:
        return await async_subagent_store.get_async_subagent_task(
            task_id=task_id,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
        )

    async def upsert_async_subagent_task(self, task: AsyncSubagentTaskRecord) -> None:
        await async_subagent_store.upsert_async_subagent_task(
            task=task,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )

    async def update_async_subagent_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        return await async_subagent_store.update_async_subagent_task(
            task_id=task_id,
            updates=updates,
            get_async_subagent_tasks_fn=self.get_async_subagent_tasks,
            save_async_subagent_tasks_fn=self.save_async_subagent_tasks,
        )

    # ==========================================================================
    # Background Task Queue (stored in background_tasks.json)
    # ==========================================================================

    async def get_background_tasks(self) -> list[BackgroundTaskEntry]:
        if self._runtime_store is not None:
            payload = await self._get_runtime_document("background_tasks")
            return [BackgroundTaskEntry.model_validate(item) for item in payload]
        return await background_store.get_background_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            background_tasks_path=self._core_file_path("background_tasks"),
        )

    async def save_background_tasks(self, tasks: list[BackgroundTaskEntry]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document(
                "background_tasks",
                [task.model_dump(mode="json") for task in tasks],
            )
            return
        await background_store.save_background_tasks(
            tasks=tasks,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            background_tasks_path=self._core_file_path("background_tasks"),
        )

    async def enqueue_background_task(self, task: BackgroundTaskEntry) -> None:
        await background_store.enqueue_background_task(
            task=task,
            get_background_tasks_fn=self.get_background_tasks,
            save_background_tasks_fn=self.save_background_tasks,
        )

    async def dequeue_background_task(self) -> BackgroundTaskEntry | None:
        return await background_store.dequeue_background_task(
            get_background_tasks_fn=self.get_background_tasks,
            save_background_tasks_fn=self.save_background_tasks,
        )

    # ==========================================================================
    # Schedule Run Tracking (database-backed in production runtime)
    # ==========================================================================

    async def get_schedule_runs(self) -> list[ScheduleRunRecord]:
        if self._runtime_store is not None:
            payload = await self._get_runtime_document("schedule_runs")
            return [ScheduleRunRecord.model_validate(item) for item in payload]
        payload = self._state_cache.get("schedule_runs", [])
        rows = payload if isinstance(payload, list) else []
        return [ScheduleRunRecord.model_validate(item) for item in rows]

    async def save_schedule_runs(self, runs: list[ScheduleRunRecord]) -> None:
        if self._runtime_store is not None:
            await self._save_runtime_document(
                "schedule_runs",
                [run.model_dump(mode="json") for run in runs],
            )
            return
        self._state_cache["schedule_runs"] = [run.model_dump(mode="json") for run in runs]

    async def get_schedule_run(self, run_id: str) -> ScheduleRunRecord | None:
        for run in await self.get_schedule_runs():
            if run.run_id == run_id:
                return run
        return None

    async def upsert_schedule_run(self, run: ScheduleRunRecord) -> None:
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id == run.run_id:
                runs[index] = run
                break
        else:
            runs.append(run)
        await self.save_schedule_runs(runs)

    async def update_schedule_run(self, run_id: str, updates: dict[str, Any]) -> bool:
        runs = await self.get_schedule_runs()
        for index, existing in enumerate(runs):
            if existing.run_id != run_id:
                continue
            payload = existing.model_dump()
            payload.update(updates)
            runs[index] = ScheduleRunRecord(**payload)
            await self.save_schedule_runs(runs)
            return True
        return False

    # ==========================================================================
    # Messages Management (stored in current_state.json)
    # ==========================================================================

    async def get_messages(self) -> list[ConversationMessage]:
        if self._runtime_store is not None:
            state = await self.get_current_state()
            return [ConversationMessage.model_validate(item) for item in state.get("messages", [])]
        return await session_store.get_messages(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def save_messages(self, messages: list[ConversationMessage]) -> None:
        if self._runtime_store is not None:
            payload = [message.model_dump(mode="json") for message in messages]
            await self._mutate_runtime_current_state(
                lambda current: current.__setitem__("messages", payload)
            )
            return
        await session_store.save_messages(
            messages=messages,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_message(self, message: ConversationMessage) -> None:
        if self._runtime_store is not None:
            payload = message.model_dump(mode="json")

            def mutate(current: dict[str, Any]) -> None:
                messages = current.get("messages", [])
                if not isinstance(messages, list):
                    messages = []
                messages.append(payload)
                current["messages"] = messages

            await self._mutate_runtime_current_state(mutate)
            return
        await session_store.add_message(
            message=message,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    # ==========================================================================
    # Schedule Management - Convenience methods for the ``schedule`` document
    # (a system feature persisted in Postgres, stored separately from current_state)
    # ==========================================================================

    async def add_schedule_entry(self, entry) -> None:
        await config_store.add_schedule_entry(
            entry=entry,
            get_schedule_fn=self.get_schedule,
            save_schedule_fn=self.save_schedule,
            log_schedule_event_fn=self.log_schedule_event,
        )

    async def remove_schedule_entry(self, schedule_id: str) -> bool:
        return await config_store.remove_schedule_entry(
            schedule_id=schedule_id,
            get_schedule_fn=self.get_schedule,
            save_schedule_fn=self.save_schedule,
            log_schedule_event_fn=self.log_schedule_event,
        )

    async def update_schedule_entry(self, schedule_id: str, updates: dict) -> bool:
        return await config_store.update_schedule_entry(
            schedule_id=schedule_id,
            updates=updates,
            get_schedule_fn=self.get_schedule,
            save_schedule_fn=self.save_schedule,
            log_schedule_event_fn=self.log_schedule_event,
        )

    # ==========================================================================
    # Tool Calls Management (stored in current_state.json)
    # ==========================================================================

    async def get_tool_calls(self) -> list[dict[str, Any]]:
        if self._runtime_store is not None:
            state = await self.get_current_state()
            tool_calls = state.get("tool_calls", [])
            return list(tool_calls) if isinstance(tool_calls, list) else []
        return await session_store.get_tool_calls(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_tool_call(self, tool_call: dict[str, Any]) -> None:
        if self._runtime_store is not None:

            def mutate(current: dict[str, Any]) -> None:
                tool_calls = current.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    tool_calls = []
                tool_calls.append(tool_call)
                current["tool_calls"] = tool_calls

            await self._mutate_runtime_current_state(mutate)
            return
        await session_store.add_tool_call(
            tool_call=tool_call,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    # ==========================================================================
    # Attachments Management (stored in current_state.json)
    # ==========================================================================

    async def get_attachments(self) -> list[dict[str, Any]]:
        if self._runtime_store is not None:
            state = await self.get_current_state()
            attachments = state.get("attachments", [])
            return list(attachments) if isinstance(attachments, list) else []
        return await session_store.get_attachments(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_attachment(self, attachment: dict[str, Any]) -> None:
        if self._runtime_store is not None:

            def mutate(current: dict[str, Any]) -> None:
                attachments = current.get("attachments", [])
                if not isinstance(attachments, list):
                    attachments = []
                attachments.append(attachment)
                current["attachments"] = attachments

            await self._mutate_runtime_current_state(mutate)
            return
        await session_store.add_attachment(
            attachment=attachment,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def commit_state(self) -> None:
        """Commit all cached state to disk."""
        for name, data in self._state_cache.items():
            if name not in self.CORE_FILES:
                continue

            if self._runtime_store is not None:
                await self._runtime_store.save_document(name, data)
                continue
            async with self._file_lock(name):
                await self._atomic_write_json(self._core_file_path(name), data)

    async def read_conversation_log(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._runtime_store is not None:
            rows = await self._runtime_store.query_logs(source="conversation", limit=limit)
            history: list[dict[str, Any]] = []
            for row in rows:
                payload = row.get("payload")
                entry = dict(payload) if isinstance(payload, dict) else {"payload": payload}
                timestamp = row.get("timestamp")
                if timestamp is not None and "timestamp" not in entry:
                    entry["timestamp"] = (
                        timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
                    )
                history.append(entry)
            return history
        return await log_store.read_conversation_log(
            logs_dir=self.logs_dir,
            log_files=self.LOG_FILES,
            limit=limit,
        )
