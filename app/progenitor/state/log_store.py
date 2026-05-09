"""Log helpers for conversation and schedule event streams."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiofiles

from .models import ConversationMessage, redact_secrets


async def append_jsonl(
    *,
    persistence,
    logs_dir: Path,
    log_files: dict[str, str],
    log_name: str,
    entry: dict[str, Any],
    timestamp_factory,
) -> None:
    if log_name not in log_files:
        raise ValueError(f"Unknown log: {log_name}")
    filepath = logs_dir / log_files[log_name]
    if "timestamp" not in entry:
        entry["timestamp"] = timestamp_factory()
    line = redact_secrets(json.dumps(entry, default=str)) + "\n"
    await persistence.append_jsonl_line(filepath, line)


async def log_conversation(*, message: ConversationMessage, append_jsonl_fn) -> None:
    await append_jsonl_fn("conversation", message.model_dump())


async def log_schedule_event(*, event: dict[str, Any], append_jsonl_fn) -> None:
    await append_jsonl_fn("schedule", event)


async def read_conversation_log(
    *,
    logs_dir: Path,
    log_files: dict[str, str],
    limit: int = 100,
) -> list[dict[str, Any]]:
    filepath = logs_dir / log_files["conversation"]
    if not filepath.exists():
        return []
    entries = []
    async with aiofiles.open(filepath, encoding="utf-8") as handle:
        async for line in handle:
            if line.strip():
                entries.append(json.loads(line))
    return entries[-limit:] if limit else entries
