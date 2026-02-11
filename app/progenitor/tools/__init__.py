"""Tools module - Copilot CLI, git/worktree helpers, MCP client, agent discovery, schedule, self-reflection, user communication, and LLM-invoked system tools."""

from typing import TYPE_CHECKING

from .agent_discovery import AgentDiscovery, discover_agents
from .copilot import CopilotCLI
from .git import GitWorktreeManager
from .mcp import MCPClient
from .registry import ToolRegistry
from .schedule import (
    SCHEDULE_TOOL_SPEC,
    add_scheduled_task,
    list_scheduled_tasks,
    remove_scheduled_task,
)
from .self_reflect import (
    SELF_REFLECT_TOOL_SPEC,
    SelfReflection,
    execute_self_reflect,
)
from .user_communication import (
    USER_COMMUNICATION_TOOLS,
    UserCommunicationTools,
)

if TYPE_CHECKING:
    from .build_status import BuildStatusTool
    from .compress_conversation import CompressConversationTool
    from .current_time import CurrentTimeTool
    from .list_agents import ListAgentsTool
    from .list_schedule import ListScheduleTool
    from .list_tools import ListToolsTool
    from .memory import MemoryTool
    from .new_conversation import NewConversationTool
    from .rediscover_agents import RediscoverAgentsTool
    from .schedule_task import ScheduleTaskTool
    from .self_reflect_tool import SelfReflectTool


def __getattr__(name: str):
    """Lazy-load LLM-invoked tool classes to avoid circular imports with node_helpers."""
    _tool_modules = {
        "ListToolsTool": "list_tools",
        "RediscoverAgentsTool": "rediscover_agents",
        "ListAgentsTool": "list_agents",
        "NewConversationTool": "new_conversation",
        "BuildStatusTool": "build_status",
        "CurrentTimeTool": "current_time",
        "ListScheduleTool": "list_schedule",
        "SelfReflectTool": "self_reflect_tool",
        "ScheduleTaskTool": "schedule_task",
        "CompressConversationTool": "compress_conversation",
        "MemoryTool": "memory",
    }
    if name in _tool_modules:
        import importlib
        mod = importlib.import_module(f".{_tool_modules[name]}", __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ToolRegistry",
    "CopilotCLI",
    "GitWorktreeManager",
    "MCPClient",
    "AgentDiscovery",
    "discover_agents",
    "add_scheduled_task",
    "remove_scheduled_task",
    "list_scheduled_tasks",
    "SCHEDULE_TOOL_SPEC",
    "SelfReflection",
    "execute_self_reflect",
    "SELF_REFLECT_TOOL_SPEC",
    "UserCommunicationTools",
    "USER_COMMUNICATION_TOOLS",
    # LLM-invoked system tools (lazy-loaded)
    "ListToolsTool",
    "RediscoverAgentsTool",
    "ListAgentsTool",
    "NewConversationTool",
    "BuildStatusTool",
    "CurrentTimeTool",
    "ListScheduleTool",
    "SelfReflectTool",
    "ScheduleTaskTool",
    "CompressConversationTool",
    "MemoryTool",
]
