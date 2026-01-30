"""
MCP Client for Progenitor.

Handles communication with MCP servers via stdio or HTTP transports.
"""

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Allowlist of safe command prefixes for MCP servers
# Commands must start with one of these to be executed
ALLOWED_COMMAND_PREFIXES = (
    "python",
    "node",
    "npx",
    "uvx",
    "deno",
)


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

    def __init__(self):
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def call_tool(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: Optional[dict[str, Any]] = None,
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
        params: Optional[dict[str, Any]] = None,
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

        # Run the MCP server
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
            timeout=30
        )

        if process.returncode != 0:
            return {
                "error": f"MCP server exited with code {process.returncode}",
                "stderr": stderr.decode() if stderr else None,
            }

        # Parse response
        try:
            response = json.loads(stdout.decode())
            return response.get("result", response)
        except json.JSONDecodeError as e:
            return {
                "error": f"Invalid JSON response: {e}",
                "stdout": stdout.decode() if stdout else None,
            }

    async def _call_http(
        self,
        tool_config: dict[str, Any],
        method: str,
        params: Optional[dict[str, Any]] = None,
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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json=request,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    result = await response.json()
                    return result.get("result", result)
        except Exception as e:
            return {"error": str(e)}

    async def list_tools(self, tool_config: dict[str, Any]) -> list[dict[str, Any]]:
        """List available tools from an MCP server."""
        result = await self.call_tool(tool_config, "tools/list")
        return result.get("tools", []) if isinstance(result, dict) else []

    async def healthcheck(self, tool_config: dict[str, Any]) -> bool:
        """Check if an MCP server is healthy."""
        try:
            result = await self.call_tool(tool_config, "ping")
            return not isinstance(result, dict) or "error" not in result
        except (OSError, asyncio.TimeoutError, ValueError) as e:
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
        context: Optional[dict[str, Any]] = None,
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

        if transport == "http":
            # For HTTP, check if endpoint is available
            endpoint = agent_config.get("endpoint")
            if not endpoint:
                # Fall back to stdio if no endpoint configured
                if agent_config.get("entrypoint"):
                    logger.info("No HTTP endpoint for agent, falling back to stdio")
                    return await self._call_stdio_agent(agent_config, request, context, timeout)
                return {"success": False, "error": "No endpoint or entrypoint specified"}
            return await self._call_http_agent(agent_config, request, context, timeout)
        elif transport == "stdio":
            return await self._call_stdio_agent(agent_config, request, context, timeout)
        else:
            raise ValueError(f"Unknown transport: {transport}")

    async def _call_stdio_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: Optional[dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Call an agent via stdio (legacy support)."""
        entrypoint = agent_config.get("entrypoint")
        if not entrypoint:
            raise ValueError("No entrypoint specified for agent")

        # Build input payload
        payload = {
            "request": request,
            "context": context or {},
        }

        # Run the agent
        process = await asyncio.create_subprocess_exec(
            "python", entrypoint,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        input_bytes = json.dumps(payload).encode() + b"\n"
        stdout, stderr = await asyncio.wait_for(
            process.communicate(input=input_bytes),
            timeout=timeout
        )

        if process.returncode != 0:
            return {
                "success": False,
                "error": f"Agent exited with code {process.returncode}",
                "stderr": stderr.decode() if stderr else None,
            }

        try:
            return json.loads(stdout.decode())
        except json.JSONDecodeError:
            return {
                "success": True,
                "response": stdout.decode(),
            }

    async def _call_http_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: Optional[dict[str, Any]] = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Call an agent via HTTP."""
        import aiohttp

        endpoint = agent_config.get("endpoint")
        if not endpoint:
            raise ValueError("No endpoint specified for HTTP agent")

        payload = {
            "request": request,
            "context": context or {},
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    endpoint,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status >= 400:
                        text = await response.text()
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {text[:200]}",
                        }
                    return await response.json()
        except aiohttp.ClientConnectorError as e:
            return {
                "success": False,
                "error": f"Connection failed: {e}. Agent may not be running.",
            }
        except asyncio.TimeoutError:
            return {
                "success": False,
                "error": f"Request timeout after {timeout}s",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_agent_card(
        self,
        agent_config: dict[str, Any],
        timeout: float = 5.0,
    ) -> Optional[dict[str, Any]]:
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
                agent_card_url = f"{endpoint}/.well-known/agent.json"
            else:
                return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    agent_card_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as response:
                    if response.status == 200:
                        return await response.json()
        except Exception as e:
            logger.debug("Failed to fetch agent card: %s", e)

        return None
