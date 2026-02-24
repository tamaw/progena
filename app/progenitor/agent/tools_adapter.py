"""DeepAgent tool adapters (core-only).

Core-only policy: Progenitor core should not expose build/git/shell/filesystem tools.
It only exposes:
- list_agents: inspect what A2A agents exist
- call_agent: delegate a request to an A2A agent by name/capability
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.tools import StructuredTool

from ..runtime.time_utils import utcnow_isoformat
from ..tools.mcp import A2AClient

EventPusher = Callable[[str, str, dict[str, Any] | None], Awaitable[None]]


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list:
    """Create the minimal tool set for progenitor-core."""
    a2a_client = A2AClient()

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

    return [
        StructuredTool.from_function(
            coroutine=say_to_user,
            name="say_to_user",
            description="Send a one-way message to the user via IPC (agent_message event).",
        ),
        StructuredTool.from_function(
            coroutine=list_agents,
            name="list_agents",
            description="List known A2A agents from the registry (optionally refresh agent cards).",
        ),
        StructuredTool.from_function(
            coroutine=call_agent,
            name="call_agent",
            description="Delegate a request to an A2A agent by name or capability.",
        ),
    ]
