"""DeepAgent tool adapters (core-only).

Core-only policy: Progenitor core should not expose build/git/shell/filesystem tools.
It only exposes:
- list_agents: inspect what A2A agents exist
- call_agent: delegate a request to an A2A agent by name/capability
"""

import contextlib
from typing import Any

from langchain_core.tools import StructuredTool

from ..runtime.time_utils import utcnow_isoformat
from ..tools.mcp import A2AClient


def create_deepagent_tools(
    tool_registry: Any = None,
    llm_client: Any = None,
    state_manager: Any = None,
) -> list:
    """Create the minimal tool set for progenitor-core."""
    a2a_client = A2AClient()

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
