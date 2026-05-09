"""Heartbeat runtime shell for the graph-driven Progenitor loop."""

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, cast

from langgraph.runtime import Runtime

from ..a2a import DEFAULT_A2A_POLL_SECONDS
from ..graph.heartbeat_graph import (
    create_runtime_heartbeat_graph,
    validate_heartbeat_context,
)
from ..nodes.checkpoint import checkpoint_node
from ..nodes.context import HeartbeatContext
from ..nodes.health import write_health_status
from ..state.models import ProgenitorState
from ..utils import utcnow
from .queues import QueuedInput as _QueuedInput
from .queues import RuntimeQueues

if TYPE_CHECKING:
    from ..config import ProgenitorConfig

logger = logging.getLogger(__name__)

QueuedInput = _QueuedInput

_SCHEDULE_TICK = "schedule_tick"
_MCP_REFRESH = "mcp_refresh"
_A2A_POLL = "a2a_poll"
_REGISTRY_DISCOVER = "registry_discover"
_REGISTRY_REFRESH = "registry_refresh"
_IDLE_HEALTH = "idle_health"

_MAINTENANCE_INTERVALS: tuple[tuple[str, str, str], ...] = (
    (_SCHEDULE_TICK, "last_schedule_poll_at", "scheduler_poll_seconds"),
    (_MCP_REFRESH, "last_mcp_refresh_at", "mcp_refresh_seconds"),
    (_A2A_POLL, "last_a2a_poll_at", "a2a_poll_seconds"),
    (_REGISTRY_DISCOVER, "last_registry_discovery_at", "registry_refresh_seconds"),
    (_REGISTRY_REFRESH, "last_registry_refresh_at", "registry_refresh_seconds"),
    (_IDLE_HEALTH, "last_health_write_at", "heartbeat_health_seconds"),
)


# Module-level reference to the current loop instance for agent tools.
_current_loop: Optional["ProgenitorLoop"] = None


def get_current_loop() -> Optional["ProgenitorLoop"]:
    """Get the current loop instance for agent tools."""
    return _current_loop


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _seconds_until_due(last_run_at: Any, interval_seconds: float, *, now: datetime) -> float:
    if interval_seconds <= 0:
        return 0.0
    last_run = _parse_iso_datetime(last_run_at)
    if last_run is None:
        return 0.0
    remaining = (last_run + timedelta(seconds=interval_seconds) - now).total_seconds()
    return max(0.0, remaining)


