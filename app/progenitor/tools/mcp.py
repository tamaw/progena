"""
MCP Client for Progenitor.

Handles communication with MCP servers via stdio or HTTP transports.
"""

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional


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

        # Parse command
        if isinstance(command, str):
            cmd_parts = command.split()
        else:
            cmd_parts = command

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
        except Exception:
            return False


class A2AClient:
    """Client for calling A2A agents."""

    async def call_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call an A2A agent with a request."""
        transport = agent_config.get("transport", "stdio")

        if transport == "stdio":
            return await self._call_stdio_agent(agent_config, request, context)
        elif transport == "http":
            return await self._call_http_agent(agent_config, request, context)
        else:
            raise ValueError(f"Unknown transport: {transport}")

    async def _call_stdio_agent(
        self,
        agent_config: dict[str, Any],
        request: str,
        context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Call an agent via stdio."""
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
            timeout=60
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
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    return await response.json()
        except Exception as e:
            return {"success": False, "error": str(e)}
