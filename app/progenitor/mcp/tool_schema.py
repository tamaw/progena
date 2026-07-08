"""Helpers for normalizing MCP tool metadata and schemas."""

from __future__ import annotations

from typing import Any, cast


def get_mcp_tool_name(tool: dict[str, Any]) -> str | None:
    """Return a normalized MCP tool name, or None when unavailable."""
    raw_name = tool.get("name")
    if raw_name is None:
        return None
    name = str(raw_name).strip()
    return name or None


def get_mcp_tool_description(tool: dict[str, Any]) -> str:
    """Return an MCP tool description."""
    raw_description = tool.get("description")
    return str(raw_description).strip() if raw_description is not None else ""


def get_mcp_tool_parameters(
    tool: dict[str, Any],
    *,
    require_object: bool = False,
) -> dict[str, Any] | None:
    """Return an MCP tool's standard input schema when present."""
    raw_parameters = tool.get("inputSchema")
    if not isinstance(raw_parameters, dict):
        return None

    parameters = cast(dict[str, Any], raw_parameters)
    if require_object and (
        parameters.get("type") != "object" and not isinstance(parameters.get("properties"), dict)
    ):
        return None
    return parameters


def normalize_mcp_tool_definition(tool: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize an MCP tool into the Responses API's flat function-tool format.

    The Responses API wants {"type": "function", "name": ..., "parameters": ...}
    at the top level, unlike the older Chat Completions API's nested
    {"type": "function", "function": {"name": ...}}. Both callers of this
    (app/progenitor/llm/client.py and app/agents/builder_agent/llm/client.py)
    send the result straight to a /responses endpoint, so the nested shape
    used to fail upstream with "Missing required parameter: 'tools[0].name'"
    whenever any MCP tool was registered.
    """
    name = get_mcp_tool_name(tool)
    if not name:
        return None
    parameters = get_mcp_tool_parameters(tool)
    if parameters is None:
        return None

    return {
        "type": "function",
        "name": name,
        "description": get_mcp_tool_description(tool),
        "parameters": parameters,
    }
