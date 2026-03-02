"""
Call Agent - Invoke an A2A agent by name or capability.

This is intentionally a *generic dispatch* tool because the set of available
agents can change during process uptime. It uses agents.json for lookup.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Awaitable
from typing import Any, cast

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

        # Refresh best-effort so agents.json edits show up immediately.
        load = getattr(tool_registry, "load", None)
        if callable(load):
            with contextlib.suppress(Exception):
                out = load()
                if inspect.isawaitable(out):
                    await cast(Awaitable[Any], out)

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

        a2a_client = A2AClient()

        # Pull latest agent card into memory before invocation.
        with contextlib.suppress(Exception):
            card = await a2a_client.get_agent_card(cfg, timeout=min(timeout_seconds, 5.0))
            if isinstance(card, dict):
                cfg = dict(cfg)
                cfg["agent_card"] = card
                if not cfg.get("url"):
                    url = str(card.get("url") or "").strip()
                    if url:
                        cfg["url"] = url

        result = await a2a_client.call_agent(
            agent_config=cfg,
            request=request,
            context={"caller": "progenitor"},
            timeout=timeout_seconds,
        )
        return ToolResult.ok(result if isinstance(result, dict) else {"result": result})
