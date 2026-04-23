"""
State Persistence - Low-level atomic file operations.

ARCH-006: Split StateManager into focused responsibilities.

This module handles ONLY:
- Atomic JSON file writes (temp → fsync → rename)
- Cross-platform file locking
- JSONL log appends
- No business logic

For business logic, see:
- backlog_manager.py: Work item management
- state_logger.py: Logging with redaction
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiofiles

logger = logging.getLogger(__name__)

PERSISTED_STATE_SCHEMA_VERSION = 1
VERSIONED_STATE_FILES = frozenset({"build_state.json", "current_state.json"})
EXPECTED_TOP_LEVEL_TYPES: dict[str, type[dict[str, Any]] | type[list[Any]]] = {
    "build_state.json": dict,
    "current_state.json": dict,
    "a2a_tasks.json": list,
    "agents.json": dict,
    "mcp.json": dict,
    "schedule.json": list,
}

SchemaMigration = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_state_dict_to_v1(data: dict[str, Any]) -> dict[str, Any]:
    """Add schema_version to legacy runtime state payloads."""
    migrated = dict(data)
    migrated["schema_version"] = PERSISTED_STATE_SCHEMA_VERSION
    return migrated


MIGRATIONS: dict[str, dict[int, SchemaMigration]] = {
    filename: {0: _migrate_state_dict_to_v1} for filename in VERSIONED_STATE_FILES
}


@dataclass(slots=True)
class JSONReadResult:
    """Result of reading a persisted JSON file."""

    data: Any | None
    needs_persist: bool = False
    broken_path: Path | None = None


def prepare_json_data_for_write(filepath: str | Path, data: Any) -> Any:
    """Normalize data before writing it to disk."""
    filename = Path(filepath).name
    if filename not in VERSIONED_STATE_FILES or not isinstance(data, dict):
        return data

    persisted = dict(data)
    persisted["schema_version"] = PERSISTED_STATE_SCHEMA_VERSION
    return persisted


def _broken_file_path(filepath: Path) -> Path:
    """Return a unique quarantine path for malformed persisted JSON."""
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    candidate = filepath.with_name(f"{filepath.name}.broken-{timestamp}")
    counter = 1
    while candidate.exists():
        candidate = filepath.with_name(f"{filepath.name}.broken-{timestamp}-{counter}")
        counter += 1
    return candidate


async def _quarantine_broken_json(filepath: Path, reason: str) -> Path | None:
    """Rotate a malformed state file out of the active path."""
    broken_path = _broken_file_path(filepath)
    try:
        await asyncio.to_thread(os.replace, filepath, broken_path)
    except OSError as exc:
        logger.error(
            "Failed to quarantine malformed %s after %s: %s",
            filepath.name,
            reason,
            exc,
        )
        return None

    logger.warning(
        "Quarantined malformed %s to %s after %s",
        filepath.name,
        broken_path.name,
        reason,
    )
    return broken_path


def _apply_migrations(filename: str, data: Any) -> tuple[Any, bool]:
    """Apply in-place schema migrations for known runtime-state payloads."""
    if filename not in MIGRATIONS or not isinstance(data, dict):
        return data, False

    version_value = data.get("schema_version", 0)
    try:
        version = int(version_value)
    except (TypeError, ValueError):
        version = 0

    if version > PERSISTED_STATE_SCHEMA_VERSION:
        logger.warning(
            "Persisted state %s uses future schema_version=%r; leaving it unchanged",
            filename,
            version_value,
        )
        return data, False

    migrated = dict(data)
    changed = False
    while version < PERSISTED_STATE_SCHEMA_VERSION:
        migration = MIGRATIONS[filename].get(version)
        if migration is None:
            logger.warning("No migration registered for %s schema_version=%d", filename, version)
            break
        migrated = migration(migrated)
        version = int(migrated.get("schema_version", version + 1))
        changed = True

    return migrated, changed


async def read_json_file(filepath: Path) -> JSONReadResult:
    """Read persisted JSON, quarantine malformed files, and migrate legacy schemas."""
    filepath = Path(filepath)
    if not filepath.exists():
        return JSONReadResult(data=None)

    async with aiofiles.open(filepath, encoding="utf-8") as f:
        content = await f.read()

    if not content or not content.strip():
        return JSONReadResult(data=None)

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        broken_path = await _quarantine_broken_json(filepath, f"json decode error: {exc}")
        return JSONReadResult(data=None, broken_path=broken_path)

    expected_type = EXPECTED_TOP_LEVEL_TYPES.get(filepath.name)
    if expected_type is not None and not isinstance(data, expected_type):
        broken_path = await _quarantine_broken_json(
            filepath,
            f"unexpected top-level {type(data).__name__}, expected {expected_type.__name__}",
        )
        return JSONReadResult(data=None, broken_path=broken_path)

    migrated, needs_persist = _apply_migrations(filepath.name, data)
    return JSONReadResult(data=migrated, needs_persist=needs_persist)


async def atomic_write_json(filepath: Path, data: Any) -> Any:
    """Write JSON atomically: temp file -> fsync -> rename."""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    persisted = prepare_json_data_for_write(filepath, data)

    fd, tmp_path = tempfile.mkstemp(dir=filepath.parent, prefix=f".{filepath.name}.", suffix=".tmp")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(persisted, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp_path, filepath)
        logger.debug("Atomically wrote %s", filepath)
        return persisted
    except Exception as exc:
        logger.error("Failed to write %s: %s", filepath, exc)
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# Cross-platform file locking
if sys.platform == "win32":
    import msvcrt

    def lock_file(f):
        """Acquire exclusive lock on Windows."""
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)

    def unlock_file(f):
        """Release lock on Windows."""
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def lock_file(f):
        """Acquire exclusive lock on Unix."""
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def unlock_file(f):
        """Release lock on Unix."""
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class StatePersistence:
    """Low-level state persistence with atomic operations.

    Provides:
    - Atomic writes (temp file → fsync → rename)
    - Async file locking
    - JSONL append operations

    This class handles ONLY file I/O - no business logic.
    """

    def __init__(
        self,
        state_dir: Path,
        logs_dir: Path,
        lock_timeout: int = 10,
    ):
        """Initialize persistence layer.

        Args:
            state_dir: Directory for state JSON files.
            logs_dir: Directory for JSONL log files.
            lock_timeout: Timeout for acquiring file locks.
        """
        self.state_dir = Path(state_dir)
        self.logs_dir = Path(logs_dir)
        self.lock_timeout = lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """Initialize directories."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def _get_lock(self, name: str) -> asyncio.Lock:
        """Get or create an async lock for a file."""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    @asynccontextmanager
    async def file_lock(self, name: str):
        """Acquire async lock for a file operation."""
        lock = self._get_lock(name)
        async with lock:
            yield

    async def read(self, filename: str) -> Any | None:
        """Read a JSON file asynchronously.

        Args:
            filename: Name of the file (with extension).

        Returns:
            Parsed JSON data or None if file doesn't exist.
        """
        filepath = self.state_dir / filename
        result = await read_json_file(filepath)
        if result.needs_persist and result.data is not None:
            return await atomic_write_json(filepath, result.data)
        return result.data

    async def write(
        self,
        filename: str,
        data: Any,
    ) -> None:
        """Write JSON atomically: temp file → fsync → rename.

        This ensures that either the entire write succeeds or the
        original file remains intact. No partial writes possible.

        Args:
            filename: Name of the file (with extension).
            data: Data to serialize and write.
        """
        filepath = self.state_dir / filename
        await atomic_write_json(filepath, data)

    async def append_jsonl(
        self,
        filename: str,
        entry: dict[str, Any],
    ) -> None:
        """Append a single entry to a JSONL log file.

        Args:
            filename: Name of the log file (with extension).
            entry: Entry to append as JSON line.
        """
        filepath = self.logs_dir / filename

        line = json.dumps(entry, default=str) + "\n"

        async with aiofiles.open(filepath, "a", encoding="utf-8") as f:
            await f.write(line)
            await f.flush()
            # fsync for durability
            os.fsync(f.fileno())

    async def read_jsonl(
        self,
        filename: str,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read entries from a JSONL log file.

        Args:
            filename: Name of the log file.
            limit: Maximum entries to return (from end of file).

        Returns:
            List of parsed entries.
        """
        filepath = self.logs_dir / filename
        if not filepath.exists():
            return []

        entries = []
        async with aiofiles.open(filepath, encoding="utf-8") as f:
            async for line in f:
                if line.strip():
                    entries.append(json.loads(line))

        if limit:
            return entries[-limit:]
        return entries

    async def read_jsonl_filtered(
        self,
        filename: str,
        filter_fn,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read filtered entries from a JSONL log file.

        Args:
            filename: Name of the log file.
            filter_fn: Function to filter entries.
            limit: Maximum entries to return.

        Returns:
            List of filtered entries.
        """
        filepath = self.logs_dir / filename
        if not filepath.exists():
            return []

        entries = []
        async with aiofiles.open(filepath, encoding="utf-8") as f:
            async for line in f:
                if line.strip():
                    entry = json.loads(line)
                    if filter_fn(entry):
                        entries.append(entry)

        if limit:
            return entries[-limit:]
        return entries

    def file_exists(self, filename: str, in_logs: bool = False) -> bool:
        """Check if a file exists.

        Args:
            filename: Name of the file.
            in_logs: If True, check in logs_dir; otherwise state_dir.

        Returns:
            True if file exists.
        """
        base_dir = self.logs_dir if in_logs else self.state_dir
        return (base_dir / filename).exists()