class ProgenitorLoop:
    """Manages the perpetual execution loop."""

    def __init__(
        self,
        graph,
        state_manager,
        config: "ProgenitorConfig",
        llm_client=None,
        tool_registry=None,
        project_root: Path | None = None,
    ):
        self.graph = graph
        self.state_manager = state_manager
        self.config = config
        self.running = False
        scheduler_cfg = getattr(self.config, "scheduler", None)
        scheduler_enabled = bool(
            scheduler_cfg is not None and getattr(scheduler_cfg, "enabled", False)
        )
        try:
            scheduler_poll_seconds = float(getattr(scheduler_cfg, "poll_seconds", 30.0))
        except (TypeError, ValueError):
            scheduler_poll_seconds = 30.0
        scheduler_poll_seconds = max(1.0, scheduler_poll_seconds)

        self._queues = RuntimeQueues(config.runtime)
        self._graph_lock = asyncio.Lock()
        self._state: dict[str, Any] = {}
        state_dir = getattr(self.state_manager, "state_dir", None)
        self._state_dir = state_dir if isinstance(state_dir, (str, Path)) else Path(".")
        storage_cfg = getattr(config, "storage", None)
        audit_logs_dir = getattr(storage_cfg, "log_dir", None)
        audit_payload: dict[str, Any] | None = None
        to_dict = getattr(config, "to_dict", None)
        if callable(to_dict):
            payload = to_dict(redact_secrets=True)
            if isinstance(payload, dict):
                audit_payload = payload
        self._heartbeat_context = HeartbeatContext(
            queues=self._queues,
            agent_graph=self.graph,
            live_state=self._state,
            state_manager=self.state_manager,
            health_state_dir=self._state_dir,
            audit_logs_dir=audit_logs_dir,
            startup_audit_payload=audit_payload,
            tool_registry=tool_registry,
            project_root=Path(project_root) if project_root is not None else None,
            llm_client=llm_client,
            idle_backoff_seconds=float(config.runtime.idle_backoff_seconds),
            scheduler_enabled=scheduler_enabled,
            scheduler_poll_seconds=scheduler_poll_seconds,
            a2a_poll_seconds=DEFAULT_A2A_POLL_SECONDS,
            mcp_refresh_seconds=60.0,
            registry_refresh_seconds=60.0,
            heartbeat_health_seconds=float(
                getattr(self.config.runtime, "checkpoint_interval_seconds", 30.0)
            ),
            timezone=getattr(self.config.runtime, "timezone", "UTC"),
            reasoning_enabled=bool(
                getattr(getattr(self.config, "llm", None), "reasoning_enabled", False)
            ),
        )
        validate_heartbeat_context(self._heartbeat_context)
        self._heartbeat_graph = create_runtime_heartbeat_graph()

    async def run_forever(self) -> None:
        """Run the perpetual graph loop."""
        global _current_loop
        _current_loop = self
        self.running = True

        boot_thread_id = str(uuid.uuid4())

        self._state = {
            "thread_id": boot_thread_id,
            "cycle_count": 0,
            "actions_this_cycle": 0,
            "shutdown_requested": False,
            "messages": [],
            "agents": [],
            "current_input_role": None,
            "current_input_metadata": None,
            "input_wait_seconds": 0.0,
            "pending_messages_in_queue": 0,
            "idle_cycles": 0,
            "current_backoff_seconds": float(self.config.runtime.idle_backoff_seconds),
            "last_health_write_at": None,
        }

        current_state = await self.state_manager.get_current_state()
        self._state.update(current_state)
        restored_thread_id = str(
            current_state.get("thread_id") or self._state.get("thread_id") or ""
        ).strip()
        if restored_thread_id:
            self._state["thread_id"] = restored_thread_id
        self._heartbeat_context.live_state = self._state

        logger.info(
            "Starting perpetual loop (boot_thread_id: %s, conversation_thread_id: %s)",
            boot_thread_id,
            self._state.get("thread_id"),
        )

        await self._queues.push_event(
            "loop_started", f"Perpetual loop started (thread_id: {boot_thread_id})"
        )

        input_task = asyncio.create_task(self._input_handler_loop())

        try:
            await input_task
        except asyncio.CancelledError:
            logger.info("Loop cancelled by signal")
            input_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await input_task

        updates = await checkpoint_node(
            cast(ProgenitorState, self._state),
            Runtime(context=self._heartbeat_context),
        )
        self._state.update(updates)
        await write_health_status(
            self._state_dir,
            "stopped",
            thread_id=str(self._state.get("thread_id") or "") or None,
            cycle_count=int(self._state.get("cycle_count", 0) or 0),
        )
        await self._queues.push_event("loop_stopped", "Perpetual loop stopped")
        logger.info("Loop terminated")

    async def _input_handler_loop(self) -> None:
        """Invoke the heartbeat graph until stopped."""
        while self.running:
            try:
                self._prepare_heartbeat_poll()
                async with self._graph_lock:
                    updates = await self._heartbeat_graph.ainvoke(
                        cast(ProgenitorState, self._state),
                        context=self._heartbeat_context,
                    )
                if isinstance(updates, dict):
                    self.apply_graph_updates(updates)
                self._heartbeat_context.due_maintenance.clear()

            except asyncio.CancelledError:
                logger.info("Input handler cancelled")
                break
            except Exception as e:
                logger.error("Input handler error: %s", e, exc_info=True)
                await self._queues.push_event("error", f"Input handler error: {e}")
                self._heartbeat_context.due_maintenance.clear()
                await asyncio.sleep(1.0)

    def _prepare_heartbeat_poll(self) -> None:
        due_maintenance, poll_timeout = self._resolve_heartbeat_poll()
        self._heartbeat_context.due_maintenance = due_maintenance
        self._heartbeat_context.input_timeout = poll_timeout
        self._state["current_backoff_seconds"] = poll_timeout

    def _resolve_heartbeat_poll(self) -> tuple[set[str], float]:
        now = utcnow()
        due_maintenance: set[str] = set()
        next_due_seconds: list[float] = []

        for maintenance_key, state_key, interval_attr in _MAINTENANCE_INTERVALS:
            interval_seconds = float(getattr(self._heartbeat_context, interval_attr))
            remaining = _seconds_until_due(self._state.get(state_key), interval_seconds, now=now)
            if remaining <= 0:
                due_maintenance.add(maintenance_key)
            else:
                next_due_seconds.append(remaining)

        poll_timeout = min(
            next_due_seconds, default=float(self.config.runtime.idle_backoff_seconds)
        )
        poll_timeout = max(0.05, poll_timeout)
        return due_maintenance, poll_timeout

    def apply_graph_updates(self, updates: dict[str, Any]) -> None:
        """Merge graph updates while preserving live thread mutations made during execution."""

        live_thread_id = str(self._state.get("thread_id") or "").strip()
        merged = dict(updates)
        update_thread_id = str(merged.get("thread_id") or "").strip()
        if live_thread_id and update_thread_id and update_thread_id != live_thread_id:
            merged["thread_id"] = live_thread_id
        self._state.update(merged)

    @property
    def queues(self) -> RuntimeQueues:
        """Runtime queue service used by IPC and heartbeat nodes."""
        return self._queues

    @property
    def state(self) -> dict[str, Any]:
        """Mutable live graph state."""
        return self._state

    @property
    def heartbeat_context(self) -> HeartbeatContext:
        """Runtime context passed to heartbeat graph nodes."""
        return self._heartbeat_context

    @property
    def graph_lock(self) -> asyncio.Lock:
        """Serialize graph execution across heartbeat and direct IPC streaming."""
        return self._graph_lock

    def stop(self) -> None:
        """Signal the loop to stop."""
        self.running = False
