"""Tool to register an ACP agent into the runtime registry.

ACP agents (Agent Client Protocol — local stdio CLIs such as ``copilot --acp``)
live in the same registry as A2A agents, so once registered they are invocable
through ``call_agent`` and visible on the Agents page. This tool writes an
``acp_agent`` entry; nothing is registered automatically — every ACP agent
(including Copilot) must be added explicitly via this tool.
"""

from __future__ import annotations

import logging
import shutil
from typing import Any

from pydantic import BaseModel, Field

from ..acp.agents import _profile_for_command, parse_acp_connection
from ..state.models import ACPAgentConfig, AgentRegistryEntry
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class RegisterACPAgentArgs(BaseModel):
    """Arguments for registering an ACP agent."""

    name: str = Field(description="Unique agent name (used to invoke it via call_agent).")
    connection: str | None = Field(
        default=None,
        description=(
            "Connection string; the simplest way to add an agent. "
            "e.g. 'tcp://192.168.0.2:3000?model=claude-haiku-4.5&cwd=/tmp' or 'stdio://copilot'. "
            "Explicit fields below override anything derived from it."
        ),
    )
    command: str = Field(
        default="copilot",
        description="CLI command that speaks ACP over stdio (e.g. 'copilot'). Ignored in TCP mode.",
    )
    description: str = Field(default="", description="What this agent does.")
    profile: str | None = Field(
        default=None,
        description="Known agent profile for flag building (e.g. 'copilot'); omit for generic.",
    )
    model: str | None = Field(
        default=None,
        description="Model id (e.g. 'claude-haiku-4.5'); set per session in TCP mode.",
    )
    host: str | None = Field(
        default=None,
        description="TCP mode: host of an already-running ACP server (e.g. '192.168.0.2').",
    )
    port: int | None = Field(
        default=None,
        description="TCP mode: port of the ACP server (e.g. 3000). Omit for local stdio.",
    )
    cwd: str | None = Field(
        default=None,
        description="TCP mode: server-side working directory passed to the session.",
    )
    timeout_seconds: int = Field(default=1200, description="Per-run timeout in seconds.")
    capabilities: list[str] = Field(
        default_factory=list, description="Capability tags used for routing."
    )


class RegisterACPAgentTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return RegisterACPAgentArgs

    @property
    def name(self) -> str:
        return "register_acp_agent"

    @property
    def description(self) -> str:
        return (
            "Register a local ACP agent (Agent Client Protocol CLI, e.g. GitHub Copilot) "
            "into the registry so it can be invoked with call_agent. Provide the command "
            "and an optional profile/model. The agent is sandboxed per run."
        )

    async def execute(self, context: ToolContext) -> ToolResult:
        tool_registry = context.tool_registry
        if tool_registry is None or not hasattr(tool_registry, "register_agent"):
            return ToolResult.fail("tool_registry unavailable; cannot register ACP agent")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("Missing 'name'")

        # Start from a connection string when given, then apply explicit overrides.
        cfg_kwargs: dict[str, Any] = {}
        if args.get("connection"):
            try:
                cfg_kwargs = parse_acp_connection(str(args["connection"]))
            except ValueError as exc:
                return ToolResult.fail(f"Invalid connection string: {exc}")

        def _override(key: str, transform=lambda v: v) -> None:
            if args.get(key) not in (None, ""):
                cfg_kwargs[key] = transform(args[key])

        _override("command", lambda v: str(v).strip())
        _override("profile", lambda v: str(v).strip())
        _override("model", lambda v: str(v).strip())
        _override("host", lambda v: str(v).strip())
        _override("cwd", lambda v: str(v).strip())
        if args.get("port") not in (None, ""):
            try:
                cfg_kwargs["port"] = int(str(args["port"]))
            except ValueError:
                return ToolResult.fail(f"Invalid port: {args['port']!r}")
        if args.get("timeout_seconds") not in (None, ""):
            try:
                cfg_kwargs["timeout_seconds"] = int(str(args["timeout_seconds"]))
            except ValueError:
                return ToolResult.fail(f"Invalid timeout_seconds: {args['timeout_seconds']!r}")

        # When the caller didn't pin a profile (directly or via connection string),
        # derive it from the resolved command so overriding `command` to a non-copilot
        # CLI doesn't silently inherit Copilot-specific flags.
        if not args.get("profile") and "profile" not in cfg_kwargs:
            cfg_kwargs["profile"] = _profile_for_command(cfg_kwargs.get("command", "copilot"))

        capabilities = args.get("capabilities")
        if not isinstance(capabilities, list):
            capabilities = []

        acp_config = ACPAgentConfig(**cfg_kwargs)
        entry = AgentRegistryEntry(
            name=name,
            kind="acp_agent",
            transport="acp",
            description=str(args.get("description") or ""),
            capabilities=[str(c) for c in capabilities],
            acp=acp_config,
        )
        await tool_registry.register_agent(entry)
        command = acp_config.command
        port = acp_config.port

        if port is not None:
            host = acp_config.host
            endpoint = f"{host or '127.0.0.1'}:{port}"
            logger.info("Registered ACP agent %r (tcp=%s)", name, endpoint)
            return ToolResult.ok(
                {
                    "registered": True,
                    "name": name,
                    "transport": "acp",
                    "mode": "tcp",
                    "endpoint": endpoint,
                    "message": f"Registered ACP agent '{name}' (TCP {endpoint}).",
                }
            )

        available = shutil.which(command) is not None
        logger.info("Registered ACP agent %r (command=%r, available=%s)", name, command, available)
        return ToolResult.ok(
            {
                "registered": True,
                "name": name,
                "command": command,
                "transport": "acp",
                "mode": "stdio",
                "command_available": available,
                "message": (
                    f"Registered ACP agent '{name}'."
                    if available
                    else f"Registered ACP agent '{name}', but command {command!r} "
                    "was not found on PATH; install it before invoking."
                ),
            }
        )


__all__ = ["RegisterACPAgentTool"]
