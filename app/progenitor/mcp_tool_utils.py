"""Shared helpers for normalizing standard external MCP tool metadata."""

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
    """Normalize an MCP tool into OpenAI-compatible function-tool format."""
    name = get_mcp_tool_name(tool)
    if not name:
        return None
    parameters = get_mcp_tool_parameters(tool)
    if parameters is None:
        return None

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": get_mcp_tool_description(tool),
            "parameters": parameters,
        },
    }
