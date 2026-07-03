"""Tools module - agent discovery, schedule, self-reflection, user communication, and LLM-invoked system tools."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..registry import ToolRegistry
    from .agent_discovery import AgentDiscovery, discover_agents
    from .current_time import CurrentTimeTool
    from .enqueue_background_task import EnqueueBackgroundTaskTool
    from .list_agents import ListAgentsTool
    from .list_tools import ListToolsTool
    from .new_conversation import NewConversationTool
    from .schedule_results import GetScheduleRunResultTool, ListScheduleRunsTool
    from .self_reflect import SELF_REFLECT_TOOL_SPEC, SelfReflection, execute_self_reflect
    from .self_reflect_tool import SelfReflectTool
    from .user_communication import USER_COMMUNICATION_TOOLS, UserCommunicationTools


def __getattr__(name: str):
    """Lazy-load LLM-invoked tool classes to avoid circular imports with node_helpers."""
    _tool_modules = {
        "ToolRegistry": "registry",
        "AgentDiscovery": "agent_discovery",
        "discover_agents": "agent_discovery",
        "SelfReflection": "self_reflect",
        "execute_self_reflect": "self_reflect",
        "SELF_REFLECT_TOOL_SPEC": "self_reflect",
        "UserCommunicationTools": "user_communication",
        "USER_COMMUNICATION_TOOLS": "user_communication",
        "ListToolsTool": "list_tools",
        "ListAgentsTool": "list_agents",
        "NewConversationTool": "new_conversation",
        "CurrentTimeTool": "current_time",
        "EnqueueBackgroundTaskTool": "enqueue_background_task",
        "SelfReflectTool": "self_reflect_tool",
        "ListScheduleRunsTool": "schedule_results",
        "GetScheduleRunResultTool": "schedule_results",
    }
    if name in _tool_modules:
        import importlib

        mod = importlib.import_module(f".{_tool_modules[name]}", __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ToolRegistry",
    "AgentDiscovery",
    "discover_agents",
    "SelfReflection",
    "execute_self_reflect",
    "SELF_REFLECT_TOOL_SPEC",
    "UserCommunicationTools",
    "USER_COMMUNICATION_TOOLS",
    # LLM-invoked system tools (lazy-loaded)
    "ListToolsTool",
    "ListAgentsTool",
    "NewConversationTool",
    "CurrentTimeTool",
    "EnqueueBackgroundTaskTool",
    "SelfReflectTool",
    "ListScheduleRunsTool",
    "GetScheduleRunResultTool",
]
