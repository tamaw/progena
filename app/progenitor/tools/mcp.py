"""MCP client and runtime wiring for Progenitor."""

import asyncio
import json
import logging
import shlex
from pathlib import Path
from typing import Any, cast

from ..config import MCPConfig
from ..mcp.jsonrpc import parse_mcp_jsonrpc_response
from ..mcp.tool_schema import get_mcp_tool_description
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


ALLOWED_COMMAND_PREFIXES = (
    "python",
    "node",
    "npx",
    "uvx",
    "deno",
)


class MCPTool(BaseSystemTool):
    """Expose MCP integration status and discovered tool list."""

    @property
    def name(self) -> str:
        return "mcp"

    @property
    def description(self) -> str:
        return "Show MCP integration status and list discovered MCP tools"

    @property
    def keywords(self) -> list[str]:
        return []

    async def execute(self, context: ToolContext) -> ToolResult:
        llm_client = context.llm_client
        mcp_config = getattr(llm_client, "mcp_config", None)
        mcp_runtime = getattr(llm_client, "mcp_runtime", None)
        mcp_tools = mcp_runtime.mcp_tools if mcp_runtime is not None else []
        runtime_servers = mcp_runtime.mcp_servers if mcp_runtime is not None else {}

        configured_servers = []
        if isinstance(mcp_config, MCPConfig):
            configured_servers = [
                {
                    "name": server.name,
                    "url": server.url,
                    "transport": server.transport,
                    "enabled": server.enabled,
                }
                for server in mcp_config.enabled_servers
            ]

        return ToolResult.ok(
            {
                "enabled": bool(configured_servers),
                "server_count": len(configured_servers),
                "servers": configured_servers,
                "connected_server_count": len(runtime_servers),
                "tool_count": len(mcp_tools),
                "tools": [
                    {
                        "name": tool.get("name"),
                        "description": get_mcp_tool_description(tool),
                        "server": tool.get("mcp_server_name"),
                    }
                    for tool in mcp_tools
                    if isinstance(tool, dict)
                ],
            }
        )


def _validate_command(command: str | list[str]) -> list[str]:
    """Safely parse and validate a command for execution."""
    if isinstance(command, list):
        cmd_parts = command
    else:
        try:
            cmd_parts = shlex.split(command)
        except ValueError as exc:
            raise ValueError(f"Invalid command syntax: {exc}") from exc

    if not cmd_parts:
        raise ValueError("Empty command")

    executable = cmd_parts[0].lower()
    executable_name = Path(executable).name.lower()
    if executable_name.endswith(".exe"):
        executable_name = executable_name[:-4]

    if not any(executable_name.startswith(prefix) for prefix in ALLOWED_COMMAND_PREFIXES):
        logger.warning(
            "Command '%s' not in allowlist. Allowed prefixes: %s",
            executable_name,
            ALLOWED_COMMAND_PREFIXES,
        )
        raise ValueError(
            f"Command '{executable_name}' not allowed. "
            f"Allowed: {', '.join(ALLOWED_COMMAND_PREFIXES)}"
        )

    return cmd_parts


class MCPClient:
    """Client for calling MCP servers/tools."""

    def __init__(self, project_root: Path | None = None):
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self.project_root = Path(project_root) if project_root else Path.cwd()

    async def call_tool(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP tool method."""
        transport = tool_config.get("transport", "stdio")

        if transport == "stdio":
            return await self._call_stdio(tool_config, method, params)
        if transport == "http":
            return await self._call_http(tool_config, method, params)
        raise ValueError(f"Unknown transport: {transport}")

    async def _call_stdio(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP server via stdio."""
        command = tool_config.get("command")
        if not command:
            raise ValueError("No command specified for stdio transport")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        cmd_parts = _validate_command(command)

        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        request_bytes = json.dumps(request).encode() + b"\n"
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=request_bytes),
            timeout=float(tool_config.get("timeout", 30)),
        )

        if process.returncode != 0:
            return {
                "error": f"MCP server exited with code {process.returncode}",
                "stderr": stderr.decode() if stderr else None,
            }

        try:
            response = json.loads(stdout.decode())
            return cast(dict[str, Any], response.get("result", response))
        except json.JSONDecodeError as exc:
            return {
                "error": f"Invalid JSON response: {exc}",
                "stdout": stdout.decode() if stdout else None,
            }

    async def _call_http(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call an MCP server via HTTP."""
        import aiohttp

        endpoint = tool_config.get("endpoint")
        if not endpoint:
            raise ValueError("No URL specified for HTTP transport")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        headers = {"Content-Type": "application/json"}
        headers.update(tool_config.get("headers", {}))

        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    endpoint,
                    json=request,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response,
            ):
                parsed = parse_mcp_jsonrpc_response(await response.text())
                if not isinstance(parsed, dict):
                    raise ValueError("Invalid MCP JSON-RPC response")
                return cast(dict[str, Any], parsed.get("result", parsed))
        except Exception as exc:
            return {"error": str(exc)}

    async def list_tools(self, tool_config: dict[str, Any]) -> list[dict[str, Any]]:
        """List available tools from an MCP server."""
        result = await self.call_tool(tool_config, "tools/list")
        return cast(
            list[dict[str, Any]], result.get("tools", []) if isinstance(result, dict) else []
        )

    async def healthcheck(self, tool_config: dict[str, Any]) -> bool:
        """Check if an MCP server is healthy."""
        try:
            result = await self.call_tool(tool_config, "ping")
            return not isinstance(result, dict) or "error" not in result
        except (TimeoutError, OSError, ValueError) as exc:
            logger.debug("MCP healthcheck failed: %s", exc)
            return False
