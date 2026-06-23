"""MCP runtime context loading and refresh helpers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

import httpx
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection, StreamableHttpConnection

from ..config import MCPConfig, MCPServerConfig
from ..registry import normalize_tool_name
from ..utils import utcnow_isoformat
from .jsonrpc import parse_mcp_jsonrpc_response
from .tool_schema import get_mcp_tool_description, get_mcp_tool_name

logger = logging.getLogger(__name__)


async def fetch_mcp_server_catalog(
    server_config: MCPServerConfig,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    """Discover a server's tools. Returns (runtime_server|None, tools, error|None).

    ``error`` is a short human-readable reason when discovery fails (e.g.
    ``"401 Unauthorized"``), so callers can surface a failed state instead of
    silently dropping the server.
    """
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    headers.update(server_config.headers)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0), headers=headers)

    try:
        logger.info("Fetching MCP tools from %s", server_config.url)
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
        init_response = await client.post(server_config.url, json=init_request)
        init_response.raise_for_status()

        session_id = init_response.headers.get("mcp-session-id")
        if session_id:
            client.headers["mcp-session-id"] = session_id

        list_request = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        response = await client.post(server_config.url, json=list_request)
        response.raise_for_status()

        parsed = parse_mcp_jsonrpc_response(response.text) or {}
        result = parsed.get("result", parsed) if isinstance(parsed, dict) else {}
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return (
            {
                "name": server_config.name,
                "url": server_config.url,
                "transport": server_config.transport,
                "client": client,
            },
            cast(list[dict[str, Any]], tools),
            None,
        )
    except Exception as exc:
        logger.warning("Failed to initialize MCP server '%s': %s", server_config.name, exc)
        await client.aclose()
        if isinstance(exc, httpx.HTTPStatusError):
            reason = f"{exc.response.status_code} {exc.response.reason_phrase}".strip()
        else:
            reason = str(exc) or exc.__class__.__name__
        return None, [], reason


async def build_mcp_runtime_context(
    mcp_config: MCPConfig,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    checked_at = utcnow_isoformat()
    # Discover servers concurrently: latency is the slowest server's round-trip,
    # not the sum, and one slow/unreachable server no longer stalls the rest.
    catalogs = await asyncio.gather(
        *(fetch_mcp_server_catalog(sc) for sc in mcp_config.enabled_servers)
    )
    discovered_servers: list[tuple[MCPServerConfig, dict[str, Any], list[dict[str, Any]]]] = []
    server_status: dict[str, dict[str, Any]] = {}
    for server_config, (runtime_server, tools, error) in zip(
        mcp_config.enabled_servers, catalogs, strict=True
    ):
        if runtime_server is None:
            server_status[server_config.name] = {
                "status": "error",
                "error": error or "discovery failed",
                "tool_count": 0,
                "checked_at": checked_at,
            }
            continue
        discovered_servers.append((server_config, runtime_server, tools))
        server_status[server_config.name] = {
            "status": "connected",
            "error": None,
            "tool_count": len(tools),
            "checked_at": checked_at,
        }

    runtime_servers: dict[str, dict[str, Any]] = {}
    flattened_tools: list[dict[str, Any]] = []
    for server_config, runtime_server, tools in discovered_servers:
        runtime_servers[server_config.name] = runtime_server
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            original_name = get_mcp_tool_name(tool)
            if original_name is None:
                continue
            runtime_name = normalize_tool_name(f"{server_config.name}_{original_name}")
            flattened_tools.append(
                {
                    **tool,
                    "name": runtime_name,
                    "mcp_original_name": original_name,
                    "mcp_server_name": server_config.name,
                    "mcp_server_url": server_config.url,
                }
            )

    logger.info(
        "Initialized %d MCP server(s), loaded %d MCP tool(s)",
        len(runtime_servers),
        len(flattened_tools),
    )
    return runtime_servers, flattened_tools, server_status


def _build_adapter_connections(mcp_config: MCPConfig) -> dict[str, Connection]:
    return {
        server.name: StreamableHttpConnection(
            transport="streamable_http",
            url=server.url,
            headers=server.headers,
        )
        for server in mcp_config.enabled_servers
    }


def _build_mcp_tool_alias_lookup(
    mcp_tools: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    aliases: dict[tuple[str, str], dict[str, Any]] = {}
    for tool in mcp_tools:
        if not isinstance(tool, dict):
            continue
        server_name = str(tool.get("mcp_server_name") or "").strip()
        original_name = str(tool.get("mcp_original_name") or tool.get("name") or "").strip()
        if server_name and original_name:
            aliases[(server_name, original_name)] = tool
    return aliases


async def build_mcp_langchain_tool_wrappers(
    mcp_config: MCPConfig,
    mcp_tools: list[dict[str, Any]],
) -> list[BaseTool]:
    connections = _build_adapter_connections(mcp_config)
    if not connections:
        return []

    client = MultiServerMCPClient(connections)
    alias_lookup = _build_mcp_tool_alias_lookup(mcp_tools)

    async def _load_server_tools(server_name: str) -> list[BaseTool]:
        try:
            loaded_tools = await client.get_tools(server_name=server_name)
        except Exception as exc:
            logger.warning(
                "Failed to load LangChain MCP tools for server '%s': %s", server_name, exc
            )
            return []

        aliased_tools: list[BaseTool] = []
        for tool in loaded_tools:
            original_name = str(getattr(tool, "name", "") or "").strip()
            if not original_name:
                continue
            alias_tool = alias_lookup.get((server_name, original_name))
            if alias_tool is not None:
                alias_name = str(alias_tool.get("name") or original_name).strip()
                if alias_name:
                    tool.name = alias_name
                alias_description = get_mcp_tool_description(alias_tool)
                if alias_description:
                    tool.description = alias_description
            aliased_tools.append(tool)
        return aliased_tools

    loaded_tool_groups = await asyncio.gather(
        *[_load_server_tools(server.name) for server in mcp_config.enabled_servers]
    )
    flattened = [tool for tools in loaded_tool_groups for tool in tools]
    logger.info("Loaded %d LangChain MCP tool wrapper(s)", len(flattened))
    return flattened


async def reload_mcp_runtime_context(
    llm_client,
    tool_registry,
    mcp_config: MCPConfig,
    state_manager=None,
    *,
    reload_config: bool = False,
) -> None:
    from ..nodes.mcp import sync_mcp_runtime_context

    mcp_runtime = llm_client.mcp_runtime
    if mcp_runtime is None:
        raise RuntimeError("llm_client.mcp_runtime must be initialized before MCP refresh")
    mcp_runtime.config = mcp_config
    await sync_mcp_runtime_context(
        llm_client=llm_client,
        tool_registry=tool_registry,
        mcp_config=mcp_config,
        state_manager=state_manager,
        reload_config=reload_config,
    )
