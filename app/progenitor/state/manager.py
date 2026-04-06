"""
State Manager - Handles persistence and locking for Progenitor state.

Rules:
- All core state is JSON
- Append-only logs are JSONL
- File locks for core files only
- Atomic writes (temp → fsync → rename)
- State is always recoverable by replaying JSON
"""

import asyncio
import copy
import json
import logging
import os
import sys
import tempfile
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, cast

import aiofiles

from ..runtime.time_utils import utcnow_isoformat
from .models import (
    ConversationMessage,
    ScheduleEntry,
    WorkItem,
    redact_secrets,
)

logger = logging.getLogger(__name__)

# Cross-platform file locking
if sys.platform == "win32":
    import msvcrt

    def lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def unlock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class StateManager:
    """Manages persistence of Progenitor state with file locking.

    State is split into two files:
    - build_state.json: Agent build process only (inner loop, LLM messages, verification)
    - current_state.json: Progenitor agent operational state (chat, schedule, tool calls)

    Provides:
    - Atomic writes for crash safety
    - File locking for concurrent access
    - Cached state for performance
    - JSONL logging with secret redaction
    - CQ-010: Full async context manager protocol for resource handling

    Usage:
        async with StateManager(state_dir, logs_dir) as manager:
            backlog = await manager.get_backlog()
            # ... work with state ...
        # Resources automatically cleaned up
    """

    CORE_FILES: dict[str, str] = {
        "build_state": "build_state.json",  # Agent builds only
        "current_state": "current_state.json",  # Progenitor agent state
        "registry": "agents.json",
        "schedule": "schedule.json",
    }

    LOG_FILES: dict[str, str] = {
        "conversation": "conversation.jsonl",
        "decisions": "decisions.jsonl",
        "copilot_calls": "copilot_calls.jsonl",
        "validation": "validation.jsonl",
        "schedule": "schedule.jsonl",
    }

    def __init__(self, state_dir: Path, logs_dir: Path, lock_timeout: int = 10) -> None:
        self.state_dir = Path(state_dir)
        self.config_dir = self.state_dir.parent / "config"
        self.logs_dir = Path(logs_dir)
        self.lock_timeout = lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._state_cache: dict[str, Any] = {}
        self._initialized = False

    def _core_file_path(self, name: str) -> Path:
        """Resolve absolute path for a core state/config file."""
        filename = self.CORE_FILES[name]
        if name == "registry":
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
        self._locks.clear()
        self._state_cache.clear()
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize state directories and files."""
        if self._initialized:
            return

        # Create directories
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

        # Initialize locks
        for name in self.CORE_FILES:
            self._locks[name] = asyncio.Lock()

        # Load or create initial state
        await self._ensure_initial_state()
        self._initialized = True

    async def _ensure_initial_state(self) -> None:
        """Ensure all state files exist with defaults."""
        # Clean up any stale temp files from previous failed writes
        self._cleanup_stale_temp_files()

        defaults = {
            # build_state.json - Agent builds ONLY (inner loop)
            # Clean nested structure with phase objects
            "build_state": {
                # Build control (top-level for quick access)
                "build_in_progress": False,
                "build_started_at": None,
                "build_phase": None,
                "task_id": None,
                # Nested objects for organization
                "task": {
                    "description": None,
                    "target_capabilities": [],
                },
                "iteration": {
                    "current": 0,
                    "max": 20,
                    "start_time": None,
                },
                "spec": {
                    "file_path": None,
                    "revision": 0,
                    "created_at": None,
                    "updated_at": None,
                },
                "phases": {
                    "observe": {
                        "ideal_state": None,
                    },
                    "think": {
                        "approaches": [],
                        "recommended_id": None,
                        "gaps_analyzed": [],
                    },
                    "plan": {
                        "experiment_plan": None,
                    },
                    "execute": {
                        "completed": False,
                        "result": None,
                    },
                    "verify": {
                        "passed": False,
                        "score": 0.0,
                        "target_score": 0.8,
                        "gaps": [],
                    },
                },
                "history": {
                    "artefacts": [],
                    "verification_reports": [],
                    "lessons_learned": [],
                    "messages": [],
                },
                "best": {
                    "iteration": None,
                    "score": 0.0,
                    "passed": False,
                },
                "clarification": {
                    "needed": False,
                    "question": None,
                    "context": None,
                    "awaiting": False,
                    "response": None,
                },
                # Complex objects at top level
                "should_iterate": True,
                "iteration_stop_reason": None,
                # Backlog stays at top level
                "backlog": [],
                # Build settings
                "build_settings": None,
            },
            # current_state.json - Progenitor agent operational state (user session)
            "current_state": {
                "run_id": utcnow_isoformat(),
                "cycle_count": 0,
                "actions_this_cycle": 0,
                "shutdown_requested": False,
                "last_checkpoint": None,
                "messages": [],  # Chat history (user ↔ progenitor)
                # NOTE: schedule is stored in schedule.json (system feature, not user session)
                "attachments": [],  # Attachment path links
                "tool_calls": [],  # Session tool calls
                "current_input": None,
                "pending_response": None,
                "current_decision": None,
                "current_build_request": None,  # Reference to active build (id only)
            },
            "registry": {},
            "schedule": [],
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
        try:
            for directory in (self.state_dir, self.config_dir):
                if not directory.exists():
                    continue
                for tmp_file in directory.glob(".*.tmp"):
                    try:
                        tmp_file.unlink()
                        logger.info("Cleaned up stale temp file: %s", tmp_file.name)
                    except Exception as e:
                        logger.warning("Failed to clean up %s: %s", tmp_file.name, e)
        except Exception as e:
            logger.warning("Error scanning for stale temp files: %s", e)

    @asynccontextmanager
    async def _file_lock(self, name: str):
        """Acquire async lock for a core file."""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()

        async with self._locks[name]:
            lock_path = self._core_file_path(name).with_suffix(
                f"{self._core_file_path(name).suffix}.lock"
            )
            lock_path.parent.mkdir(parents=True, exist_ok=True)

            with open(lock_path, "a+b") as lock_handle:
                if lock_handle.tell() == 0 and lock_handle.seek(0, os.SEEK_END) == 0:
                    lock_handle.write(b"0")
                    lock_handle.flush()
                lock_handle.seek(0)

                deadline = time.monotonic() + self.lock_timeout
                while True:
                    try:
                        await asyncio.to_thread(lock_file, lock_handle)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out acquiring file lock for {name}")
                        await asyncio.sleep(0.05)

                try:
                    yield
                finally:
                    with suppress(OSError):
                        await asyncio.to_thread(unlock_file, lock_handle)

    async def _atomic_write_json(self, filepath: Path, data: Any) -> None:
        """Write JSON atomically: temp file → fsync → rename.

        This ensures that either the entire write succeeds or the original
        file remains intact. No partial writes possible.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent, prefix=f".{filepath.name}.", suffix=".tmp"
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename with retry for Windows file locking issues
            # On Windows, os.replace can fail if target is held open by another process
            max_retries = 5
            retry_delay = 0.1
            for attempt in range(max_retries):
                try:
                    os.replace(tmp_path, filepath)
                    break
                except PermissionError as pe:
                    if attempt < max_retries - 1:
                        logger.debug(
                            "Retry %d/%d: os.replace failed for %s: %s",
                            attempt + 1,
                            max_retries,
                            filepath.name,
                            pe,
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        raise
            logger.debug("Atomically wrote %s", filepath)

        except Exception as e:
            logger.error("Failed to write %s: %s", filepath, e)
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            raise

    async def _read_json(self, filepath: Path) -> Any:
        """Read JSON file asynchronously."""
        filepath = Path(filepath)
        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, encoding="utf-8") as f:
            content = await f.read()
            # Handle empty files gracefully
            if not content or not content.strip():
                return None
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning("Invalid JSON in %s: %s - treating as empty", filepath.name, e)
                return None

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

        async with aiofiles.open(filepath, "a", encoding="utf-8") as f:
            await f.write(line)
            await f.flush()
            # fsync for durability - use os.fsync on the file descriptor
            os.fsync(f.fileno())

    async def _append_jsonl(self, filepath: Path, entry: dict[str, Any]) -> None:
        """Append a single entry to an arbitrary JSONL file with secret redaction."""
        # Add timestamp if not present
        if "timestamp" not in entry:
            entry["timestamp"] = utcnow_isoformat()

        # Redact secrets before writing
        line = json.dumps(entry, default=str)
        line = redact_secrets(line) + "\n"

        async with aiofiles.open(filepath, "a", encoding="utf-8") as f:
            await f.write(line)
            await f.flush()
            os.fsync(f.fileno())

    async def log_conversation(self, message: ConversationMessage) -> None:
        """Log a conversation message."""
        await self.append_jsonl("conversation", message.model_dump())

    async def log_decision(self, decision: dict[str, Any]) -> None:
        """Log a decision."""
        await self.append_jsonl("decisions", decision)

    async def log_copilot_call(self, call_data: dict[str, Any]) -> None:
        """Log a Copilot CLI call."""
        await self.append_jsonl("copilot_calls", call_data)

    async def log_validation(self, result: dict[str, Any]) -> None:
        """Log a validation result."""
        await self.append_jsonl("validation", result)

    async def log_schedule_event(self, event: dict[str, Any]) -> None:
        """Log a schedule event."""
        await self.append_jsonl("schedule", event)

    # Backlog management (embedded in build_state.json)
    async def get_backlog(self) -> list[WorkItem]:
        """Get the current backlog from build_state."""
        state = await self.get_build_state()
        backlog_data = state.get("backlog", [])
        return [WorkItem(**item) for item in backlog_data]

    async def save_backlog(self, backlog: list[WorkItem]) -> None:
        """Save the backlog to build_state."""
        state = await self.get_build_state()
        state["backlog"] = [item.model_dump() for item in backlog]
        await self.save_build_state(state)

    async def add_work_item(self, item: WorkItem) -> None:
        """Add a work item to the backlog."""
        backlog = await self.get_backlog()
        backlog.append(item)
        await self.save_backlog(backlog)

    async def update_work_item(self, item: WorkItem) -> None:
        """Update a work item in the backlog."""
        backlog = await self.get_backlog()
        for i, existing in enumerate(backlog):
            if existing.id == item.id:
                backlog[i] = item
                break
        await self.save_backlog(backlog)

    # Schedule management (stored in schedule.json - system feature, not user session)
    async def get_schedule(self) -> list[ScheduleEntry]:
        """Get scheduled tasks from schedule.json.

        Schedule is a system feature that persists beyond user sessions.
        It is stored separately from current_state.json.
        """
        async with self._file_lock("schedule"):
            data = await self._read_json(self.state_dir / self.CORE_FILES["schedule"])
            return [ScheduleEntry(**item) for item in (data or [])]

    async def save_schedule(self, schedule: list[ScheduleEntry]) -> None:
        """Save scheduled tasks to schedule.json.

        Schedule is a system feature that persists beyond user sessions.
        It is stored separately from current_state.json.
        """
        async with self._file_lock("schedule"):
            data = [item.model_dump() for item in schedule]
            await self._atomic_write_json(self.state_dir / self.CORE_FILES["schedule"], data)
            self._state_cache["schedule"] = data

    # SpecDoc management (persistent spec artifact in build_state.json)
    async def get_spec_doc(self) -> dict[str, Any] | None:
        """Get the current spec_doc from build_state, if it exists."""
        state = await self.get_build_state()
        return cast(dict[str, Any] | None, state.get("spec_doc"))

    async def save_spec_doc(self, spec_doc) -> None:
        """Save spec_doc to build_state.json (backward compatible - only adds key)."""
        state = await self.get_build_state()
        if hasattr(spec_doc, "model_dump"):
            state["spec_doc"] = spec_doc.model_dump()
        else:
            state["spec_doc"] = spec_doc
        await self.save_build_state(state)

    async def clear_spec_doc(self) -> None:
        """Clear the spec_doc from build_state (for task completion/reset)."""
        state = await self.get_build_state()
        if "spec_doc" in state:
            del state["spec_doc"]
            await self.save_build_state(state)

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
    # Build State Management (build_state.json - Agent builds ONLY)
    # ==========================================================================

    async def get_build_state(self) -> dict[str, Any]:
        """Get build state (agent builds only).

        Returns cached in-memory state when available, only falls back
        to disk when the cache is empty (e.g. first access before init).
        """
        if "build_state" in self._state_cache:
            return cast(dict[str, Any], self._state_cache["build_state"])
        async with self._file_lock("build_state"):
            data = await self._read_json(self.state_dir / self.CORE_FILES["build_state"]) or {}
            self._state_cache["build_state"] = data
            return data

    async def save_build_state(self, state: dict[str, Any]) -> None:
        """Save build state (agent builds only)."""
        # Ensure nested objects exist
        state = self._ensure_nested_structure(state)
        async with self._file_lock("build_state"):
            await self._atomic_write_json(self.state_dir / self.CORE_FILES["build_state"], state)
            self._state_cache["build_state"] = state

    def _ensure_nested_structure(self, state: dict[str, Any]) -> dict[str, Any]:
        """Ensure all nested objects exist in build state."""
        if "task" not in state:
            state["task"] = {"description": None, "target_capabilities": []}
        if "iteration" not in state:
            state["iteration"] = {"current": 0, "max": 20, "start_time": None}
        if "spec" not in state:
            state["spec"] = {
                "file_path": None,
                "revision": 0,
                "created_at": None,
                "updated_at": None,
            }
        if "phases" not in state:
            state["phases"] = {}
        for phase in ["think", "plan", "execute", "verify"]:
            if phase not in state["phases"]:
                state["phases"][phase] = {}
        if "history" not in state:
            state["history"] = {
                "artefacts": [],
                "verification_reports": [],
                "lessons_learned": [],
                "messages": [],
            }
        if "best" not in state:
            state["best"] = {"iteration": None, "score": 0.0, "passed": False}
        if "clarification" not in state:
            state["clarification"] = {
                "needed": False,
                "question": None,
                "context": None,
                "awaiting": False,
                "response": None,
            }
        return state

    async def update_build_state(self, updates: dict[str, Any]) -> None:
        """Partially update build state with given fields.

        Uses deep merge for nested dicts (phases, history, best, etc.)
        to preserve existing values while updating specific keys.
        """
        state = await self.get_build_state()
        self._deep_merge(state, updates)
        await self.save_build_state(state)

    def _deep_merge(self, base: dict, updates: dict) -> dict:
        """Deep merge updates into base dictionary.

        Recursively merges nested dicts instead of replacing them.
        For non-dict values, updates override base.
        """
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base

    async def add_build_message(self, message: dict[str, Any]) -> None:
        """Add a message to history.messages in build_state.

        Used during agent builds to track LLM communications.
        """
        state = await self.get_build_state()
        if "history" not in state:
            state["history"] = {
                "artefacts": [],
                "verification_reports": [],
                "lessons_learned": [],
                "messages": [],
            }
        if "messages" not in state["history"]:
            state["history"]["messages"] = []
        state["history"]["messages"].append(message)
        await self.save_build_state(state)

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
    # Schedule Management - Convenience methods using schedule.json
    # (Schedule is a system feature stored in schedule.json, not current_state)
    # ==========================================================================

    async def add_schedule_entry(self, entry) -> None:
        """Add a schedule entry to schedule.json.

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
        """Remove a schedule entry from schedule.json by ID.

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
        """Update a schedule entry in schedule.json.

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
        """Commit all cached state to disk.

        IMPORTANT: Skip build_state if it's in suspended state, as the suspend
        handler already did a safe read-modify-write. The cache may not have
        all fields loaded from the in-memory loop state.
        """
        for name, data in self._state_cache.items():
            if name not in self.CORE_FILES:
                continue

            # CRITICAL: Skip build_state if suspended - the suspend handler
            # already saved it correctly with a full read-modify-write pattern.
            # The cache may be stale/incomplete.
            if name == "build_state" and data and data.get("build_phase") == "suspended":
                logger.debug(
                    "commit_state: Skipping build_state (already saved by suspend handler)"
                )
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

    # Iteration and Verification Tracking (loop.spec.md)

    async def log_iteration_start(self, iteration: int, ideal_state: Any) -> None:
        """Log the start of an inner loop iteration."""
        from ..runtime.time_utils import utcnow_isoformat

        ideal_state_dict = (
            ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state
        )

        entry = {
            "event": "iteration_start",
            "iteration": iteration,
            "timestamp": utcnow_isoformat(),
            "ideal_state_version": ideal_state_dict.get("version", 1)
            if isinstance(ideal_state_dict, dict)
            else 1,
            "criteria_count": len(ideal_state_dict.get("success_criteria", []))
            if isinstance(ideal_state_dict, dict)
            else 0,
        }
        await self.log_decision(entry)

    async def log_iteration_end(
        self, iteration: int, verification_report: Any, stop_reason: str | None = None
    ) -> None:
        """Log the end of an inner loop iteration."""
        from ..runtime.time_utils import utcnow_isoformat

        report_dict = (
            verification_report.model_dump()
            if hasattr(verification_report, "model_dump")
            else verification_report
        )

        entry = {
            "event": "iteration_end",
            "iteration": iteration,
            "timestamp": utcnow_isoformat(),
            "verification_score": report_dict.get("overall_score", 0.0)
            if isinstance(report_dict, dict)
            else 0.0,
            "passed": report_dict.get("passed", False) if isinstance(report_dict, dict) else False,
            "gaps_count": len(report_dict.get("gaps", [])) if isinstance(report_dict, dict) else 0,
            "stop_reason": stop_reason,
        }
        await self.log_decision(entry)

    async def get_iteration_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent iteration history asynchronously."""
        decisions_path = self.logs_dir / self.LOG_FILES["decisions"]
        if not decisions_path.exists():
            return []

        iterations = []
        async with aiofiles.open(decisions_path) as f:
            async for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("event") in ("iteration_start", "iteration_end", "verification"):
                        iterations.append(entry)

        return iterations[-limit:] if limit else iterations

    async def save_verification_report(self, iteration: int, report: Any) -> None:
        """Save verification report for an iteration."""
        report_dict = report.model_dump() if hasattr(report, "model_dump") else report

        entry = {
            "event": "verification_report",
            "iteration": iteration,
            "timestamp": utcnow_isoformat(),
            "report": report_dict,
        }

        # Log to validation file for detailed records
        filepath = self.logs_dir / self.LOG_FILES["validation"]
        await self._append_jsonl(filepath, entry)
