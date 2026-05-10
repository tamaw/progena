"""High-level state manager façade for Progenitor persistence."""

import logging
from pathlib import Path
from typing import Any

from ..utils import utcnow_isoformat
from . import (
    a2a_store,
    async_subagent_store,
    background_store,
    config_store,
    log_store,
    session_store,
)
from .layout import CORE_FILES, LOG_FILES, core_file_path, ensure_initial_state
from .models import (
    A2ATaskRecord,
    AsyncSubagentTaskRecord,
    BackgroundTaskEntry,
    ConversationMessage,
    ScheduleEntry,
)
from .persistence import StatePersistence

logger = logging.getLogger(__name__)


class StateManager:
    """Manages persistence of Progenitor state with file locking.

    Core runtime data is split across state and config files:
    - current_state.json: Progenitor agent operational state (chat, tool calls)
    - schedule.json: Scheduled tasks stored in data/config

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

    def __init__(self, state_dir: Path, logs_dir: Path, lock_timeout: int = 10) -> None:
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

        # Flush any cached state to disk
        try:
            for name in self.CORE_FILES:
                if name in self._state_cache:
                    filepath = self._core_file_path(name)
                    await self._atomic_write_json(filepath, self._state_cache[name])
            logger.debug("StateManager: flushed cached state on close")
        except Exception as e:
            logger.warning("StateManager: error flushing state on close: %s", e)

        # Clear locks and cache
        self._persistence.clear_locks()
        self._state_cache.clear()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize state directories and files."""
        if self._initialized:
            return

        await self._persistence.initialize()

        # Load or create initial state
        await self._ensure_initial_state()
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

    async def append_jsonl(self, log_name: str, entry: dict[str, Any]) -> None:
        """Append a single entry to a JSONL log file with secret redaction."""
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
        await log_store.log_conversation(message=message, append_jsonl_fn=self.append_jsonl)

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event."""
        await log_store.log_schedule_event(event=event, append_jsonl_fn=self.append_jsonl)

    # Schedule management (stored in data/config/schedule.json)
    async def get_schedule(self) -> list[ScheduleEntry]:
        return await config_store.get_schedule(
            file_lock=self._file_lock,
            read_json=self._read_json,
            schedule_path=self._core_file_path("schedule"),
        )

    async def save_schedule(self, schedule: list[ScheduleEntry]) -> None:
        await config_store.save_schedule(
            schedule=schedule,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            schedule_path=self._core_file_path("schedule"),
        )

    # Registry management
    async def get_registry(self) -> dict[str, Any]:
        return await config_store.get_registry(
            file_lock=self._file_lock,
            read_json=self._read_json,
            registry_path=self._core_file_path("registry"),
        )

    async def save_registry(self, registry: dict[str, Any]) -> None:
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
        return await session_store.get_current_state(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def save_current_state(self, state: dict[str, Any]) -> None:
        await session_store.save_current_state(
            state=state,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def update_current_state(self, updates: dict[str, Any]) -> None:
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
        return await a2a_store.get_a2a_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            a2a_tasks_path=self._core_file_path("a2a_tasks"),
        )

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
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
    # Async Subagent Task Tracking (stored in async_subagent_tasks.json)
    # ==========================================================================

    async def get_async_subagent_tasks(self) -> list[AsyncSubagentTaskRecord]:
        return await async_subagent_store.get_async_subagent_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            async_subagent_tasks_path=self._core_file_path("async_subagent_tasks"),
        )

    async def save_async_subagent_tasks(self, tasks: list[AsyncSubagentTaskRecord]) -> None:
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
        return await background_store.get_background_tasks(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            background_tasks_path=self._core_file_path("background_tasks"),
        )

    async def save_background_tasks(self, tasks: list[BackgroundTaskEntry]) -> None:
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
    # Messages Management (stored in current_state.json)
    # ==========================================================================

    async def get_messages(self) -> list[ConversationMessage]:
        return await session_store.get_messages(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def save_messages(self, messages: list[ConversationMessage]) -> None:
        await session_store.save_messages(
            messages=messages,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_message(self, message: ConversationMessage) -> None:
        await session_store.add_message(
            message=message,
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            atomic_write_json=self._atomic_write_json,
            current_state_path=self._core_file_path("current_state"),
        )

    # ==========================================================================
    # Schedule Management - Convenience methods using data/config/schedule.json
    # (Schedule is a system feature stored separately from current_state)
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
        return await session_store.get_tool_calls(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_tool_call(self, tool_call: dict[str, Any]) -> None:
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
        return await session_store.get_attachments(
            state_cache=self._state_cache,
            file_lock=self._file_lock,
            read_json=self._read_json,
            current_state_path=self._core_file_path("current_state"),
        )

    async def add_attachment(self, attachment: dict[str, Any]) -> None:
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

            async with self._file_lock(name):
                await self._atomic_write_json(self._core_file_path(name), data)

    async def read_conversation_log(self, limit: int = 100) -> list[dict[str, Any]]:
        return await log_store.read_conversation_log(
            logs_dir=self.logs_dir,
            log_files=self.LOG_FILES,
            limit=limit,
        )
