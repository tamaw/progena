"""Heartbeat runtime shell for the graph-driven Progena loop."""

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from langgraph.runtime import Runtime

from src.progena.a2a.tasks import DEFAULT_A2A_POLL_SECONDS
from src.progena.orchestration.graphs.heartbeat_graph import (
    create_runtime_heartbeat_graph,
    validate_heartbeat_context,
)
from src.progena.orchestration.nodes.checkpoint import checkpoint_node
from src.progena.orchestration.nodes.context import HeartbeatContext
from src.progena.orchestration.nodes.health import write_health_status
from src.progena.orchestration.state import ProgenaState

from ..utils import utcnow
from .queues import QueuedInput as _QueuedInput
from .queues import RuntimeQueues

if TYPE_CHECKING:
    from ..config import ProgenaConfig

logger = logging.getLogger(__name__)

QueuedInput = _QueuedInput

_SCHEDULE_TICK = "schedule_tick"
_MCP_REFRESH = "mcp_refresh"
_PLUGIN_UPDATE_CHECK = "plugin_update_check"
_A2A_POLL = "a2a_poll"
_ASYNC_SUBAGENT_POLL = "async_subagent_poll"
_REGISTRY_DISCOVER = "registry_discover"
_REGISTRY_REFRESH = "registry_refresh"
_REPO_SCAN = "repo_scan"
_IDLE_HEALTH = "idle_health"

_MAINTENANCE_INTERVALS: tuple[tuple[str, str, str], ...] = (
    (_SCHEDULE_TICK, "last_schedule_poll_at", "scheduler_poll_seconds"),
    (_MCP_REFRESH, "last_mcp_refresh_at", "mcp_refresh_seconds"),
    (_PLUGIN_UPDATE_CHECK, "last_plugin_update_check_at", "plugin_update_check_seconds"),
    (_A2A_POLL, "last_a2a_poll_at", "a2a_poll_seconds"),
    (_ASYNC_SUBAGENT_POLL, "last_async_subagent_poll_at", "async_subagent_poll_seconds"),
    (_REGISTRY_DISCOVER, "last_registry_discovery_at", "registry_refresh_seconds"),
    (_REGISTRY_REFRESH, "last_registry_refresh_at", "registry_refresh_seconds"),
    (_REPO_SCAN, "last_repo_scan_at", "repo_scan_seconds"),
    (_IDLE_HEALTH, "last_health_write_at", "heartbeat_health_seconds"),
)


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


class ProgenaLoop:
    """Manages the perpetual execution loop."""

    def __init__(
        self,
        graph,
        state_manager,
        config: "ProgenaConfig",
        llm_client=None,
        tool_registry=None,
        project_root: Path | None = None,
        log_sink=None,
    ):
        self.graph = graph
        self.state_manager = state_manager
        self.config = config
        self.log_sink = log_sink
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
        self._state: dict[str, Any] = {}
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
            audit_log_sink=self.log_sink,
            startup_audit_payload=audit_payload,
            tool_registry=tool_registry,
            project_root=Path(project_root) if project_root is not None else None,
            llm_client=llm_client,
            idle_backoff_seconds=float(config.runtime.idle_backoff_seconds),
            scheduler_enabled=scheduler_enabled,
            scheduler_poll_seconds=scheduler_poll_seconds,
            a2a_poll_seconds=DEFAULT_A2A_POLL_SECONDS,
            async_subagent_poll_seconds=DEFAULT_A2A_POLL_SECONDS,
            mcp_refresh_seconds=float(config.runtime.mcp_refresh_seconds),
            plugin_update_check_seconds=float(config.runtime.plugin_update_check_seconds),
            registry_refresh_seconds=float(config.runtime.registry_refresh_seconds),
            repo_scan_seconds=float(config.runtime.repo_scan_seconds),
            worktree_dir=Path(config.storage.worktree_dir),
            agent_repositories=tuple(getattr(config.storage, "agent_repositories", ())),
            heartbeat_health_seconds=float(
                getattr(self.config.runtime, "checkpoint_interval_seconds", 30.0)
            ),
            timezone=getattr(self.config.runtime, "timezone", "UTC"),
            reasoning_enabled=bool(
                getattr(getattr(self.config, "llm", None), "reasoning_enabled", False)
            ),
            llm_model=str(getattr(getattr(self.config, "llm", None), "model_default", "") or ""),
            llm_reasoning_effort=str(
                getattr(getattr(self.config, "llm", None), "reasoning_effort", "") or ""
            ),
            routine_runner=getattr(self.graph, "routine_runner", None),
            routine_resumer=getattr(self.graph, "routine_resumer", None),
        )
        validate_heartbeat_context(self._heartbeat_context)
        self._heartbeat_graph = create_runtime_heartbeat_graph()

    async def run_forever(self) -> None:
        """Run the perpetual graph loop."""
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
            "last_async_subagent_poll_at": None,
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

        # Publish this process's identity before any cycle runs, so runs owned by
        # the previous process read as interrupted immediately, not after the
        # first heartbeat cycle (which can wait on queued input).
        await write_health_status(
            self.state_manager,
            "running",
            thread_id=str(self._state.get("thread_id") or "") or None,
            cycle_count=int(self._state.get("cycle_count", 0) or 0),
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
            cast(ProgenaState, self._state),
            Runtime(context=self._heartbeat_context),
        )
        self._state.update(updates)
        await write_health_status(
            self.state_manager,
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
                # Wait for queued input before ainvoke. This wait can
                # block up to input_timeout (commonly idle_backoff_seconds,
                # 30s by default, whenever nothing else is due).  Skip the
                # fetch if a prior cycle left an unconsumed item in state
                # (error-recovery retry case).
                if self._state.get("queued_input") is None:
                    self._state["queued_input"] = await self._queues.get_input(
                        self._heartbeat_context.input_timeout
                    )
                updates = await self._heartbeat_graph.ainvoke(
                    cast(ProgenaState, self._state),
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
        """Runtime queue service used by SSE and heartbeat nodes."""
        return self._queues

    @property
    def state(self) -> dict[str, Any]:
        """Mutable live graph state."""
        return self._state

    @property
    def heartbeat_context(self) -> HeartbeatContext:
        """Runtime context passed to heartbeat graph nodes."""
        return self._heartbeat_context

    def stop(self) -> None:
        """Signal the loop to stop."""
        self.running = False
