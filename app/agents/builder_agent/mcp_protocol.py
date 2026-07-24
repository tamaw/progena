"""Local MCP JSON-RPC and tool-schema helpers for builder_agent.

Self-contained copy of the MCP protocol parsing utilities: builder_agent does
not depend on app.progenitor. Behavior matches app/progenitor/mcp/ exactly.
"""

from __future__ import annotations

import json
from typing import Any, cast


def parse_mcp_jsonrpc_response(text: str) -> dict[str, Any] | None:
    """Parse a JSON-RPC response delivered as plain JSON or SSE."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None

    candidates: list[str] = []
    if raw.startswith("{"):
        candidates.append(raw)
    else:
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped.startswith("data:"):
                continue
            payload = stripped[len("data:") :].strip()
            if payload and payload != "[DONE]":
                candidates.append(payload)

    parsed: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            parsed = cast(dict[str, Any], data)

    return parsed


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
    {"type": "function", "function": {"name": ...}}.
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
