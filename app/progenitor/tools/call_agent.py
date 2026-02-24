"""
Call Agent - Invoke an A2A agent by name or capability.

This is intentionally a *generic dispatch* tool because the set of available
agents can change during process uptime. It uses registry.json for lookup.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from .mcp import A2AClient
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class CallAgentTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "call_agent"

    @property
    def description(self) -> str:
        return "Call an A2A agent by name or capability"

    @property
    def keywords(self) -> list[str]:
        return ["call agent", "delegate", "run agent", "ask agent"]

    async def execute(self, context: ToolContext) -> ToolResult:
        tool_registry = context.tool_registry
        if not tool_registry:
            return ToolResult.fail("tool_registry unavailable")

        # Refresh best-effort so registry.json edits show up immediately.
        load = getattr(tool_registry, "load", None)
        if callable(load):
            with contextlib.suppress(Exception):
                await load()

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        agent = str(args.get("agent") or "").strip()
        request = str(args.get("request") or context.user_input or "").strip()
        timeout_seconds = float(args.get("timeout_seconds") or 60.0)

        if not agent:
            return ToolResult.fail("Missing 'agent' (name or capability)")
        if not request:
            return ToolResult.fail("Missing 'request'")

        cfg: dict[str, Any] | None = None
        if hasattr(tool_registry, "find_by_name"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_by_name(agent)
        if not cfg and hasattr(tool_registry, "find_agent_by_capability"):
            with contextlib.suppress(Exception):
                cfg = tool_registry.find_agent_by_capability(agent)

        if not isinstance(cfg, dict):
            return ToolResult.fail(f"Unknown agent: {agent}")

        result = await A2AClient().call_agent(
            agent_config=cfg,
            request=request,
            context={"caller": "progenitor"},
            timeout=timeout_seconds,
        )
        return ToolResult.ok(result if isinstance(result, dict) else {"result": result})

