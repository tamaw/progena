"""Agent registry heartbeat adapters."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.runtime import Runtime

from ..registry import (
    discover_external_agents,
    refresh_agent_metadata,
)
from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext

logger = logging.getLogger(__name__)


async def _persist_registry(context: HeartbeatContext) -> None:
    """Mirror the in-memory runtime registry into the Postgres state document.

    Progenitor is the source of truth for the live agent list: the heartbeat
    refreshes each agent's card and ``/health`` status in memory, and this writes
    the *enriched* registry (including ``agent_card`` and live ``status``) to the
    ``("progenitor","state","registry")`` document. The backend reads that document
    read-through, so it must carry the full card — unlike the file-backed
    ``ToolRegistry.save()``, which intentionally drops ``agent_card``.
    """
    state_manager = getattr(context, "state_manager", None)
    registry = getattr(context, "tool_registry", None)
    if state_manager is None or registry is None or not hasattr(registry, "get_agents"):
        return
    try:
        agents = [dict(agent) for agent in registry.get_agents() if isinstance(agent, dict)]
        tools = registry.get_tools() if hasattr(registry, "get_tools") else []
        await state_manager.save_registry({"agents": agents, "tools": tools})
    except Exception as exc:  # pragma: no cover - best-effort mirror
        logger.warning("Failed to persist registry to runtime store: %s", exc)


async def discover_agents_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Discover agents from configured external repositories when due."""
    context = runtime.context
    if context.tool_registry is None or context.llm_client is None:
        return {}
    if "registry_discover" not in context.due_maintenance:
        return {}
    summary = await discover_external_agents(
        agent_repositories=context.agent_repositories,
        llm_client=context.llm_client,
        registry=context.tool_registry,
        state_manager=getattr(context, "state_manager", None),
    )
    await _persist_registry(context)
    return {
        "last_registry_discovery_at": utcnow_isoformat(),
        "registry_discovery_summary": summary,
    }


async def startup_discover_agents_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Run external-agent discovery once during startup."""
    context = runtime.context
    if (
        "startup_registry_discover" in context.startup_flags
        or context.tool_registry is None
        or context.llm_client is None
    ):
        return {}
    summary = await discover_external_agents(
        agent_repositories=context.agent_repositories,
        llm_client=context.llm_client,
        registry=context.tool_registry,
        state_manager=getattr(context, "state_manager", None),
    )
    await _persist_registry(context)
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
    await _persist_registry(context)
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
    await _persist_registry(context)
    context.startup_flags.add("startup_registry_refresh")
    return {
        "last_registry_refresh_at": utcnow_isoformat(),
        "registry_refresh_summary": summary,
    }
