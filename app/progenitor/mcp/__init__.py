"""MCP integration package for Progenitor."""

from .context import (
    build_mcp_langchain_tool_wrappers,
    build_mcp_runtime_context,
    fetch_mcp_server_catalog,
    reload_mcp_runtime_context,
)
from .jsonrpc import parse_mcp_jsonrpc_response
from .loader import load_mcp_servers, resolve_required_env_template
from .runtime import MCPRuntime
from .tool_schema import (
    get_mcp_tool_description,
    get_mcp_tool_name,
    get_mcp_tool_parameters,
    normalize_mcp_tool_definition,
)

__all__ = [
    "MCPRuntime",
    "build_mcp_langchain_tool_wrappers",
    "build_mcp_runtime_context",
    "fetch_mcp_server_catalog",
    "get_mcp_tool_description",
    "get_mcp_tool_name",
    "get_mcp_tool_parameters",
    "load_mcp_servers",
    "normalize_mcp_tool_definition",
    "parse_mcp_jsonrpc_response",
    "reload_mcp_runtime_context",
    "resolve_required_env_template",
]
