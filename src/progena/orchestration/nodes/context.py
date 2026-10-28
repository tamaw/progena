"""Runtime context passed to heartbeat graph nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RoutineRunner = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class HeartbeatContext:
    queues: Any
    agent_graph: Any
    live_state: dict[str, Any]
    state_manager: Any
    startup_audit_payload: dict[str, Any] | None
    tool_registry: Any
    project_root: Path | None
    llm_client: Any | None
    idle_backoff_seconds: float
    scheduler_enabled: bool
    scheduler_poll_seconds: float
    a2a_poll_seconds: float
    async_subagent_poll_seconds: float
    mcp_refresh_seconds: float
    plugin_update_check_seconds: float
    registry_refresh_seconds: float
    repo_scan_seconds: float
    worktree_dir: Path | None
    heartbeat_health_seconds: float
    timezone: str
    reasoning_enabled: bool
    input_timeout: float = 1.0
    due_maintenance: set[str] = field(default_factory=set)
    startup_flags: set[str] = field(default_factory=set)
    llm_model: str = ""
    llm_reasoning_effort: str = ""
    agent_repositories: tuple[Path, ...] = ()
    # Ephemeral routine execution entry point.  It resolves the routine
    # document from Postgres for each invocation; it is deliberately not a
    # registry of compiled routines.
    routine_runner: RoutineRunner | None = None
    routine_resumer: Callable[..., Awaitable[dict[str, Any]]] | None = None
