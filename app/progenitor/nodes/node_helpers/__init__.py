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
    from ...tools.current_time import CurrentTimeTool
    from ...tools.list_agents import ListAgentsTool
    from ...tools.list_schedule import ListScheduleTool
    from ...tools.list_tools import ListToolsTool
    from ...tools.new_conversation import NewConversationTool
    from ...tools.rediscover_agents import RediscoverAgentsTool
    from ...tools.schedule_task import ScheduleTaskTool
    from ...tools.self_reflect_tool import SelfReflectTool
    from ...tools.shell import ShellCheckTool, ShellExecTool


def __getattr__(name: str):
    """Lazy-load tool classes to avoid circular imports with tools/ package."""
    _abs_map = {
        "ListToolsTool": "app.progenitor.tools.list_tools",
        "RediscoverAgentsTool": "app.progenitor.tools.rediscover_agents",
        "ListAgentsTool": "app.progenitor.tools.list_agents",
        "AgentDiscoveryTool": "app.progenitor.tools.agent_discovery",
        "CurrentTimeTool": "app.progenitor.tools.current_time",
        "ListScheduleTool": "app.progenitor.tools.list_schedule",
        "SelfReflectTool": "app.progenitor.tools.self_reflect_tool",
        "SelfReflectModuleTool": "app.progenitor.tools.self_reflect",
        "MCPTool": "app.progenitor.tools.mcp",
        "RegistryTool": "app.progenitor.tools.registry",
        "ScheduleTool": "app.progenitor.tools.schedule",
        "SemanticRegistryTool": "app.progenitor.tools.semantic_registry",
        "UserCommunicationTool": "app.progenitor.tools.user_communication",
        "ScheduleTaskTool": "app.progenitor.tools.schedule_task",
        "NewConversationTool": "app.progenitor.tools.new_conversation",
        "ShellCheckTool": "app.progenitor.tools.shell",
        "ShellExecTool": "app.progenitor.tools.shell",
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
    # Tool implementations (lazy-loaded)
    "ListToolsTool",
    "RediscoverAgentsTool",
    "ListAgentsTool",
    "AgentDiscoveryTool",
    "CurrentTimeTool",
    "ListScheduleTool",
    "SelfReflectTool",
    "SelfReflectModuleTool",
    "MCPTool",
    "RegistryTool",
    "ScheduleTool",
    "SemanticRegistryTool",
    "UserCommunicationTool",
    "ScheduleTaskTool",
    "NewConversationTool",
    "ShellCheckTool",
    "ShellExecTool",
]

