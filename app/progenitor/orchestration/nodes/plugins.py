"""Heartbeat node for periodic plugin update checks."""

from __future__ import annotations

from typing import Any

from langgraph.runtime import Runtime

from app.progenitor.orchestration.nodes.context import HeartbeatContext
from app.progenitor.orchestration.state import ProgenitorState
from app.progenitor.plugins.install import check_plugin_updates
from app.progenitor.utils import utcnow_isoformat


async def refresh_plugins_runtime_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh plugin update status when the heartbeat runtime marks it due.

    This node only *checks* for upstream changes (digest drift) and sets
    ``available_version`` on affected entries.  It never installs, upgrades,
    or deletes skill documents — those are handled event-driven by the
    reconciler wired to store-change notifications.
    """
    context = runtime.context
    if context.state_manager is None:
        return {}
    if "plugin_update_check" not in context.due_maintenance:
        return {}

    try:
        summary = await check_plugin_updates(context.state_manager)
    except Exception as exc:
        summary = {"error": str(exc)}

    return {
        "last_plugin_update_check_at": utcnow_isoformat(),
        "plugin_update_check_summary": summary,
    }
