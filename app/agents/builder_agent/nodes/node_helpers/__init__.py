"""
Node Helpers Module.

Provides protocol types, a tool registry, and result formatters
used by the LangGraph nodes to execute and present tool results.
"""

from typing import TYPE_CHECKING

from ...tools.protocol import BaseSystemTool, SystemTool, ToolContext, ToolResult
from .formatters import format_schedule_result, format_tool_result
from .registry import (
    execute_system_tool,
    get_all_system_tools,
    get_system_tool,
    get_system_tool_names,
)

if TYPE_CHECKING:
    from ...tools.copilot import CopilotTool
    from ...tools.git import GitTool
    from ...tools.mcp import MCPTool
    from ...tools.registry import RegistryTool
    from ...tools.shell import ShellCheckTool, ShellExecTool
    from ...tools.user_communication import UserCommunicationTool


def __getattr__(name: str):
    """Lazy-load tool classes to avoid circular imports with tools/ package."""
    # Only tools that exist in the builder agent tools package
    _abs_map = {
        "CopilotTool": "app.agents.builder_agent.tools.copilot",
        "MCPTool": "app.agents.builder_agent.tools.mcp",
        "RegistryTool": "app.agents.builder_agent.tools.registry",
        "GitTool": "app.agents.builder_agent.tools.git",
        "ShellCheckTool": "app.agents.builder_agent.tools.shell",
        "ShellExecTool": "app.agents.builder_agent.tools.shell",
        "UserCommunicationTool": "app.agents.builder_agent.tools.user_communication",
    }
    if name in _abs_map:
        import importlib
        mod = importlib.import_module(_abs_map[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Protocol and base types
    "ToolContext",
    "ToolResult",
    "SystemTool",
    "BaseSystemTool",
    # Registry functions
    "execute_system_tool",
    "get_system_tool",
    "get_all_system_tools",
    "get_system_tool_names",
    # Formatters
    "format_tool_result",
    "format_schedule_result",
    # Tool implementations (lazy-loaded via builder agent tools)
    "CopilotTool",
    "MCPTool",
    "RegistryTool",
    "GitTool",
    "ShellCheckTool",
    "ShellExecTool",
    "UserCommunicationTool",
]
