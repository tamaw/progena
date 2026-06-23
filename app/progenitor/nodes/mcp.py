"""MCP refresh nodes and helpers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langgraph.runtime import Runtime

from ..config import MCPConfig
from ..state.models import ProgenitorState
from ..utils import utcnow_isoformat
from .context import HeartbeatContext


async def _sync_mcp_tool_flags(mcp_runtime: Any, state_manager: Any) -> None:
    """Persist discovered MCP tools (preserving user enabled flags) and apply them.

    The runtime owns the tool list; the backend (UI) owns the per-tool enabled
    flag. On each discovery we merge: keep the existing enabled value for known
    tools, default new tools to enabled, and drop tools no longer present. The
    resulting disabled set is applied so the agent stops seeing disabled tools.
    """
    existing_doc = await state_manager.get_mcp_tools()
    existing = existing_doc.get("tools", []) if isinstance(existing_doc, dict) else []
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
    await state_manager.save_mcp_tools({"tools": entries, "updated_at": utcnow_isoformat()})


async def sync_mcp_runtime_context(
    *,
    llm_client: Any,
    tool_registry: Any,
    mcp_config: MCPConfig,
    state_manager: Any = None,
    reload_config: bool = False,
) -> dict[str, Any]:
    """Refresh the live MCP runtime context and sync discovered tools."""
    from ..mcp import build_mcp_langchain_tool_wrappers, build_mcp_runtime_context

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

    mcp_servers, mcp_tools = await build_mcp_runtime_context(effective_config)
    mcp_langchain_tools = await build_mcp_langchain_tool_wrappers(effective_config, mcp_tools)
    mcp_runtime.set_context(mcp_servers, mcp_tools, mcp_langchain_tools)

    if state_manager is not None and hasattr(state_manager, "get_mcp_tools"):
        await _sync_mcp_tool_flags(mcp_runtime, state_manager)

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
