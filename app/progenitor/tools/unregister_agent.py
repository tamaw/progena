"""Tool to remove an agent from the runtime registry (database-backed).

Deletes any registered agent by name — ACP, A2A, or otherwise — from the
canonical registry store. The inverse of ``register_acp_agent`` and the backend
agent-registration flow.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class UnregisterAgentArgs(BaseModel):
    """Arguments for removing an agent from the registry."""

    name: str = Field(description="Name of the registered agent to remove.")


class UnregisterAgentTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return UnregisterAgentArgs

    @property
    def name(self) -> str:
        return "unregister_agent"

    @property
    def description(self) -> str:
        return (
            "Remove a registered agent (ACP, A2A, etc.) from the registry by name. "
            "Deletes it from the database so it can no longer be started or messaged."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        tool_registry = context.tool_registry
        if tool_registry is None or not hasattr(tool_registry, "unregister"):
            return ToolResult.fail("tool_registry unavailable; cannot remove agent")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("Missing 'name'")

        # Resolve the stored name (find_by_name normalizes; unregister matches exact).
        resolved = name
        if hasattr(tool_registry, "find_by_name"):
            match = tool_registry.find_by_name(name)
            if isinstance(match, dict) and match.get("name"):
                resolved = str(match["name"])

        removed = await tool_registry.unregister(resolved)
        if not removed:
            return ToolResult.fail(f"No registered agent named {name!r}")

        logger.info("Unregistered agent %r", resolved)
        return ToolResult.ok(
            {
                "removed": True,
                "name": resolved,
                "message": f"Removed agent '{resolved}' from the registry.",
            }
        )


__all__ = ["UnregisterAgentTool"]
