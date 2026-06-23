"""Tool to add a remote HTTP MCP server into the shared MCP registry.

The Tools page "+ Add" button opens a quick-chat that asks Progenitor to connect
a new MCP server; this is the system tool that actually persists it. It writes
the server into the shared ``mcp`` state document (so a restart keeps it) and
reloads the live MCP runtime context so the server's tools are discovered
immediately — the same discovery path used at startup and by ``mcp_refresh``.

Only remote HTTP MCP servers are supported (matching ``load_mcp_servers``).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..config import MCPConfig
from ..mcp import reload_mcp_runtime_context
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class AddMCPServerArgs(BaseModel):
    """Arguments for adding a remote HTTP MCP server."""

    name: str = Field(description="Unique server name (the key it is stored under).")
    url: str = Field(description="HTTP(S) endpoint of the MCP server, e.g. 'https://host/mcp'.")
    description: str = Field(default="", description="What this server provides.")
    headers: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional HTTP headers sent to the server (e.g. auth). Values may use "
            "$VAR / ${VAR} placeholders resolved from the runtime environment."
        ),
    )
    enabled: bool = Field(default=True, description="Whether the server starts enabled.")


class AddMCPServerTool(BaseSystemTool):
    @property
    def args_model(self) -> type[BaseModel]:
        return AddMCPServerArgs

    @property
    def name(self) -> str:
        return "add_mcp_server"

    @property
    def description(self) -> str:
        return (
            "Add a remote HTTP MCP server to the shared MCP registry by name and url, "
            "persist it, and discover its tools immediately. Restart keeps it."
        )

    @property
    def keywords(self) -> list[str]:
        return [
            "add mcp server",
            "connect mcp server",
            "register mcp server",
            "new mcp server",
        ]

    async def execute(self, context: ToolContext) -> ToolResult:
        state_manager = context.state_manager
        if state_manager is None or not hasattr(state_manager, "save_mcp_config"):
            return ToolResult.fail("state_manager unavailable; cannot persist MCP server")

        llm_client = context.llm_client
        if llm_client is None:
            return ToolResult.fail("llm_client unavailable; cannot load MCP runtime")

        args = context.tool_args if isinstance(context.tool_args, dict) else {}

        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("Missing 'name'")

        url = str(args.get("url") or "").strip()
        if not url:
            return ToolResult.fail("Missing 'url'")

        # Validate the literal URL only when it carries no env-var placeholders;
        # templated URLs (e.g. https://$HOST/mcp) are resolved at load time.
        if "$" not in url:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                return ToolResult.fail(f"Invalid url: {url!r} (expected http(s)://host[:port])")

        headers = args.get("headers")
        normalized_headers = (
            {str(key): str(value) for key, value in headers.items()}
            if isinstance(headers, dict)
            else {}
        )

        payload = await state_manager.get_mcp_config()
        servers = payload.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
            payload["mcpServers"] = servers

        already_present = name in servers
        server_entry: dict[str, object] = {
            "url": url,
            "transport": "http",
            "enabled": bool(args.get("enabled", True)),
            "description": str(args.get("description") or "").strip(),
        }
        if normalized_headers:
            server_entry["headers"] = normalized_headers
        servers[name] = server_entry

        await state_manager.save_mcp_config(payload)

        # Re-read the saved config and rediscover tools so the new server is live
        # without a restart (reload_config=True re-reads from the state manager).
        try:
            await reload_mcp_runtime_context(
                llm_client,
                context.tool_registry,
                MCPConfig.from_payload(payload),
                state_manager=state_manager,
                reload_config=True,
            )
        except Exception as exc:  # noqa: BLE001 - surface discovery failures to the caller
            logger.warning("Saved MCP server %r but live reload failed: %s", name, exc)
            return ToolResult.ok(
                {
                    "added": True,
                    "updated": already_present,
                    "name": name,
                    "url": url,
                    "enabled": server_entry["enabled"],
                    "reloaded": False,
                    "message": (
                        f"Saved MCP server '{name}' ({url}), but live tool discovery "
                        f"failed: {exc}. It will be retried on the next MCP refresh."
                    ),
                }
            )

        mcp_runtime = getattr(llm_client, "mcp_runtime", None)
        tool_count = len(mcp_runtime.mcp_tools) if mcp_runtime is not None else 0
        verb = "Updated" if already_present else "Connected"
        logger.info("%s MCP server %r (url=%s)", verb, name, url)

        return ToolResult.ok(
            {
                "added": True,
                "updated": already_present,
                "name": name,
                "url": url,
                "enabled": server_entry["enabled"],
                "reloaded": True,
                "tool_count": tool_count,
                "message": f"{verb} MCP server '{name}' ({url}).",
            }
        )


__all__ = ["AddMCPServerTool"]
