"""Tools module - agent discovery, schedule, self-reflection, user communication, and LLM-invoked system tools."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..registry import ToolRegistry
    from .agent_discovery import AgentDiscovery, discover_agents
    from .current_time import CurrentTimeTool
    from .enqueue_background_task import EnqueueBackgroundTaskTool
    from .list_agents import ListAgentsTool
    from .list_schedule import ListScheduleTool
    from .list_tools import ListToolsTool
    from .new_conversation import NewConversationTool
    from .schedule import (
        DELETE_SCHEDULE_TOOL_SPEC,
        PAUSE_SCHEDULE_TOOL_SPEC,
        RESUME_SCHEDULE_TOOL_SPEC,
        SCHEDULE_TOOL_SPEC,
        UPDATE_SCHEDULE_TOOL_SPEC,
        delete_scheduled_task,
        list_scheduled_tasks,
        pause_scheduled_task,
        resume_scheduled_task,
        update_scheduled_task,
    )
    from .schedule_results import GetScheduleRunResultTool, ListScheduleRunsTool
    from .schedule_task import ScheduleTaskTool
    from .self_reflect import SELF_REFLECT_TOOL_SPEC, SelfReflection, execute_self_reflect
    from .self_reflect_tool import SelfReflectTool
    from .user_communication import USER_COMMUNICATION_TOOLS, UserCommunicationTools


def __getattr__(name: str):
    """Lazy-load LLM-invoked tool classes to avoid circular imports with node_helpers."""
    _tool_modules = {
        "ToolRegistry": "registry",
        "AgentDiscovery": "agent_discovery",
        "discover_agents": "agent_discovery",
        "delete_scheduled_task": "schedule",
        "update_scheduled_task": "schedule",
        "pause_scheduled_task": "schedule",
        "resume_scheduled_task": "schedule",
        "list_scheduled_tasks": "schedule",
        "SCHEDULE_TOOL_SPEC": "schedule",
        "DELETE_SCHEDULE_TOOL_SPEC": "schedule",
        "UPDATE_SCHEDULE_TOOL_SPEC": "schedule",
        "PAUSE_SCHEDULE_TOOL_SPEC": "schedule",
        "RESUME_SCHEDULE_TOOL_SPEC": "schedule",
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
        "ListScheduleTool": "list_schedule",
        "SelfReflectTool": "self_reflect_tool",
        "ScheduleTaskTool": "schedule_task",
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
    "delete_scheduled_task",
    "update_scheduled_task",
    "pause_scheduled_task",
    "resume_scheduled_task",
    "list_scheduled_tasks",
    "SCHEDULE_TOOL_SPEC",
    "DELETE_SCHEDULE_TOOL_SPEC",
    "UPDATE_SCHEDULE_TOOL_SPEC",
    "PAUSE_SCHEDULE_TOOL_SPEC",
    "RESUME_SCHEDULE_TOOL_SPEC",
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
    "ListScheduleTool",
    "SelfReflectTool",
    "ScheduleTaskTool",
    "ListScheduleRunsTool",
    "GetScheduleRunResultTool",
]
