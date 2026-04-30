"""
State Manager - Handles persistence and locking for Progenitor state.

Rules:
- All core state is JSON
- Append-only logs are JSONL
- File locks for core files only
- Atomic writes (temp → fsync → rename)
- State is always recoverable by replaying JSON
"""

import copy
import json
import logging
from pathlib import Path
from typing import Any, cast

import aiofiles

from ..config import default_mcp_config_payload, default_schedule_config_payload
from ..runtime.time_utils import utcnow_isoformat
from .models import (
    A2ATaskRecord,
    ConversationMessage,
    ScheduleEntry,
    redact_secrets,
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

    CORE_FILES: dict[str, str] = {
        "current_state": "current_state.json",  # Progenitor agent state
        "a2a_tasks": "a2a_tasks.json",  # Tracked async A2A task state
        "registry": "agents.json",
        "mcp": "mcp.json",
        "schedule": "schedule.json",
    }

    LOG_FILES: dict[str, str] = {
        "conversation": "conversation.jsonl",
        "schedule": "schedule.jsonl",
    }

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
        filename = self.CORE_FILES[name]
        if name in {"registry", "mcp", "schedule"}:
            return self.config_dir / filename
        return self.state_dir / filename

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
        # Clean up any stale temp files from previous failed writes
        self._cleanup_stale_temp_files()

        schedule_default = default_schedule_config_payload()
        legacy_schedule_path = self.state_dir / self.CORE_FILES["schedule"]
        schedule_path = self._core_file_path("schedule")
        if not schedule_path.exists() and legacy_schedule_path.exists():
            legacy_schedule = await self._read_json(legacy_schedule_path)
            if isinstance(legacy_schedule, list):
                logger.info(
                    "Migrating legacy schedule config from %s to %s",
                    legacy_schedule_path,
                    schedule_path,
                )
                schedule_default = legacy_schedule

        conversation_thread_id = utcnow_isoformat()

        defaults = {
            # current_state.json - Progenitor agent operational state (user session)
            "current_state": {
                "thread_id": conversation_thread_id,
                "cycle_count": 0,
                "actions_this_cycle": 0,
                "shutdown_requested": False,
                "last_checkpoint": None,
                "messages": [],  # Chat history (user ↔ progenitor)
                # NOTE: schedule is stored in data/config/schedule.json
                "attachments": [],  # Attachment path links
                "tool_calls": [],  # Session tool calls
                "current_input": None,
                "pending_response": None,
            },
            "a2a_tasks": [],
            "registry": {},
            "mcp": default_mcp_config_payload(),
            "schedule": schedule_default,
        }

        for name, filename in self.CORE_FILES.items():
            filepath = self._core_file_path(name)
            if not filepath.exists():
                await self._atomic_write_json(filepath, defaults[name])
            cached = await self._read_json(filepath)
            # If file was empty/invalid, use default and rewrite
            if cached is None:
                logger.info("State file %s was empty/invalid, initializing with defaults", filename)
                await self._atomic_write_json(filepath, defaults[name])
                cached = defaults[name]
            self._state_cache[name] = cached

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
        if log_name not in self.LOG_FILES:
            raise ValueError(f"Unknown log: {log_name}")

        filepath = self.logs_dir / self.LOG_FILES[log_name]

        # Add timestamp if not present
        if "timestamp" not in entry:
            entry["timestamp"] = utcnow_isoformat()

        # Redact secrets before writing
        line = json.dumps(entry, default=str)
        line = redact_secrets(line) + "\n"

        await self._persistence.append_jsonl_line(filepath, line)

    async def log_conversation(self, message: ConversationMessage) -> None:
        """Log a conversation message."""
        await self.append_jsonl("conversation", message.model_dump())

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event."""
        await self.append_jsonl("schedule", event)

    # Schedule management (stored in data/config/schedule.json)
    async def get_schedule(self) -> list[ScheduleEntry]:
        """Get scheduled tasks from data/config/schedule.json.

        Schedule is a system feature that persists beyond user sessions.
        It is stored separately from current_state.json.
        """
        async with self._file_lock("schedule"):
            data = await self._read_json(self._core_file_path("schedule"))
            return [ScheduleEntry(**item) for item in (data or [])]

    async def save_schedule(self, schedule: list[ScheduleEntry]) -> None:
        """Save scheduled tasks to data/config/schedule.json.

        Schedule is a system feature that persists beyond user sessions.
        It is stored separately from current_state.json.
        """
        async with self._file_lock("schedule"):
            data = [item.model_dump() for item in schedule]
            await self._atomic_write_json(self._core_file_path("schedule"), data)
            self._state_cache["schedule"] = data

    # Registry management
    async def get_registry(self) -> dict[str, Any]:
        """Get the agent/tool registry."""
        async with self._file_lock("registry"):
            return await self._read_json(self._core_file_path("registry")) or {}

    async def save_registry(self, registry: dict[str, Any]) -> None:
        """Save the registry."""
        async with self._file_lock("registry"):
            await self._atomic_write_json(self._core_file_path("registry"), registry)
            self._state_cache["registry"] = registry

    # ==========================================================================
    # Current State Management (current_state.json - Progenitor agent state)
    # ==========================================================================

    async def get_current_state(self) -> dict[str, Any]:
        """Get current progenitor agent state (chat, schedule, tool calls).

        Returns cached in-memory state when available, only falls back
        to disk when the cache is empty (e.g. first access before init).
        """
        if "current_state" in self._state_cache:
            return copy.deepcopy(cast(dict[str, Any], self._state_cache["current_state"]))
        async with self._file_lock("current_state"):
            data = await self._read_json(self.state_dir / self.CORE_FILES["current_state"]) or {}
            self._state_cache["current_state"] = data
            return copy.deepcopy(data)

    async def save_current_state(self, state: dict[str, Any]) -> None:
        """Save current progenitor agent state."""
        state_copy = copy.deepcopy(state)
        async with self._file_lock("current_state"):
            await self._atomic_write_json(
                self.state_dir / self.CORE_FILES["current_state"], state_copy
            )
            self._state_cache["current_state"] = state_copy

    async def update_current_state(self, updates: dict[str, Any]) -> None:
        """Partially update current state with given fields."""
        async with self._file_lock("current_state"):
            state = copy.deepcopy(cast(dict[str, Any], self._state_cache.get("current_state", {})))
            if not state:
                state = (
                    await self._read_json(self.state_dir / self.CORE_FILES["current_state"]) or {}
                )
            state.update(updates)
            await self._atomic_write_json(self.state_dir / self.CORE_FILES["current_state"], state)
            self._state_cache["current_state"] = state

    # ==========================================================================
    # A2A Task Tracking (stored in a2a_tasks.json)
    # ==========================================================================

    async def get_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return all persisted A2A task records."""
        if "a2a_tasks" in self._state_cache:
            data = copy.deepcopy(cast(list[dict[str, Any]], self._state_cache["a2a_tasks"]))
            return [A2ATaskRecord(**item) for item in data]
        async with self._file_lock("a2a_tasks"):
            data = await self._read_json(self.state_dir / self.CORE_FILES["a2a_tasks"]) or []
            task_rows = [item for item in data if isinstance(item, dict)]
            self._state_cache["a2a_tasks"] = task_rows
            return [A2ATaskRecord(**item) for item in copy.deepcopy(task_rows)]

    async def get_active_a2a_tasks(self) -> list[A2ATaskRecord]:
        """Return A2A tasks that still need polling or completion notification."""
        tasks = await self.get_a2a_tasks()
        return [
            task
            for task in tasks
            if not task.is_terminal
            or (task.notify_on_completion and task.notification_sent_at is None)
        ]

    async def save_a2a_tasks(self, tasks: list[A2ATaskRecord]) -> None:
        """Persist the full tracked A2A task list."""
        task_rows = [task.model_dump(mode="json", exclude_none=True) for task in tasks]
        async with self._file_lock("a2a_tasks"):
            await self._atomic_write_json(self.state_dir / self.CORE_FILES["a2a_tasks"], task_rows)
            self._state_cache["a2a_tasks"] = task_rows

    async def get_a2a_task(self, task_id: str) -> A2ATaskRecord | None:
        """Return one tracked A2A task by ID, if present."""
        for task in await self.get_a2a_tasks():
            if task.task_id == task_id:
                return task
        return None

    async def upsert_a2a_task(self, task: A2ATaskRecord) -> None:
        """Insert or replace a tracked A2A task record."""
        tasks = await self.get_a2a_tasks()
        for idx, existing in enumerate(tasks):
            if existing.task_id == task.task_id:
                tasks[idx] = task
                break
        else:
            tasks.append(task)
        await self.save_a2a_tasks(tasks)

    async def update_a2a_task(self, task_id: str, updates: dict[str, Any]) -> bool:
        """Apply a partial update to a tracked A2A task."""
        tasks = await self.get_a2a_tasks()
        for idx, existing in enumerate(tasks):
            if existing.task_id != task_id:
                continue
            payload = existing.model_dump()
            payload.update(updates)
            tasks[idx] = A2ATaskRecord(**payload)
            await self.save_a2a_tasks(tasks)
            return True
        return False

    # ==========================================================================
    # Messages Management (stored in current_state.json)
    # ==========================================================================

    async def get_messages(self) -> list[ConversationMessage]:
        """Get chat messages from current_state."""
        state = await self.get_current_state()
        messages_data = state.get("messages", [])
        return [
            ConversationMessage(**msg) if isinstance(msg, dict) else msg for msg in messages_data
        ]

    async def save_messages(self, messages: list[ConversationMessage]) -> None:
        """Save chat messages to current_state."""
        state = await self.get_current_state()
        state["messages"] = [
            msg.model_dump() if hasattr(msg, "model_dump") else msg for msg in messages
        ]
        await self.save_current_state(state)

    async def add_message(self, message: ConversationMessage) -> None:
        """Add a message to current_state."""
        messages = await self.get_messages()
        messages.append(message)
        await self.save_messages(messages)

    # ==========================================================================
    # Schedule Management - Convenience methods using data/config/schedule.json
    # (Schedule is a system feature stored separately from current_state)
    # ==========================================================================

    async def add_schedule_entry(self, entry) -> None:
        """Add a schedule entry to data/config/schedule.json.

        Args:
            entry: ScheduleEntry object or dict with schedule data.
        """
        schedule = await self.get_schedule()
        if hasattr(entry, "model_dump"):
            # It's a ScheduleEntry model
            schedule.append(entry)
        else:
            # It's a dict - convert to ScheduleEntry
            from .models import ScheduleEntry

            schedule.append(ScheduleEntry(**entry))
        await self.save_schedule(schedule)

        # Log the schedule event
        entry_dict = entry.model_dump() if hasattr(entry, "model_dump") else entry
        await self.log_schedule_event(
            {
                "event": "entry_added",
                "schedule_id": entry_dict.get("id"),
                "description": entry_dict.get("description"),
            }
        )

    async def remove_schedule_entry(self, schedule_id: str) -> bool:
        """Remove a schedule entry from data/config/schedule.json by ID.

        Args:
            schedule_id: The ID of the schedule entry to remove.

        Returns:
            True if entry was found and removed, False otherwise.
        """
        schedule = await self.get_schedule()
        original_len = len(schedule)
        schedule = [s for s in schedule if s.id != schedule_id]

        if len(schedule) < original_len:
            await self.save_schedule(schedule)
            await self.log_schedule_event(
                {
                    "event": "entry_removed",
                    "schedule_id": schedule_id,
                }
            )
            return True
        return False

    async def update_schedule_entry(self, schedule_id: str, updates: dict) -> bool:
        """Update a schedule entry in data/config/schedule.json.

        Args:
            schedule_id: The ID of the schedule entry to update.
            updates: Dict of fields to update.

        Returns:
            True if entry was found and updated, False otherwise.
        """
        schedule = await self.get_schedule()
        for i, entry in enumerate(schedule):
            if entry.id == schedule_id:
                entry_dict = entry.model_dump()
                entry_dict.update(updates)
                from .models import ScheduleEntry

                schedule[i] = ScheduleEntry(**entry_dict)
                await self.save_schedule(schedule)
                await self.log_schedule_event(
                    {
                        "event": "entry_updated",
                        "schedule_id": schedule_id,
                        "updates": list(updates.keys()),
                    }
                )
                return True
        return False

    # ==========================================================================
    # Tool Calls Management (stored in current_state.json)
    # ==========================================================================

    async def get_tool_calls(self) -> list[dict[str, Any]]:
        """Get tool calls from current_state."""
        state = await self.get_current_state()
        return cast(list[dict[str, Any]], state.get("tool_calls", []))

    async def add_tool_call(self, tool_call: dict[str, Any]) -> None:
        """Add a tool call to current_state."""
        state = await self.get_current_state()
        tool_calls = state.get("tool_calls", [])
        tool_calls.append(tool_call)
        state["tool_calls"] = tool_calls
        await self.save_current_state(state)

    # ==========================================================================
    # Attachments Management (stored in current_state.json)
    # ==========================================================================

    async def get_attachments(self) -> list[dict[str, Any]]:
        """Get attachments from current_state."""
        state = await self.get_current_state()
        return cast(list[dict[str, Any]], state.get("attachments", []))

    async def add_attachment(self, attachment: dict[str, Any]) -> None:
        """Add an attachment reference to current_state."""
        state = await self.get_current_state()
        attachments = state.get("attachments", [])
        attachments.append(attachment)
        state["attachments"] = attachments
        await self.save_current_state(state)

    async def commit_state(self) -> None:
        """Commit all cached state to disk."""
        for name, data in self._state_cache.items():
            if name not in self.CORE_FILES:
                continue

            async with self._file_lock(name):
                await self._atomic_write_json(self._core_file_path(name), data)

    async def read_conversation_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent conversation log entries asynchronously."""
        filepath = self.logs_dir / self.LOG_FILES["conversation"]
        if not filepath.exists():
            return []

        entries = []
        async with aiofiles.open(filepath) as f:
            async for line in f:
                if line.strip():
                    entries.append(json.loads(line))

        return entries[-limit:] if limit else entries
