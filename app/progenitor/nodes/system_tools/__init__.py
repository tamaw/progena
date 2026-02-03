"""
System Tools Module.

Provides a protocol-based system for executing inline system tools.
"""

from .protocol import ToolContext, ToolResult, SystemTool, BaseSystemTool
from .registry import (
    execute_system_tool,
    get_system_tool,
    get_all_system_tools,
    get_system_tool_names,
)
from .formatters import format_tool_result, format_schedule_result
from .tools import (
    ListToolsTool,
    RediscoverAgentsTool,
    ListAgentsTool,
    BuildStatusTool,
    CurrentTimeTool,
    ListScheduleTool,
    SelfReflectTool,
    ScheduleTaskTool,
)

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
    # Tool implementations
    "ListToolsTool",
    "RediscoverAgentsTool",
    "ListAgentsTool",
    "BuildStatusTool",
    "CurrentTimeTool",
    "ListScheduleTool",
    "SelfReflectTool",
    "ScheduleTaskTool",
]
