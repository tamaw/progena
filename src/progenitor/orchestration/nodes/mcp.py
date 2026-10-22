"""MCP refresh nodes and helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.runtime import Runtime

from src.progenitor.config import MCPConfig
from src.progenitor.orchestration.nodes.context import HeartbeatContext
from src.progenitor.orchestration.state import ProgenitorState
from src.progenitor.utils import utcnow_isoformat


def _tools_content_snapshot(entries: list[Any]) -> dict[str, tuple[Any, Any, Any, Any]]:
    """Order-insensitive comparable snapshot of a ``tools`` list (name -> content tuple)."""
    return {
        str(entry.get("name")): (
            entry.get("server"),
            entry.get("original_name"),
            entry.get("description"),
            entry.get("enabled"),
        )
        for entry in entries
        if isinstance(entry, dict) and entry.get("name")
    }


async def _sync_mcp_tool_flags(
    mcp_runtime: Any,
    state_manager: Any,
    server_status: dict[str, Any] | None = None,
) -> bool:
    """Persist discovered MCP tools (preserving user enabled flags) and apply them.

    The runtime owns the tool list; the backend (UI) owns the per-tool enabled
    flag. On each discovery we merge: keep the existing enabled value for known
    tools, default new tools to enabled, and drop tools no longer present. The
    resulting disabled set is applied so the agent stops seeing disabled tools.

    ``server_status`` (name -> {status, error, tool_count, checked_at}) records
    each server's last discovery outcome so the UI can show a failed state with
    a reason instead of a server that silently has no tools.

    Returns whether the persisted ``tools`` (name/server/description/enabled)
    or ``servers`` content actually changed, order-insensitively, compared to
    the previous document — used to decide whether the agent-visible tool
    prefix changed and the Azure prompt cache should be invalidated.
    """
    existing_doc = await state_manager.get_mcp_tools()
    existing = existing_doc.get("tools", []) if isinstance(existing_doc, dict) else []
    existing_servers = existing_doc.get("servers", {}) if isinstance(existing_doc, dict) else {}
    enabled_by_name = {
        str(tool.get("name")): bool(tool.get("enabled", True))
        for tool in existing
        if isinstance(tool, dict) and tool.get("name")
    }

    entries: list[dict[str, Any]] = []
    disabled: set[str] = set()
    for tool in mcp_runtime.mcp_tools:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        enabled = enabled_by_name.get(name, True)
        if not enabled:
            disabled.add(name)
        entries.append(
            {
                "name": name,
                "server": tool.get("mcp_server_name"),
                "original_name": tool.get("mcp_original_name") or name,
                "description": str(tool.get("description") or ""),
                "enabled": enabled,
            }
        )

    mcp_runtime.set_disabled_tools(disabled)
    new_servers = server_status or {}
    changed = _tools_content_snapshot(entries) != _tools_content_snapshot(existing) or (
        new_servers != (existing_servers if isinstance(existing_servers, dict) else {})
    )
    await state_manager.save_mcp_tools(
        {
            "tools": entries,
            "servers": new_servers,
            "updated_at": utcnow_isoformat(),
        }
    )
    return changed


async def reapply_mcp_disabled_tools(llm_client: Any, state_manager: Any) -> tuple[int, bool]:
    """Re-apply per-tool enabled flags to the live runtime without re-discovery.

    The backend (UI) toggles a tool by flipping its ``enabled`` flag in the
    ``mcp_tools`` document. This reads those flags and updates the runtime's
    disabled set so the toggle takes effect immediately — without re-fetching
    every server (which a full ``sync_mcp_runtime_context`` does). Returns
    ``(disabled_count, changed)``, where ``changed`` reports whether the newly
    applied disabled set differs from what the runtime had before this call —
    used to decide whether the agent-visible tool prefix changed and the Azure
    prompt cache should be invalidated.
    """
    mcp_runtime = getattr(llm_client, "mcp_runtime", None)
    if mcp_runtime is None or state_manager is None:
        return 0, False
    doc = await state_manager.get_mcp_tools()
    raw = doc.get("tools", []) if isinstance(doc, dict) else []
    disabled = {
        str(tool.get("name"))
        for tool in raw
        if isinstance(tool, dict) and tool.get("name") and not bool(tool.get("enabled", True))
    }
    previous_disabled = set(getattr(mcp_runtime, "disabled_tools", set()) or set())
    mcp_runtime.set_disabled_tools(disabled)
    return len(disabled), disabled != previous_disabled


async def sync_mcp_runtime_context(
    *,
    llm_client: Any,
    tool_registry: Any,
    mcp_config: MCPConfig,
    state_manager: Any = None,
    reload_config: bool = False,
) -> dict[str, Any]:
    """Refresh the live MCP runtime context and sync discovered tools."""
    from src.progenitor.mcp.context import (
        build_mcp_langchain_tool_wrappers,
        build_mcp_runtime_context,
    )

    mcp_runtime = getattr(llm_client, "mcp_runtime", None)
    if mcp_runtime is None:
        raise RuntimeError("llm_client.mcp_runtime must be initialized before MCP refresh")

    effective_config = mcp_config
    if reload_config:
        if state_manager is None or not hasattr(state_manager, "get_mcp_config"):
            raise RuntimeError("state_manager.get_mcp_config is required for MCP refresh")
        payload = await state_manager.get_mcp_config()
        effective_config = MCPConfig.from_payload(payload)

    mcp_runtime.config = effective_config
    await mcp_runtime.close()
    llm_client.mcp_config = effective_config
    llm_client.set_mcp_runtime(mcp_runtime)

    mcp_servers, mcp_tools, server_status = await build_mcp_runtime_context(effective_config)
    mcp_langchain_tools = await build_mcp_langchain_tool_wrappers(effective_config, mcp_tools)
    mcp_runtime.set_context(mcp_servers, mcp_tools, mcp_langchain_tools)

    tool_doc_changed = False
    if state_manager is not None and hasattr(state_manager, "get_mcp_tools"):
        tool_doc_changed = await _sync_mcp_tool_flags(mcp_runtime, state_manager, server_status)

    if tool_registry is not None:
        entries = mcp_runtime.get_tool_registry_entries()
        set_runtime_tools = getattr(tool_registry, "set_runtime_tools", None)
        if callable(set_runtime_tools):
            set_runtime_tools(entries)
        else:
            register_tool = getattr(tool_registry, "register_tool", None)
            if callable(register_tool):
                async_register_tool = cast(
                    Callable[[dict[str, Any]], Awaitable[Any]], register_tool
                )
                for entry in entries:
                    await async_register_tool(entry)

    return {
        "server_count": len(mcp_servers),
        "tool_count": len(mcp_tools),
        "config_reloaded": reload_config,
        "tool_doc_changed": tool_doc_changed,
    }


async def startup_mcp_refresh_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh MCP runtime once at startup."""
    context = runtime.context
    if "startup_mcp" in context.startup_flags or context.llm_client is None:
        return {}
    mcp_config = getattr(context.llm_client, "mcp_config", None)
    if not isinstance(mcp_config, MCPConfig):
        context.startup_flags.add("startup_mcp")
        return {}
    try:
        summary = await sync_mcp_runtime_context(
            llm_client=context.llm_client,
            tool_registry=context.tool_registry,
            mcp_config=mcp_config,
            state_manager=context.state_manager,
            reload_config=True,
        )
    except Exception as exc:
        summary = {"error": str(exc)}
    context.startup_flags.add("startup_mcp")
    return {
        "last_mcp_refresh_at": utcnow_isoformat(),
        "mcp_refresh_summary": summary,
    }


async def refresh_mcp_runtime_node(
    _state: ProgenitorState,
    runtime: Runtime[HeartbeatContext],
) -> dict[str, Any]:
    """Refresh MCP runtime when the heartbeat runtime marks it due."""
    context = runtime.context
    if context.llm_client is None:
        return {}
    if "mcp_refresh" not in context.due_maintenance:
        return {}
    mcp_config = getattr(context.llm_client, "mcp_config", None)
    if not isinstance(mcp_config, MCPConfig):
        return {}
    try:
        summary = await sync_mcp_runtime_context(
            llm_client=context.llm_client,
            tool_registry=context.tool_registry,
            mcp_config=mcp_config,
            state_manager=context.state_manager,
            reload_config=True,
        )
    except Exception as exc:
        summary = {"error": str(exc)}
    return {
        "last_mcp_refresh_at": utcnow_isoformat(),
        "mcp_refresh_summary": summary,
    }
