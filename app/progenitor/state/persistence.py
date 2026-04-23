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
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import aiofiles

logger = logging.getLogger(__name__)


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
        *,
        config_dir: Path | None = None,
    ) -> None:
        """Initialize persistence layer.

        Args:
            state_dir: Directory for state JSON files.
            logs_dir: Directory for JSONL log files.
            lock_timeout: Timeout for acquiring file locks.
            config_dir: Optional directory for config JSON files.
        """
        self.state_dir = Path(state_dir)
        self.logs_dir = Path(logs_dir)
        self.config_dir = Path(config_dir) if config_dir is not None else None
        self.lock_timeout = lock_timeout
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """Initialize directories."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        if self.config_dir is not None:
            self.config_dir.mkdir(parents=True, exist_ok=True)

    def clear_locks(self) -> None:
        """Forget cached asyncio locks."""
        self._locks.clear()

    def _get_lock(self, name: str) -> asyncio.Lock:
        """Get or create an async lock for a file."""
        if name not in self._locks:
            self._locks[name] = asyncio.Lock()
        return self._locks[name]

    @asynccontextmanager
    async def file_lock(self, name: str, *, filepath: Path | None = None):
        """Acquire async lock for a file operation."""
        target_path = Path(filepath) if filepath is not None else self.state_dir / name
        lock = self._get_lock(str(target_path))
        async with lock:
            lock_path = target_path.with_suffix(f"{target_path.suffix}.lock")
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

    async def read(self, filename: str) -> Any:
        """Read a JSON file asynchronously.

        Args:
            filename: Name of the file (with extension).

        Returns:
            Parsed JSON data or None if file doesn't exist.
        """
        return await self.read_json(self.state_dir / filename)

    async def read_json(self, filepath: Path) -> Any:
        """Read an arbitrary JSON file asynchronously."""
        filepath = Path(filepath)
        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, encoding="utf-8") as f:
            content = await f.read()
            if not content or not content.strip():
                return None
            try:
                return json.loads(content)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid JSON in %s: %s - treating as empty", filepath.name, exc)
                return None

    async def write(
        self,
        filename: str,
        data: Any,
    ) -> None:
        """Write a JSON file under state_dir atomically."""
        await self.write_json(self.state_dir / filename, data)

    async def write_json(self, filepath: Path, data: Any) -> None:
        """Write JSON atomically: temp file → fsync → rename.

        This ensures that either the entire write succeeds or the
        original file remains intact. No partial writes possible.

        Args:
            filepath: Absolute path to the JSON file.
            data: Data to serialize and write.
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Create temp file in same directory for atomic rename
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent,
            prefix=f".{filepath.name}.",
            suffix=".tmp",
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            max_retries = 5
            retry_delay = 0.1
            for attempt in range(max_retries):
                try:
                    os.replace(tmp_path, filepath)
                    break
                except PermissionError as exc:
                    if attempt == max_retries - 1:
                        raise
                    logger.debug(
                        "Retry %d/%d: os.replace failed for %s: %s",
                        attempt + 1,
                        max_retries,
                        filepath.name,
                        exc,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2

            logger.debug("Atomically wrote %s", filepath)

        except Exception as e:
            logger.error("Failed to write %s: %s", filepath, e)

            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

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
        line = json.dumps(entry, default=str) + "\n"
        await self.append_jsonl_line(self.logs_dir / filename, line)

    async def append_jsonl_line(self, filepath: Path, line: str) -> None:
        """Append a raw JSONL line to an arbitrary file."""
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(filepath, "a", encoding="utf-8") as f:
            await f.write(line)
            await f.flush()
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

    def cleanup_stale_temp_files(self, *directories: Path) -> None:
        """Remove leftover .tmp files from failed atomic writes."""
        try:
            for directory in directories:
                if not directory.exists():
                    continue
                for tmp_file in directory.glob(".*.tmp"):
                    try:
                        tmp_file.unlink()
                        logger.info("Cleaned up stale temp file: %s", tmp_file.name)
                    except Exception as exc:
                        logger.warning("Failed to clean up %s: %s", tmp_file.name, exc)
        except Exception as exc:
            logger.warning("Error scanning for stale temp files: %s", exc)
