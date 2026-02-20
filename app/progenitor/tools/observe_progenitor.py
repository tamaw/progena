"""
observe_progenitor (system tool)

Calls the Observe A2A agent to inspect Progenitor's state/logs and summarize health/errors.
"""

import os
from typing import Any

from .protocol import BaseSystemTool, ToolContext, ToolResult


class ObserveProgenitorTool(BaseSystemTool):
    @property
    def name(self) -> str:
        return "observe_progenitor"

    @property
    def description(self) -> str:
        return "Ask the Observe agent to summarize Progenitor health/status and explain recent errors using logs/state."

    @property
    def keywords(self) -> list[str]:
        # Leave empty so tool use is model-driven (not keyword-triggered).
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        request = str((context.tool_args or {}).get("request") or context.user_input or "").strip()
        if not request:
            request = "Summarize current health and recent errors."

        # Preferred: explicit endpoint (Docker DNS name, etc.)
        endpoint = os.getenv("PROGENITOR_OBSERVE_AGENT_ENDPOINT", "").strip().rstrip("/")
        if not endpoint and context.tool_registry is not None:
            # Fallback to registry discovery (may be localhost-based in non-container runs)
            for candidate in ("observe_agent", "observe"):
                cfg = context.tool_registry.find_by_name(candidate)  # type: ignore[union-attr]
                if isinstance(cfg, dict) and cfg.get("endpoint"):
                    endpoint = str(cfg["endpoint"]).strip().rstrip("/")
                    break

        if not endpoint:
            return ToolResult.fail(
                "Observe agent endpoint not configured. Set PROGENITOR_OBSERVE_AGENT_ENDPOINT "
                "(e.g., http://progenitor-agent-observe_agent:8080) and ensure the agent is running."
            )

        from .mcp import A2AClient

        a2a = A2AClient()
        agent_config: dict[str, Any] = {"transport": "http", "endpoint": endpoint, "status": "running"}
        result = await a2a.call_agent(
            agent_config=agent_config,
            request=request,
            context={
                "caller": "progenitor_system_tool",
                "tool": self.name,
            },
            timeout=60.0,
        )

        report = None
        if isinstance(result, dict) and result.get("success") is True:
            resp = result.get("response")
            if isinstance(resp, str):
                report = resp

        return ToolResult.ok(
            {
                "endpoint": endpoint,
                "success": bool(result.get("success")) if isinstance(result, dict) else False,
                "report": report,
                "error": result.get("error") if isinstance(result, dict) else "Unknown error",
            }
        )

