"""
MCP Client for Progenitor.

Handles communication with MCP servers via stdio or HTTP transports.
"""

import asyncio
import json
import logging
import shlex
import uuid
from pathlib import Path
from typing import Any, cast

import httpx

from ..config import MCPConfig
from .protocol import BaseSystemTool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


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
        mcp_tools = getattr(llm_client, "mcp_tools", None) or []

        return ToolResult.ok({
            "enabled": bool(getattr(mcp_config, "enabled", False)) if mcp_config else False,
            "base_url": getattr(mcp_config, "base_url", None) if mcp_config else None,
            "tool_count": len(mcp_tools),
            "tools": [
                {
                    "name": t.get("name"),
                    "description": t.get("description") or t.get("summary"),
                }
                for t in mcp_tools
                if isinstance(t, dict)
            ],
        })

# Allowlist of safe command prefixes for MCP servers
# Commands must start with one of these to be executed
ALLOWED_COMMAND_PREFIXES = (
    "python",
    "node",
    "npx",
    "uvx",
    "deno",
)


async def initialize_mcp_tools(
    mcp_config: MCPConfig,
) -> tuple[httpx.AsyncClient | None, list[dict[str, Any]]]:
    """Initialize MCP tools by calling tools/list on the MCP server."""
    if not mcp_config.enabled:
        logger.info("MCP integration disabled")
        return None, []

    def _parse_jsonrpc_response(text: str) -> dict[str, Any] | None:
        """Parse either JSON or SSE('data: {json}') JSON-RPC responses."""
        raw = (text or "").strip()
        if not raw:
            return None

        # Plain JSON
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                return cast(dict[str, Any], data) if isinstance(data, dict) else None
            except Exception:
                return None

        # SSE: find first data: line
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
                return cast(dict[str, Any], data) if isinstance(data, dict) else None
            except Exception:
                return None

        return None

    try:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if mcp_config.api_key:
            headers["Authorization"] = f"Bearer {mcp_config.api_key}"

        mcp_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            headers=headers,
        )

        logger.info("Fetching MCP tools from %s", mcp_config.base_url)

        # Initialize to obtain an optional mcp-session-id.
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "progenitor", "version": "0.0.0"},
            },
        }
        init_response = await mcp_client.post(mcp_config.base_url, json=init_request)
        init_response.raise_for_status()

        session_id = init_response.headers.get("mcp-session-id")
        if session_id:
            mcp_client.headers["mcp-session-id"] = session_id

        # Now list tools.
        list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        }
        response = await mcp_client.post(mcp_config.base_url, json=list_request)
        response.raise_for_status()

        parsed = _parse_jsonrpc_response(response.text) or {}
        result = parsed.get("result", parsed) if isinstance(parsed, dict) else {}

        tools = result.get("tools", []) if isinstance(result, dict) else []
        logger.info("Loaded %d MCP tools", len(tools))
        return mcp_client, tools
    except Exception as e:
        logger.warning("Failed to initialize MCP tools: %s", e)
        return None, []


async def setup_mcp_for_llm_client(
    llm_client,
    mcp_config: MCPConfig,
) -> None:
    """Initialize MCP tools and attach them to the LLM client."""
    mcp_client, mcp_tools = await initialize_mcp_tools(mcp_config)
    llm_client.set_mcp_context(mcp_client, mcp_tools)


