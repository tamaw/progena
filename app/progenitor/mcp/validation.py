"""Validation capabilities."""

from __future__ import annotations

import json

from app.progenitor.documents.catalog import check_entry_schema
from app.progenitor.documents.values import JsonDoc


def validate_mcp_document(content: str) -> JsonDoc:
    """Parse + validate an ``mcp.json`` payload (remote HTTP servers only)."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("top level must be a JSON object")
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError("missing 'mcpServers' object")
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"server '{name}' must be an object")
        check_entry_schema(f"server '{name}'", cfg, "mcp_server_entry")
        url = str(cfg.get("url") or cfg.get("endpoint") or "").strip()
        if not url:
            raise ValueError(f"server '{name}' is missing 'url'")
        transport = str(cfg.get("transport") or "http").strip().lower()
        if transport != "http":
            raise ValueError(
                f"server '{name}' transport '{transport}' is unsupported (remote HTTP only)"
            )
    return data


def _validate_mcp_tools_document(content: str) -> JsonDoc:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        raise ValueError('expected {"tools": [<mcp_tool_entry>, ...]}')
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise ValueError(f"tool {index}: expected an object")
        check_entry_schema(f"tool '{tool.get('name', index)}'", tool, "mcp_tool_entry")
    return data
