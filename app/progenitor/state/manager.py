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
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from contextlib import asynccontextmanager

import aiofiles

logger = logging.getLogger(__name__)


def _get_telemetry():
    """Get telemetry components if available."""
    try:
        from ..telemetry import get_tracer, get_metrics
        return get_tracer(), get_metrics()
    except ImportError:
        return None, None

# Cross-platform file locking
if sys.platform == 'win32':
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

from .models import (
    ProgenitorState,
    WorkItem,
    ScheduleEntry,
    AgentRegistryEntry,
    ConversationMessage,
    redact_secrets,
)
from ..runtime.time_utils import utcnow_isoformat


class StateManager:
    """Manages persistence of Progenitor state with file locking.

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
        "build_state": "build_state.json",
        "registry": "registry.json",
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
        self.logs_dir = Path(logs_dir)
        self.lock_timeout = lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}
        self._state_cache: dict[str, Any] = {}
        self._initialized = False

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
                    filepath = self.state_dir / self.CORE_FILES[name]
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
            "build_state": {
                "cycle_count": 0,
                "actions_this_cycle": 0,
                "shutdown_requested": False,
                "run_id": utcnow_isoformat(),
                "backlog": [],
                "messages": [],
                "schedule": [],
                # Build tracking (inner loop)
                "build_in_progress": False,
                "build_started_at": None,
                "build_task_description": None,
                "build_messages": [],
                "build_phase": None,
            },
            "registry": {"agents": [], "tools": []},
            "schedule": [],
        }

        for name, filename in self.CORE_FILES.items():
            filepath = self.state_dir / filename
            if not filepath.exists():
                await self._atomic_write_json(filepath, defaults[name])
            self._state_cache[name] = await self._read_json(filepath)

    def _cleanup_stale_temp_files(self) -> None:
        """Remove leftover .tmp files from failed atomic writes."""
        try:
            for tmp_file in self.state_dir.glob(".*.tmp"):
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
            yield

    async def _atomic_write_json(self, filepath: Path, data: Any) -> None:
        """Write JSON atomically: temp file → fsync → rename.

        This ensures that either the entire write succeeds or the original
        file remains intact. No partial writes possible.
        """
        filepath = Path(filepath)
        start_time = time.perf_counter()
        tracer, metrics = _get_telemetry()
        span = None

        if tracer:
            span = tracer.start_span(
                "state_write",
                attributes={
                    "file": filepath.name,
                    "operation": "atomic_write",
                },
            )

        # Write to temp file in same directory
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent,
            prefix=f".{filepath.name}.",
            suffix=".tmp"
        )

        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
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
                            attempt + 1, max_retries, filepath.name, pe
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                    else:
                        raise
            logger.debug("Atomically wrote %s", filepath)

            # Record success metrics
            duration = time.perf_counter() - start_time
            if metrics:
                metrics.record_histogram("state_write_duration_seconds", duration)
                metrics.increment("state_write_total", {"file": filepath.name, "success": "true"})

            if span:
                span.set_attribute("success", True)
                span.set_attribute("duration_seconds", duration)
                span.end()

        except Exception as e:
            logger.error("Failed to write %s: %s", filepath, e)
            # Clean up temp file on error
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            if metrics:
                metrics.increment("state_write_total", {"file": filepath.name, "success": "false"})

            if span:
                span.record_exception(e)
                span.end()
            raise

    async def _read_json(self, filepath: Path) -> Any:
        """Read JSON file asynchronously."""
        filepath = Path(filepath)
        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)

    async def append_jsonl(self, log_name: str, entry: dict[str, Any]) -> None:
        """Append a single entry to a JSONL log file with secret redaction."""
        if log_name not in self.LOG_FILES:
            raise ValueError(f"Unknown log: {log_name}")

        filepath = self.logs_dir / self.LOG_FILES[log_name]
        tracer, metrics = _get_telemetry()

        # Add timestamp if not present
        if "timestamp" not in entry:
            entry["timestamp"] = utcnow_isoformat()

        # Redact secrets before writing
        line = json.dumps(entry, default=str)
        line = redact_secrets(line) + "\n"

        async with aiofiles.open(filepath, 'a', encoding='utf-8') as f:
            await f.write(line)
            await f.flush()
            # fsync for durability - use os.fsync on the file descriptor
            os.fsync(f.fileno())

        # Record log append metrics
        if metrics:
            metrics.increment("log_append_total", {"log": log_name})
            metrics.record_histogram("log_entry_size_bytes", len(line))

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
        state = await self.get_state()
        backlog_data = state.get("backlog", [])
        return [WorkItem(**item) for item in backlog_data]

    async def save_backlog(self, backlog: list[WorkItem]) -> None:
        """Save the backlog to build_state."""
        state = await self.get_state()
        state["backlog"] = [item.model_dump() for item in backlog]
        await self.save_state(state)

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

    # Schedule management
    async def get_schedule(self) -> list[ScheduleEntry]:
        """Get scheduled tasks."""
        async with self._file_lock("schedule"):
            data = await self._read_json(self.state_dir / self.CORE_FILES["schedule"])
            return [ScheduleEntry(**item) for item in (data or [])]

    async def save_schedule(self, schedule: list[ScheduleEntry]) -> None:
        """Save scheduled tasks."""
        async with self._file_lock("schedule"):
            data = [item.model_dump() for item in schedule]
            await self._atomic_write_json(
                self.state_dir / self.CORE_FILES["schedule"],
                data
            )
            self._state_cache["schedule"] = data

    # SpecDoc management (persistent spec artifact in build_state.json)
    async def get_spec_doc(self) -> dict[str, Any] | None:
        """Get the current spec_doc from build_state, if it exists."""
        state = await self.get_state()
        return state.get("spec_doc")

    async def save_spec_doc(self, spec_doc) -> None:
        """Save spec_doc to build_state.json (backward compatible - only adds key)."""
        state = await self.get_state()
        if hasattr(spec_doc, "model_dump"):
            state["spec_doc"] = spec_doc.model_dump()
        else:
            state["spec_doc"] = spec_doc
        await self.save_state(state)

    async def clear_spec_doc(self) -> None:
        """Clear the spec_doc from build_state (for task completion/reset)."""
        state = await self.get_state()
        if "spec_doc" in state:
            del state["spec_doc"]
            await self.save_state(state)

    # Registry management
    async def get_registry(self) -> dict[str, Any]:
        """Get the agent/tool registry."""
        async with self._file_lock("registry"):
            return await self._read_json(
                self.state_dir / self.CORE_FILES["registry"]
            ) or {"agents": [], "tools": []}

    async def save_registry(self, registry: dict[str, Any]) -> None:
        """Save the registry."""
        async with self._file_lock("registry"):
            await self._atomic_write_json(
                self.state_dir / self.CORE_FILES["registry"],
                registry
            )
            self._state_cache["registry"] = registry

    # Core state management
    async def get_state(self) -> dict[str, Any]:
        """Get build state (formerly current_state)."""
        async with self._file_lock("build_state"):
            return await self._read_json(
                self.state_dir / self.CORE_FILES["build_state"]
            ) or {}

    async def save_state(self, state: dict[str, Any]) -> None:
        """Save build state (formerly current_state)."""
        async with self._file_lock("build_state"):
            await self._atomic_write_json(
                self.state_dir / self.CORE_FILES["build_state"],
                state
            )
            self._state_cache["build_state"] = state

    async def commit_state(self) -> None:
        """Commit all cached state to disk."""
        for name, data in self._state_cache.items():
            if name in self.CORE_FILES:
                async with self._file_lock(name):
                    await self._atomic_write_json(
                        self.state_dir / self.CORE_FILES[name],
                        data
                    )

    async def read_conversation_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent conversation log entries asynchronously."""
        filepath = self.logs_dir / self.LOG_FILES["conversation"]
        if not filepath.exists():
            return []

        entries = []
        async with aiofiles.open(filepath, 'r') as f:
            async for line in f:
                if line.strip():
                    entries.append(json.loads(line))

        return entries[-limit:] if limit else entries

    # Iteration and Verification Tracking (loop.spec.md)

    async def log_iteration_start(self, iteration: int, ideal_state: Any) -> None:
        """Log the start of an inner loop iteration."""
        from ..runtime.time_utils import utcnow_isoformat

        ideal_state_dict = ideal_state.model_dump() if hasattr(ideal_state, "model_dump") else ideal_state

        entry = {
            "event": "iteration_start",
            "iteration": iteration,
            "timestamp": utcnow_isoformat(),
            "ideal_state_version": ideal_state_dict.get("version", 1) if isinstance(ideal_state_dict, dict) else 1,
            "criteria_count": len(ideal_state_dict.get("success_criteria", [])) if isinstance(ideal_state_dict, dict) else 0,
        }
        await self.log_decision(entry)

    async def log_iteration_end(
        self,
        iteration: int,
        verification_report: Any,
        stop_reason: Optional[str] = None
    ) -> None:
        """Log the end of an inner loop iteration."""
        from ..runtime.time_utils import utcnow_isoformat

        report_dict = verification_report.model_dump() if hasattr(verification_report, "model_dump") else verification_report

        entry = {
            "event": "iteration_end",
            "iteration": iteration,
            "timestamp": utcnow_isoformat(),
            "verification_score": report_dict.get("overall_score", 0.0) if isinstance(report_dict, dict) else 0.0,
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
        async with aiofiles.open(decisions_path, 'r') as f:
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
