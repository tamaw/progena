"""Heartbeat/background Progenitor graph construction.

Flow overview:

        START
            |
            v
        +-------------------+
        |   startup_audit   |
        +-------------------+
            |
            v
        +-------------------+      Startup maintenance chain:
        |    startup_mcp    |      verify runtime, refresh MCP state,
        +-------------------+      and discover agent registry entries.
            |
            v
        +---------------------------+
        | startup_registry_discover |
        +---------------------------+
            |
            v
        +--------------------------+
        | startup_registry_refresh |
        +--------------------------+
            |
            v
        +-------------+
        |   dequeue   |
        +-------------+
            |
            +-------------------------------+
            | queued input?                 |
            |                               |
            | no                            | yes
            v                               v
        +-------------+                +-------------+
        |    idle     |                |    setup    |
        +-------------+                +-------------+
            |                               |
            v                               +-------------------------------+
        +---------------+                 | current input?                |
        | schedule_tick |                 | no -> END                     |
        +---------------+                 | yes + isolated -> isolated    |
            |                               | yes + main -> cycle           |
            v                               +-------------------------------+
        +---------------+                           |
        |  mcp_refresh  |                           v
        +---------------+                +-------------------+
            |                              | cycle / isolated  |
            v                              +-------------------+
        +---------------+                           |
        |   a2a_poll    |                           +----------------------+
        +---------------+                           | cycle_error?         |
            |                                         | yes -> error         |
            v                                         | no  -> completion    |
        +---------------+                           +----------------------+
        |   a2a_apply   |                                      |
        +---------------+                                      v
            |                               +--------------------------------------+
            v                               | error -> scheduled_completion        |
        +---------------+                 | scheduled_completion -> checkpoint   |
        |  a2a_notify   |                 | checkpoint -> health -> signal       |
        +---------------+                 | signal -> cleanup -> END             |
            |                               +--------------------------------------+
            v
        +-------------------+
        | registry_discover |
        +-------------------+
            |
            v
        +------------------+
        | registry_refresh |
        +------------------+
            |
            v
        +--------------+
        | idle_health  |
        +--------------+
            |
            v
         END

In short, the graph has two modes:
1. Idle heartbeat mode runs maintenance work that the heartbeat runtime has marked due when no user input is queued.
2. Cycle mode runs one conversation turn, records health/checkpoints, then cleans up.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from ..nodes.a2a import (
    apply_completed_tasks_node,
    notify_completed_tasks_node,
    poll_tracked_tasks_node,
)
from ..nodes.audit import startup_audit_node
from ..nodes.checkpoint import checkpoint_node
from ..nodes.context import HeartbeatContext
from ..nodes.control import (
    cleanup_cycle_state_node,
    cycle_error_node,
    idle_state_node,
    signal_cycle_complete_node,
)
from ..nodes.cycle import cycle_node, isolated_cycle_node
from ..nodes.health import idle_health_tick_node, runtime_health_node
from ..nodes.intake import cycle_setup_node, dequeue_input_node
from ..nodes.mcp import refresh_mcp_runtime_node, startup_mcp_refresh_node
from ..nodes.registry import (
    discover_agents_node,
    refresh_registry_node,
    startup_discover_agents_node,
    startup_refresh_registry_node,
)
from ..nodes.schedule import scheduled_completion_node, scheduler_tick_node
from ..state.models import ProgenitorState, redact_secrets

logger = logging.getLogger(__name__)

HeartbeatNode = Callable[[Any, Runtime[HeartbeatContext]], Awaitable[dict[str, Any]]]
DequeueRoute = Literal["idle", "setup"]
SetupRoute = Literal["end", "cycle", "isolated_cycle"]
CycleRoute = Literal["error", "scheduled_completion"]


def _guard_maintenance_node(name: str, node: HeartbeatNode) -> Any:
    async def wrapped(
        state: Any,
        runtime: Runtime[HeartbeatContext],
    ) -> dict[str, Any]:
        try:
            return await node(state, runtime)
        except Exception as exc:
            safe_error = redact_secrets(f"Heartbeat node {name} failed: {exc}")
            logger.error("%s", safe_error, exc_info=True)
            with suppress(Exception):
                await runtime.context.queues.push_event(
                    "error",
                    safe_error,
                    {"heartbeat_node": name},
                )
            return {
                "heartbeat_action": "error",
                "health_status_override": "error",
                "health_error": safe_error,
            }

    return wrapped


def validate_heartbeat_context(context: HeartbeatContext) -> None:
    """Validate runtime dependencies supplied to the heartbeat graph."""
    if context.queues is None:
        raise ValueError("queues is required")
    if context.agent_graph is None:
        raise ValueError("agent_graph is required")
    if context.live_state is None:
        raise ValueError("live_state is required")
    if context.state_manager is None:
        raise ValueError("state_manager is required")
    if not str(context.health_state_dir).strip():
        raise ValueError("health_state_dir must not be empty")
    if context.idle_backoff_seconds < 0:
        raise ValueError("idle_backoff_seconds must be greater than or equal to 0")
    for interval_name, interval_value in (
        ("scheduler_poll_seconds", context.scheduler_poll_seconds),
        ("a2a_poll_seconds", context.a2a_poll_seconds),
        ("mcp_refresh_seconds", context.mcp_refresh_seconds),
        ("registry_refresh_seconds", context.registry_refresh_seconds),
        ("heartbeat_health_seconds", context.heartbeat_health_seconds),
        ("input_timeout", context.input_timeout),
    ):
        if interval_value <= 0:
            raise ValueError(f"{interval_name} must be greater than 0")
    if not context.timezone.strip():
        raise ValueError("timezone must not be empty")


def create_runtime_heartbeat_graph() -> CompiledStateGraph[Any, HeartbeatContext, Any, Any]:
    """Create the runtime heartbeat graph that drives one loop iteration."""
    graph = StateGraph(ProgenitorState, context_schema=HeartbeatContext)

    graph.add_node("startup_audit", _guard_maintenance_node("startup_audit", startup_audit_node))
    graph.add_node("startup_mcp", _guard_maintenance_node("startup_mcp", startup_mcp_refresh_node))
    graph.add_node(
        "startup_registry_discover",
        _guard_maintenance_node("startup_registry_discover", startup_discover_agents_node),
    )
    graph.add_node(
        "startup_registry_refresh",
        _guard_maintenance_node("startup_registry_refresh", startup_refresh_registry_node),
    )
    graph.add_node("dequeue", _guard_maintenance_node("dequeue", dequeue_input_node))
    graph.add_node("idle", idle_state_node)
    graph.add_node("schedule_tick", _guard_maintenance_node("schedule_tick", scheduler_tick_node))
    graph.add_node("mcp_refresh", _guard_maintenance_node("mcp_refresh", refresh_mcp_runtime_node))
    graph.add_node("a2a_poll", _guard_maintenance_node("a2a_poll", poll_tracked_tasks_node))
    graph.add_node("a2a_apply", _guard_maintenance_node("a2a_apply", apply_completed_tasks_node))
    graph.add_node("a2a_notify", _guard_maintenance_node("a2a_notify", notify_completed_tasks_node))
    graph.add_node(
        "registry_discover", _guard_maintenance_node("registry_discover", discover_agents_node)
    )
    graph.add_node(
        "registry_refresh", _guard_maintenance_node("registry_refresh", refresh_registry_node)
    )
    graph.add_node("idle_health", _guard_maintenance_node("idle_health", idle_health_tick_node))
    graph.add_node("setup", _guard_maintenance_node("setup", cycle_setup_node))
    graph.add_node("cycle", cycle_node)
    graph.add_node("isolated_cycle", isolated_cycle_node)
    graph.add_node("error", _guard_maintenance_node("error", cycle_error_node))
    graph.add_node(
        "scheduled_completion",
        _guard_maintenance_node("scheduled_completion", scheduled_completion_node),
    )
    graph.add_node("checkpoint", _guard_maintenance_node("checkpoint", checkpoint_node))
    graph.add_node("health", _guard_maintenance_node("health", runtime_health_node))
    graph.add_node("signal", _guard_maintenance_node("signal", signal_cycle_complete_node))
    graph.add_node("cleanup", _guard_maintenance_node("cleanup", cleanup_cycle_state_node))

    graph.add_edge(START, "startup_audit")
    graph.add_edge("startup_audit", "startup_mcp")
    graph.add_edge("startup_mcp", "startup_registry_discover")
    graph.add_edge("startup_registry_discover", "startup_registry_refresh")
    graph.add_edge("startup_registry_refresh", "dequeue")
    graph.add_conditional_edges(
        "dequeue",
        _route_after_dequeue,
        {
            "idle": "idle",
            "setup": "setup",
        },
    )
    graph.add_edge("idle", "schedule_tick")
    graph.add_edge("schedule_tick", "mcp_refresh")
    graph.add_edge("mcp_refresh", "a2a_poll")
    graph.add_edge("a2a_poll", "a2a_apply")
    graph.add_edge("a2a_apply", "a2a_notify")
    graph.add_edge("a2a_notify", "registry_discover")
    graph.add_edge("registry_discover", "registry_refresh")
    graph.add_edge("registry_refresh", "idle_health")
    graph.add_edge("idle_health", END)
    graph.add_conditional_edges(
        "setup",
        _route_after_setup,
        {
            "end": END,
            "cycle": "cycle",
            "isolated_cycle": "isolated_cycle",
        },
    )
    graph.add_conditional_edges(
        "cycle",
        _route_after_cycle,
        {
            "error": "error",
            "scheduled_completion": "scheduled_completion",
        },
    )
    graph.add_conditional_edges(
        "isolated_cycle",
        _route_after_cycle,
        {
            "error": "error",
            "scheduled_completion": "scheduled_completion",
        },
    )
    graph.add_edge("error", "scheduled_completion")
    graph.add_edge("scheduled_completion", "checkpoint")
    graph.add_edge("checkpoint", "health")
    graph.add_edge("health", "signal")
    graph.add_edge("signal", "cleanup")
    graph.add_edge("cleanup", END)
    return graph.compile()


def _route_after_dequeue(state: ProgenitorState) -> DequeueRoute:
    return "setup" if state.get("queued_input") else "idle"


def _route_after_setup(state: ProgenitorState) -> SetupRoute:
    if not state.get("current_input"):
        return "end"
    if state.get("is_isolated_thread"):
        return "isolated_cycle"
    return "cycle"


def _route_after_cycle(state: ProgenitorState) -> CycleRoute:
    return "error" if state.get("cycle_error") else "scheduled_completion"