def _validate_command(command: str | list[str]) -> list[str]:
    """
    Safely parse and validate a command for execution.

    Args:
        command: Command string or list of command parts

    Returns:
        List of command parts, safely parsed

    Raises:
        ValueError: If command is invalid or not in allowlist
    """
    if isinstance(command, list):
        cmd_parts = command
    else:
        # Use shlex.split for proper shell escaping (prevents injection)
        try:
            cmd_parts = shlex.split(command)
        except ValueError as e:
            raise ValueError(f"Invalid command syntax: {e}") from e

    if not cmd_parts:
        raise ValueError("Empty command")

    # Validate command against allowlist
    executable = cmd_parts[0].lower()
    # Extract just the executable name (handle paths like /usr/bin/python)
    executable_name = Path(executable).name.lower()
    # Remove common extensions
    if executable_name.endswith('.exe'):
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
        elif transport == "http":
            return await self._call_http(tool_config, method, params)
        else:
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

        # Build the JSON-RPC request
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        # Safely parse and validate command (prevents command injection)
        cmd_parts = _validate_command(command)

        # Start MCP server process directly (no container isolation)
        process = await asyncio.create_subprocess_exec(
            *cmd_parts,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Send request and get response
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

        # Parse response
        try:
            response = json.loads(stdout.decode())
            return cast(dict[str, Any], response.get("result", response))
        except json.JSONDecodeError as e:
            return {
                "error": f"Invalid JSON response: {e}",
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
            raise ValueError("No endpoint specified for HTTP transport")

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params or {},
        }

        headers = {"Content-Type": "application/json"}
        headers.update(tool_config.get("headers", {}))
        if "Authorization" not in headers:
            mcp_config = MCPConfig.from_env()
            if mcp_config.api_key:
                headers["Authorization"] = f"Bearer {mcp_config.api_key}"

        try:
            async with aiohttp.ClientSession() as session, session.post(
                endpoint,
                json=request,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                result = await response.json()
                return cast(dict[str, Any], result.get("result", result))
        except Exception as e:
            return {"error": str(e)}

    async def list_tools(self, tool_config: dict[str, Any]) -> list[dict[str, Any]]:
        """List available tools from an MCP server."""
        result = await self.call_tool(tool_config, "tools/list")
        return cast(list[dict[str, Any]], result.get("tools", []) if isinstance(result, dict) else [])

    async def healthcheck(self, tool_config: dict[str, Any]) -> bool:
        """Check if an MCP server is healthy."""
        try:
            result = await self.call_tool(tool_config, "ping")
            return not isinstance(result, dict) or "error" not in result
        except (TimeoutError, OSError, ValueError) as e:
            logger.debug("MCP healthcheck failed: %s", e)
            return False


class A2AClient:
    """Client for calling A2A agents.

    Supports both HTTP (preferred) and stdio transports.
    HTTP agents should expose endpoints at their registered URL.
    """

    async def call_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Call an A2A agent with a request.

        Args:
            agent_config: Agent configuration from registry.
            request: Request message.
            context: Additional context.
            timeout: Request timeout in seconds.

        Returns:
            Response dict with success/error fields.
        """
        transport = agent_config.get("transport", "http")  # Default to HTTP

        # Check agent status
        status = agent_config.get("status", "stopped")
        if status == "failed":
            return {
                "success": False,
                "error": f"Agent is in failed state: {agent_config.get('last_error', 'Unknown error')}",
            }

        if transport != "http":
            return {"success": False, "error": f"Unsupported transport: {transport}. Only 'http' is supported."}
        endpoint = agent_config.get("endpoint")
        if not endpoint:
            return {"success": False, "error": "No HTTP endpoint configured for agent"}
        return await self._call_http_agent(agent_config, request, context, timeout)

    async def _call_http_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: dict[str, Any] | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Call an A2A agent via JSON-RPC (A2A protocol v0.3.x)."""
        endpoint = agent_config.get("endpoint")
        if not endpoint:
            raise ValueError("No endpoint specified for HTTP agent")

        try:
            async with httpx.AsyncClient(timeout=timeout) as httpx_client:
                message_id = uuid.uuid4().hex
                payload = {
                    "jsonrpc": "2.0",
                    "id": uuid.uuid4().hex,
                    "method": "message/send",
                    "params": {
                        "message": {
                            "kind": "message",
                            "message_id": message_id,
                            "role": "user",
                            "parts": [{"kind": "text", "text": request or ""}],
                        },
                        "metadata": context or {},
                    },
                }
                resp = await httpx_client.post(str(endpoint), json=payload)
        except TimeoutError:
            return {"success": False, "error": f"Request timeout after {timeout}s"}
        except Exception as e:
            return {"success": False, "error": str(e)}

        if resp.status_code >= 400:
            return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}

        try:
            data = resp.json()
        except Exception:
            return {"success": False, "error": f"Non-JSON A2A response: {resp.text[:200]}"}

        if isinstance(data, dict) and data.get("error"):
            err = data.get("error")
            if isinstance(err, dict):
                return {"success": False, "error": err.get("message") or str(err)}
            return {"success": False, "error": str(err)}

        if not isinstance(data, dict) or "result" not in data:
            return {"success": False, "error": f"Invalid A2A response: {str(data)[:200]}"}

        return {"success": True, "result": data.get("result")}

    async def get_agent_card(
        self,
        agent_config: dict[str, Any],
        timeout: float = 5.0,
    ) -> dict[str, Any] | None:
        """Fetch the agent card from an HTTP agent.

        Args:
            agent_config: Agent configuration from registry.
            timeout: Request timeout.

        Returns:
            Agent card dict or None if unavailable.
        """
        import aiohttp

        agent_card_url = agent_config.get("agent_card_url")
        if not agent_card_url:
            endpoint = agent_config.get("endpoint")
            if endpoint:
                agent_card_url = f"{endpoint}/.well-known/agent-card.json"
            else:
                return None

        try:
            async with aiohttp.ClientSession() as session, session.get(
                agent_card_url,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status == 200:
                    return cast(dict[str, Any], await response.json())
        except Exception as e:
            logger.debug("Failed to fetch agent card: %s", e)

        return None
