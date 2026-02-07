"""
Node Helpers Module.

Provides protocol types, a tool registry, and result formatters
used by the LangGraph nodes to execute and present tool results.
"""

from .protocol import ToolContext, ToolResult, SystemTool, BaseSystemTool
from .registry import (
    execute_system_tool,
    get_system_tool,
    get_all_system_tools,
    get_system_tool_names,
)
from .formatters import format_tool_result, format_schedule_result


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
]
