"""MCP runtime state holder and tool-call helpers for Progenitor."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool

from ..config import MCPConfig
from .jsonrpc import parse_mcp_jsonrpc_response
from .tool_schema import normalize_mcp_tool_definition

logger = logging.getLogger(__name__)


class MCPRuntime:
    """Owns live MCP server state, tool metadata, and LangChain wrappers."""

    def __init__(self, config: MCPConfig) -> None:
        self.config = config
        self.mcp_servers: dict[str, dict[str, Any]] = {}
        self.mcp_tools: list[dict[str, Any]] = []
        self.mcp_langchain_tools: list[BaseTool] = []
        self.mcp_tool_lookup: dict[str, dict[str, Any]] = {}

    def set_context(
        self,
        mcp_servers: dict[str, dict[str, Any]] | None,
        mcp_tools: list[dict[str, Any]] | None = None,
        mcp_langchain_tools: list[BaseTool] | None = None,
    ) -> None:
        """Replace the active MCP runtime context."""
        self.mcp_servers = mcp_servers or {}
        self.mcp_tools = mcp_tools or []
        self.mcp_langchain_tools = mcp_langchain_tools or []
        self.mcp_tool_lookup = {}
        for tool in self.mcp_tools:
            name = str(tool.get("name") or "").strip()
            if name:
                self.mcp_tool_lookup[name] = tool

    async def close(self) -> None:
        """Close all initialized MCP clients and clear the runtime context."""
        seen_client_ids: set[int] = set()
        for server in self.mcp_servers.values():
            client = server.get("client") if isinstance(server, dict) else None
            if not isinstance(client, httpx.AsyncClient):
                continue
            client_id = id(client)
            if client_id in seen_client_ids:
                continue
            seen_client_ids.add(client_id)
            await client.aclose()

        self.set_context({}, [], [])

    def get_tool_registry_entries(self) -> list[dict[str, Any]]:
        """Return MCP tools formatted for registry storage."""
        entries: list[dict[str, Any]] = []
        for tool in self.mcp_tools:
            name = tool.get("name")
            if not name:
                continue
            entries.append(
                {
                    "name": name,
                    "description": tool.get("description", "MCP tool"),
                    "type": "mcp_tool",
                    "source": tool.get("mcp_server_name") or "mcp",
                    "endpoint": tool.get("mcp_server_url"),
                    "transport": "http",
                    "capabilities": tool.get("capabilities") or [name],
                    "server_name": tool.get("mcp_server_name"),
                    "original_name": tool.get("mcp_original_name") or name,
                }
            )
        return entries

    def normalize_tool_definition(self, tool: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize a tool definition into OpenAI-compatible format."""
        if tool.get("type") == "function" and "function" in tool:
            return tool
        return normalize_mcp_tool_definition(tool)

    def iter_openai_tools(self) -> list[dict[str, Any]]:
        """Return the live MCP tool set in OpenAI tool-definition format."""
        normalized: list[dict[str, Any]] = []
        for tool in self.mcp_tools:
            definition = self.normalize_tool_definition(tool)
            if definition is not None:
                normalized.append(definition)
        return normalized

    def get_langchain_tools(self) -> list[BaseTool]:
        """Return the live MCP LangChain tool wrappers."""
        return [tool for tool in self.mcp_langchain_tools if isinstance(tool, BaseTool)]

    async def call_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool call against the resolved live MCP server."""
        name = (
            tool_call.get("name")
            or tool_call.get("tool_name")
            or tool_call.get("function", {}).get("name")
        )
        if not name:
            return {"error": "Tool call missing name"}

        if not self.config.enabled_servers and not self.mcp_tools and not self.mcp_tool_lookup:
            return {"error": "MCP integration disabled"}

        arguments = (
            tool_call.get("arguments")
            or tool_call.get("input")
            or tool_call.get("params")
            or tool_call.get("function", {}).get("arguments")
            or {}
        )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"raw": arguments}

        resolved_tool = self.mcp_tool_lookup.get(str(name))
        if resolved_tool is None:
            matches = [
                tool
                for tool in self.mcp_tools
                if str(tool.get("mcp_original_name") or tool.get("name") or "") == str(name)
            ]
            if len(matches) == 1:
                resolved_tool = matches[0]
            elif len(matches) > 1:
                return {"error": f"MCP tool name '{name}' is ambiguous across configured servers"}

        if resolved_tool is None:
            return {"error": f"MCP tool '{name}' is not registered"}

        server_name = str(resolved_tool.get("mcp_server_name") or "").strip()
        server_runtime = self.mcp_servers.get(server_name) if server_name else None
        if not isinstance(server_runtime, dict):
            return {"error": f"MCP server runtime unavailable for tool '{name}'"}

        mcp_client = server_runtime.get("client")
        endpoint = server_runtime.get("url")
        post = getattr(mcp_client, "post", None)
        if not callable(post) or not isinstance(endpoint, str):
            return {"error": f"MCP server '{server_name}' is not initialized"}
        post_typed = cast(Callable[..., Awaitable[httpx.Response]], post)

        target_name = str(resolved_tool.get("mcp_original_name") or name)
        logger.info("Calling MCP tool: %s", name)

        request = {
            "jsonrpc": "2.0",
            "id": tool_call.get("call_id")
            or tool_call.get("id")
            or tool_call.get("tool_call_id")
            or 1,
            "method": "tools/call",
            "params": {
                "name": target_name,
                "arguments": arguments,
            },
        }

        try:
            response = await post_typed(endpoint, json=request)
            response.raise_for_status()
            payload = parse_mcp_jsonrpc_response(response.text)
            if not isinstance(payload, dict):
                raise ValueError("Invalid MCP JSON-RPC response")
            if error := payload.get("error"):
                return {"error": error}
            return cast(dict[str, Any], payload.get("result", payload))
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("MCP tool call failed: %s", exc)
            return {"error": f"MCP call failed: {exc}"}
