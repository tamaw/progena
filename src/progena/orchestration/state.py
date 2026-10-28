"""Orchestration domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from src.progena.a2a.models import A2ATaskRecord
from src.progena.acp.models import ACPTaskRecord
from src.progena.async_subagents.models import AsyncSubagentTaskRecord
from src.progena.registry.models import AgentRegistryEntry
from src.progena.schedules.models import ScheduleEntry
from src.progena.sessions.models import ConversationMessage
from src.progena.utils import utcnow


class BackgroundTaskEntry(BaseModel):
    """Persisted background work item that Heartbeat can drain and execute."""

    id: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class ConversationState(TypedDict, total=False):
    """Conversation-related state."""

    messages: list[ConversationMessage]
    current_input: str | None
    current_input_role: str | None
    current_input_metadata: dict[str, Any] | None
    input_wait_seconds: float | None
    pending_messages_in_queue: int
    pending_response: str | None
    last_reasoning_trace: str | None
    attachments: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]


class SchedulingState(TypedDict, total=False):
    """Scheduling and registry state."""

    new_schedule_entry: ScheduleEntry | None
    current_time: datetime | None
    due_items: list[str]
    scheduled_enqueued_count: int
    last_schedule_poll_at: str | None
    agents: list[AgentRegistryEntry]
    a2a_completed_task: A2ATaskRecord | None
    a2a_completed_tasks: list[A2ATaskRecord]
    last_a2a_poll_at: str | None
    a2a_poll_ran: bool
    acp_completed_task: ACPTaskRecord | None
    acp_completed_tasks: list[ACPTaskRecord]
    async_subagent_completed_task: AsyncSubagentTaskRecord | None
    async_subagent_completed_tasks: list[AsyncSubagentTaskRecord]
    last_async_subagent_poll_at: str | None
    async_subagent_poll_ran: bool
    last_mcp_refresh_at: str | None
    mcp_refresh_summary: dict[str, Any] | None
    last_registry_discovery_at: str | None
    registry_discovery_summary: dict[str, Any] | None
    last_registry_refresh_at: str | None
    registry_refresh_summary: dict[str, Any] | None
    last_repo_scan_at: str | None
    repo_scan_summary: dict[str, Any] | None
    last_health_write_at: str | None
    is_scheduled_input: bool
    scheduled_schedule_id: str | None
    scheduled_run_id: str | None
    scheduled_message: str | None
    scheduled_result_status: str | None
    health_status_override: str | None
    health_error: str | None
    health: dict[str, Any]


class LoopControlState(TypedDict, total=False):
    """Loop control and metadata."""

    cycle_count: int
    actions_this_cycle: int
    shutdown_requested: bool
    last_commit_time: datetime | None
    thread_id: str
    requested_thread_id: str | None
    is_isolated_thread: bool
    queued_input: Any
    cycle_source: str | None
    cycle_error: str | None
    idle_cycles: int
    current_backoff_seconds: float
    heartbeat_action: str | None
    last_validation_result: dict[str, Any] | None
    validation_result_v2: dict[str, Any] | None
    last_checkpoint: str | None
    last_cycle_response: str | None


class ProgenaState(
    ConversationState,
    SchedulingState,
    LoopControlState,
    total=False,
):
    """Main state for the active DeepAgent-backed runtime."""

    pass
