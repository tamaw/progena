"""Agent registry heartbeat adapters."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from ..registry import (
    discover_local_agents,
    refresh_agent_metadata,
)
from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext


async def discover_agents_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Discover local agents when the heartbeat runtime marks it due."""
    context = runtime.context
    if context.project_root is None or context.tool_registry is None or context.llm_client is None:
        return {}
    if "registry_discover" not in context.due_maintenance:
        return {}
    summary = await discover_local_agents(
        project_root=context.project_root,
        llm_client=context.llm_client,
        registry=context.tool_registry,
    )
    return {
        "last_registry_discovery_at": utcnow_isoformat(),
        "registry_discovery_summary": summary,
    }


async def startup_discover_agents_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run local agent discovery once during startup."""
    context = runtime.context
    if (
        "startup_registry_discover" in context.startup_flags
        or context.project_root is None
        or context.tool_registry is None
        or context.llm_client is None
    ):
        return {}
    summary = await discover_local_agents(
        project_root=context.project_root,
        llm_client=context.llm_client,
        registry=context.tool_registry,
    )
    context.startup_flags.add("startup_registry_discover")
    return {
        "last_registry_discovery_at": utcnow_isoformat(),
        "registry_discovery_summary": summary,
    }


async def refresh_registry_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh agent registry metadata when the heartbeat runtime marks it due."""
    context = runtime.context
    if context.tool_registry is None:
        return {}
    if "registry_refresh" not in context.due_maintenance:
        return {}
    summary = await refresh_agent_metadata(context.tool_registry)
    return {
        "last_registry_refresh_at": utcnow_isoformat(),
        "registry_refresh_summary": summary,
    }


async def startup_refresh_registry_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh agent metadata once during startup."""
    context = runtime.context
    if "startup_registry_refresh" in context.startup_flags or context.tool_registry is None:
        return {}
    summary = await refresh_agent_metadata(context.tool_registry)
    context.startup_flags.add("startup_registry_refresh")
    return {
        "last_registry_refresh_at": utcnow_isoformat(),
        "registry_refresh_summary": summary,
    }
