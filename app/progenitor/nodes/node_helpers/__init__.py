"""
Node Helpers Module.

Provides protocol types, a tool registry, and result formatters
used by the LangGraph nodes to execute and present tool results.
"""

from typing import TYPE_CHECKING

from .formatters import format_schedule_result, format_tool_result
from .protocol import BaseSystemTool, SystemTool, ToolContext, ToolResult
from .registry import (
    execute_system_tool,
    get_all_system_tools,
    get_system_tool,
    get_system_tool_names,
    match_tool_by_input,
)

if TYPE_CHECKING:
    from ...tools.build_status import BuildStatusTool
    from ...tools.compress_conversation import CompressConversationTool
    from ...tools.current_time import CurrentTimeTool
    from ...tools.list_agents import ListAgentsTool
    from ...tools.list_schedule import ListScheduleTool
    from ...tools.list_tools import ListToolsTool
    from ...tools.memory import MemoryTool
    from ...tools.new_conversation import NewConversationTool
    from ...tools.rediscover_agents import RediscoverAgentsTool
    from ...tools.schedule_task import ScheduleTaskTool
    from ...tools.self_reflect_tool import SelfReflectTool


def __getattr__(name: str):
    """Lazy-load tool classes to avoid circular imports with tools/ package."""
    _tool_map = {
        "ListToolsTool": "...tools.list_tools",
        "RediscoverAgentsTool": "...tools.rediscover_agents",
        "ListAgentsTool": "...tools.list_agents",
        "BuildStatusTool": "...tools.build_status",
        "CurrentTimeTool": "...tools.current_time",
        "ListScheduleTool": "...tools.list_schedule",
        "SelfReflectTool": "...tools.self_reflect_tool",
        "ScheduleTaskTool": "...tools.schedule_task",
        "NewConversationTool": "...tools.new_conversation",
        "CompressConversationTool": "...tools.compress_conversation",
        "MemoryTool": "...tools.memory",
    }
    if name in _tool_map:
        import importlib
        # Resolve the relative module path
        _abs_map = {
            "ListToolsTool": "app.progenitor.tools.list_tools",
            "RediscoverAgentsTool": "app.progenitor.tools.rediscover_agents",
            "ListAgentsTool": "app.progenitor.tools.list_agents",
            "BuildStatusTool": "app.progenitor.tools.build_status",
            "CurrentTimeTool": "app.progenitor.tools.current_time",
            "ListScheduleTool": "app.progenitor.tools.list_schedule",
            "SelfReflectTool": "app.progenitor.tools.self_reflect_tool",
            "ScheduleTaskTool": "app.progenitor.tools.schedule_task",
            "NewConversationTool": "app.progenitor.tools.new_conversation",
            "CompressConversationTool": "app.progenitor.tools.compress_conversation",
            "MemoryTool": "app.progenitor.tools.memory",
        }
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
    "match_tool_by_input",
    # Formatters
    "format_tool_result",
    "format_schedule_result",
    # Tool implementations (lazy-loaded)
    "ListToolsTool",
    "RediscoverAgentsTool",
    "ListAgentsTool",
    "BuildStatusTool",
    "CurrentTimeTool",
    "ListScheduleTool",
    "SelfReflectTool",
    "ScheduleTaskTool",
    "NewConversationTool",
    "CompressConversationTool",
    "MemoryTool",
]
