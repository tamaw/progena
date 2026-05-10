"""Runtime context passed to heartbeat graph nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class HeartbeatContext:
    queues: Any
    agent_graph: Any
    live_state: dict[str, Any]
    state_manager: Any
    health_state_dir: str | Path
    audit_logs_dir: str | Path | None
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
    registry_refresh_seconds: float
    heartbeat_health_seconds: float
    timezone: str
    reasoning_enabled: bool
    input_timeout: float = 1.0
    due_maintenance: set[str] = field(default_factory=set)
    startup_flags: set[str] = field(default_factory=set)
