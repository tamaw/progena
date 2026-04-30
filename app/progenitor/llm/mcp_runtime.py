"""MCP runtime context and tool execution helpers."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool

from ..config import MCPConfig
from ..mcp_tool_utils import normalize_mcp_tool_definition, parse_mcp_jsonrpc_response

logger = logging.getLogger(__name__)


async def close_mcp_context(
    *,
    mcp_servers: dict[str, dict[str, Any]],
    mcp_client: httpx.AsyncClient | None,
) -> None:
    """Close all initialized MCP clients."""
    seen_client_ids: set[int] = set()
    for server in mcp_servers.values():
        client = server.get("client") if isinstance(server, dict) else None
        if not isinstance(client, httpx.AsyncClient):
            continue
        client_id = id(client)
        if client_id in seen_client_ids:
            continue
        seen_client_ids.add(client_id)
        await client.aclose()

    if mcp_client is not None and id(mcp_client) not in seen_client_ids:
        await mcp_client.aclose()


def configure_mcp_context(
    *,
    mcp_servers: dict[str, dict[str, Any]] | None,
    mcp_tools: list[dict[str, Any]] | None,
    mcp_langchain_tools: list[BaseTool] | None,
) -> tuple[
    dict[str, dict[str, Any]],
    httpx.AsyncClient | None,
    list[dict[str, Any]],
    list[BaseTool],
    dict[str, dict[str, Any]],
]:
    """Build MCP runtime fields from initialized MCP servers and tools."""
    servers = mcp_servers or {}
    first_server = next(iter(servers.values()), None)
    first_client = (
        first_server.get("client")
        if isinstance(first_server, dict)
        and isinstance(first_server.get("client"), httpx.AsyncClient)
        else None
    )
    tools = mcp_tools or []
    tool_lookup: dict[str, dict[str, Any]] = {}
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if name:
            tool_lookup[name] = tool

    return (
        servers,
        cast(httpx.AsyncClient | None, first_client),
        tools,
        mcp_langchain_tools or [],
        tool_lookup,
    )


def get_mcp_tool_registry_entries(mcp_tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return MCP tools formatted for tool registry storage."""
    entries: list[dict[str, Any]] = []
    for tool in mcp_tools:
        name = tool.get("name")
        if not name:
            continue
        entry = {
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
        entries.append(entry)
    return entries


def merge_tools(
    *,
    mcp_tools: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge caller-supplied tools with MCP tools, de-duplicated by name."""
    if tools is not None and len(tools) == 0:
        return []

    merged: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    for tool in tools or []:
        name = (
            tool.get("function", {}).get("name")
            if tool.get("type") == "function"
            else tool.get("name")
        )
        if name:
            seen_names.add(name)
        merged.append(tool)

    for tool in mcp_tools:
        normalized = normalize_mcp_tool_definition(tool)
        if not normalized:
            continue
        name = normalized.get("function", {}).get("name")
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        merged.append(normalized)

    return merged


def resolve_request_tools(
    *,
    mcp_tools: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    response_format: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Resolve effective tools for a Responses API request."""
    if response_format is not None and tools is None:
        return merge_tools(mcp_tools=mcp_tools, tools=None)
    return merge_tools(mcp_tools=mcp_tools, tools=tools)


async def call_mcp_tool(
    *,
    mcp_config: MCPConfig,
    mcp_servers: dict[str, dict[str, Any]],
    mcp_tools: list[dict[str, Any]],
    mcp_tool_lookup: dict[str, dict[str, Any]],
    tool_call: dict[str, Any],
) -> dict[str, Any]:
    """Execute a tool call against the correct MCP server for that tool."""
    name = (
        tool_call.get("name")
        or tool_call.get("tool_name")
        or tool_call.get("function", {}).get("name")
    )
    if not name:
        return {"error": "Tool call missing name"}

    if not mcp_config.enabled_servers:
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

    resolved_tool = mcp_tool_lookup.get(str(name))
    if resolved_tool is None:
        matches = [
            tool
            for tool in mcp_tools
            if str(tool.get("mcp_original_name") or tool.get("name") or "") == str(name)
        ]
        if len(matches) == 1:
            resolved_tool = matches[0]
        elif len(matches) > 1:
            return {"error": f"MCP tool name '{name}' is ambiguous across configured servers"}

    if resolved_tool is None:
        return {"error": f"MCP tool '{name}' is not registered"}

    server_name = str(resolved_tool.get("mcp_server_name") or "").strip()
    server_runtime = mcp_servers.get(server_name) if server_name else None
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
        "id": tool_call.get("call_id") or tool_call.get("id") or tool_call.get("tool_call_id") or 1,
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
