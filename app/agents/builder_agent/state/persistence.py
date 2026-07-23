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
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

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

    async def read(self, filename: str) -> dict[str, Any] | None:
        """Read a JSON file asynchronously.

        Args:
            filename: Name of the file (with extension).

        Returns:
            Parsed JSON data or None if file doesn't exist.
        """
        filepath = self.state_dir / filename
        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, encoding="utf-8") as f:
            content = await f.read()
            return cast(dict[str, Any], json.loads(content))

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

        metrics = None
        span = None

        # Create temp file in same directory for atomic rename
        fd, tmp_path = tempfile.mkstemp(
            dir=filepath.parent, prefix=f".{filepath.name}.", suffix=".tmp"
        )

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(tmp_path, filepath)

            logger.debug("Atomically wrote %s", filepath)

        except Exception as e:
            logger.error("Failed to write %s: %s", filepath, e)

            # Clean up temp file
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

            if metrics:
                metrics.increment("state_write_total", {"file": filename, "success": "false"})

            if span:
                span.record_exception(e)
                span.end()

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
