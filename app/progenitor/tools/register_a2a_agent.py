"""Tool to register an A2A agent into the runtime registry (database-backed).

The A2A counterpart of ``register_acp_agent``: adds a remote HTTP A2A agent by URL
so it can be invoked via ``a2a_send`` or ``a2a_send_async``. Persists to the
registry store (DB), so a restart picks it up. (A2A agents can also arrive via
discovery or the backend ``/agents/register`` API; this exposes the same
capability as a system tool.)
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.progenitor.registry.models import AgentRegistryEntry

from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class RegisterA2AAgentArgs(BaseModel):
    """Arguments for registering an A2A agent."""

    name: str = Field(description="Unique agent name (used with a2a_send).")
    url: str = Field(description="Base URL of the A2A server, e.g. 'http://localhost:8107'.")
    description: str = Field(default="", description="What this agent does.")
    capabilities: list[str] = Field(
        default_factory=list, description="Capability tags used for routing."
    )
    headers: dict[str, str] | None = Field(
        default=None, description="Optional HTTP headers (e.g. auth) sent to the agent."
    )
    agent_card_url: str | None = Field(
        default=None,
        description="Agent-card URL; defaults to '<url>/.well-known/agent-card.json'.",
    )


class RegisterA2AAgentTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return RegisterA2AAgentArgs

    @property
    def name(self) -> str:
        return "register_a2a_agent"

    @property
    def description(self) -> str:
        return (
            "Register a remote A2A agent by URL into the registry so it can be invoked "
            "with a2a_send. Persists to the database; a restart keeps it."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        tool_registry = context.tool_registry
        if tool_registry is None or not hasattr(tool_registry, "register_agent"):
            return ToolResult.fail("tool_registry unavailable; cannot register agent")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("Missing 'name'")
        url = str(args.get("url") or "").strip().rstrip("/")
        if not url:
            return ToolResult.fail("Missing 'url'")

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ToolResult.fail(f"Invalid url: {url!r} (expected http(s)://host[:port])")

        capabilities = args.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []
        headers = args.get("headers")
        headers = (
            {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else None
        )
        agent_card_url = str(args.get("agent_card_url") or "").strip() or (
            f"{url}/.well-known/agent-card.json"
        )

        entry = AgentRegistryEntry(
            name=name,
            kind="a2a_agent",
            transport="http",
            description=str(args.get("description") or ""),
            capabilities=[str(c) for c in capabilities],
            url=url,
            endpoint=url,
            port=parsed.port,
            headers=headers,
            agent_card_url=agent_card_url,
        )
        await tool_registry.register_agent(entry)
        logger.info("Registered A2A agent %r (url=%s)", name, url)

        return ToolResult.ok(
            {
                "registered": True,
                "name": name,
                "transport": "http",
                "url": url,
                "agent_card_url": agent_card_url,
                "message": f"Registered A2A agent '{name}' ({url}).",
            }
        )


__all__ = ["RegisterA2AAgentTool"]
