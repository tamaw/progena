"""DeepAgent tool adapters.

Progenitor's system tools live under `app/progenitor/tools/` as `BaseSystemTool`
implementations. The DeepAgent needs to expose these as LangChain tools so the
LLM can invoke them directly during a conversation (e.g. `current_time`).
"""

import contextlib
import inspect
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.tools import StructuredTool

from ..config import MCPConfig
from ..nodes.node_helpers.registry import get_all_system_tools
from ..runtime.time_utils import utcnow_isoformat
from ..tools.mcp import A2AClient, setup_mcp_for_llm_client
from ..tools.protocol import ToolContext

EventPusher = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list:
    """Create DeepAgent-compatible tools (system tools + DeepAgent-only helpers)."""
    a2a_client = A2AClient()
    seen: set[str] = set()
    tools: list[StructuredTool] = []

    def _add_tool(tool: StructuredTool) -> None:
        if tool.name in seen:
            return
        seen.add(tool.name)
        tools.append(tool)

    def _wrap_system_tool(system_tool: Any) -> StructuredTool:
        async def _run(request: str = "", tool_args: dict[str, Any] | None = None) -> dict[str, Any]:
            ctx = ToolContext(
                tool_registry=tool_registry,
                llm_client=llm_client,
                state=None,
                state_manager=state_manager,
                user_input=request or "",
                tool_args=tool_args,
            )

            result = await system_tool.execute(ctx)
            if result.success:
                return result.data or {}
            return {
                "success": False,
                "error": result.error or f"{system_tool.name} failed",
                "should_retry_slow_path": bool(getattr(result, "should_retry_slow_path", False)),
            }

        return StructuredTool.from_function(
            coroutine=_run,
            name=str(getattr(system_tool, "name", "system_tool")),
            description=str(getattr(system_tool, "description", "Progenitor system tool")),
        )

    async def say_to_user(message: str) -> dict[str, Any]:
        """Send a one-way user notification via IPC (forwarded to Telegram when no web clients)."""
        msg = (message or "").strip()
        if not msg:
            return {"success": False, "error": "message is required"}

        push = getattr(llm_client, "_push_event", None)
        if not callable(push):
            return {"success": False, "error": "IPC event pusher unavailable"}

        await cast(EventPusher, push)(
            "agent_message",
            msg,
            {"tool": "say_to_user", "timestamp": utcnow_isoformat()},
        )
        return {"success": True}

    async def list_agents(refresh: bool = False) -> dict[str, Any]:
        """List known A2A agents from registry.json."""
        load = getattr(tool_registry, "load", None)
        if callable(load) and inspect.iscoroutinefunction(load):
            with contextlib.suppress(Exception):
                await load()
        if tool_registry and refresh and hasattr(tool_registry, "refresh_agent_cards"):
            with contextlib.suppress(Exception):
                await tool_registry.refresh_agent_cards(a2a_client)
        agents = tool_registry.get_agents() if tool_registry and hasattr(tool_registry, "get_agents") else []
        return {
            "timestamp": utcnow_isoformat(),
            "count": len(agents or []),
            "agents": agents or [],
        }

    async def call_agent(agent: str, request: str, timeout_seconds: float = 60.0) -> dict[str, Any]:
        """Call an A2A agent by name or capability."""
        if not tool_registry:
            return {"success": False, "error": "tool_registry unavailable"}
        load = getattr(tool_registry, "load", None)
        if callable(load) and inspect.iscoroutinefunction(load):
            with contextlib.suppress(Exception):
                await load()
        agent = (agent or "").strip()
        if not agent:
            return {"success": False, "error": "Missing agent name/capability"}

        cfg = None
        if hasattr(tool_registry, "find_by_name"):
            cfg = tool_registry.find_by_name(agent)
        if not cfg and hasattr(tool_registry, "find_agent_by_capability"):
            cfg = tool_registry.find_agent_by_capability(agent)
        if not isinstance(cfg, dict):
            return {"success": False, "error": f"Unknown agent: {agent}"}

        return await a2a_client.call_agent(
            agent_config=cfg,
            request=request or "",
            context={"caller": "progenitor-core"},
            timeout=float(timeout_seconds or 60.0),
        )

    async def mcp_refresh(persist: bool = True) -> dict[str, Any]:
        """Refresh MCP tool list from LiteLLM and (optionally) persist to registry.json."""
        if llm_client is None:
            return {"success": False, "error": "llm_client unavailable"}

        mcp_config = getattr(llm_client, "mcp_config", None)
        if not isinstance(mcp_config, MCPConfig) or not mcp_config.enabled:
            return {"success": False, "error": "MCP integration disabled"}

        await setup_mcp_for_llm_client(llm_client, mcp_config)

        if persist and tool_registry and hasattr(tool_registry, "register_tool"):
            with contextlib.suppress(Exception):
                for entry in llm_client.get_mcp_tool_registry_entries() or []:
                    await tool_registry.register_tool(entry)

        return {
            "success": True,
            "tool_count": len(getattr(llm_client, "mcp_tools", None) or []),
        }

    async def mcp_call(name: str, arguments: dict[str, Any] | str | None = None) -> dict[str, Any]:
        """Call an MCP tool by name via LiteLLM's MCP endpoint."""
        if llm_client is None:
            return {"success": False, "error": "llm_client unavailable"}
        tool_name = (name or "").strip()
        if not tool_name:
            return {"success": False, "error": "name is required"}

        # Allow passing JSON as a string for convenience.
        args_in: dict[str, Any] | str = arguments or {}
        if isinstance(args_in, str):
            raw = args_in.strip()
            if raw:
                with contextlib.suppress(Exception):
                    args_in = json.loads(raw)

        call = getattr(llm_client, "_call_mcp_tool", None)
        if not callable(call):
            return {"success": False, "error": "MCP caller unavailable"}
        return cast(dict[str, Any], await call({"name": tool_name, "arguments": args_in}))

    # DeepAgent-only tools
    _add_tool(
        StructuredTool.from_function(
            coroutine=say_to_user,
            name="say_to_user",
            description="Send a one-way message to the user via IPC (agent_message event).",
        )
    )
    _add_tool(
        StructuredTool.from_function(
            coroutine=list_agents,
            name="list_agents",
            description="List known A2A agents from the registry (optionally refresh agent cards).",
        )
    )
    _add_tool(
        StructuredTool.from_function(
            coroutine=call_agent,
            name="call_agent",
            description="Delegate a request to an A2A agent by name or capability.",
        )
    )
    _add_tool(
        StructuredTool.from_function(
            coroutine=mcp_refresh,
            name="mcp_refresh",
            description="Refresh MCP tool list from LiteLLM and optionally persist to registry.json.",
        )
    )
    _add_tool(
        StructuredTool.from_function(
            coroutine=mcp_call,
            name="mcp_call",
            description="Call an MCP tool by name via LiteLLM MCP (tools/call).",
        )
    )

    # System tools (dynamic discovery under app/progenitor/tools/)
    for system_tool in get_all_system_tools():
        _add_tool(_wrap_system_tool(system_tool))

    return tools
