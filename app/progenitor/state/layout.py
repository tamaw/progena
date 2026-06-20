"""State file layout and initialization helpers."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from ..config import default_mcp_config_payload
from ..utils import utcnow_isoformat

logger = logging.getLogger(__name__)

CORE_FILES: dict[str, str] = {
    "current_state": "current_state.json",
    "a2a_tasks": "a2a_tasks.json",
    "acp_tasks": "acp_tasks.json",
    "async_subagent_tasks": "async_subagent_tasks.json",
    "background_tasks": "background_tasks.json",
    "registry": "agents.json",
    "mcp": "mcp.json",
}

LOG_FILES: dict[str, str] = {
    "conversation": "conversation.jsonl",
    "schedule": "schedule.jsonl",
}

CONFIG_BACKED_CORE_FILES = {"registry", "mcp"}


def core_file_path(name: str, state_dir: Path, config_dir: Path) -> Path:
    """Resolve an absolute path for a core state/config file."""
    filename = CORE_FILES[name]
    if name in CONFIG_BACKED_CORE_FILES:
        return config_dir / filename
    return state_dir / filename


async def ensure_initial_state(
    *,
    state_dir: Path,
    config_dir: Path,
    state_cache: dict[str, Any],
    read_json: Callable[[Path], Awaitable[Any]],
    atomic_write_json: Callable[[Path, Any], Awaitable[None]],
) -> None:
    """Ensure all core state/config files exist and are cached."""
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
        "registry": {},
        "mcp": default_mcp_config_payload(),
    }

    for name, filename in CORE_FILES.items():
        filepath = core_file_path(name, state_dir, config_dir)
        if not filepath.exists():
            await atomic_write_json(filepath, defaults[name])
        cached = await read_json(filepath)
        if cached is None:
            logger.info("State file %s was empty/invalid, initializing with defaults", filename)
            await atomic_write_json(filepath, defaults[name])
            cached = defaults[name]
        state_cache[name] = cached
