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

from ..runtime.a2a_tasks import build_task_response, dispatch_tracked_a2a_task
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
        if context.state_manager is None:
            return ToolResult.fail("state_manager unavailable; cannot track A2A tasks")

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
        nested_args = args.get("tool_args")
        if isinstance(nested_args, dict):
            merged_args = dict(nested_args)
            merged_args.update({k: v for k, v in args.items() if k != "tool_args"})
            args = merged_args

        agent = str(args.get("agent") or args.get("agent_name") or args.get("name") or "").strip()
        request = str(args.get("request") or context.user_input or "").strip()
        timeout_seconds = float(args.get("timeout_seconds") or 60.0)
        blocking_arg = args.get("blocking", False)
        blocking = (
            blocking_arg
            if isinstance(blocking_arg, bool)
            else str(blocking_arg).strip().lower() in {"1", "true", "yes", "on"}
        )

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

        result, tracked_task = await dispatch_tracked_a2a_task(
            state_manager=context.state_manager,
            agent_name=str(cfg.get("name") or agent or "").strip() or agent,
            agent_config=cfg,
            request_text=request,
            request_metadata={
                "caller": "progenitor",
                "dispatch_mode": "blocking" if blocking else "async",
            },
            source="call_agent",
            timeout=timeout_seconds,
            blocking=blocking,
            notify_on_completion=not blocking,
        )
        if not result.get("success"):
            return ToolResult.fail(str(result.get("error") or f"Agent call failed: {agent}"))

        if tracked_task is not None:
            payload = build_task_response(tracked_task)
            if not blocking:
                payload["message"] = (
                    f"Task submitted to '{agent}' (task_id: {tracked_task.task_id}). "
                    "Use get_a2a_task for progress and final results."
                )
            return ToolResult.ok(payload)

        if not blocking:
            return ToolResult.fail(f"{agent} did not return a trackable A2A task")

        return ToolResult.ok(result if isinstance(result, dict) else {"result": result})
