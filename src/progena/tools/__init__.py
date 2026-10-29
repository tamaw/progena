"""Tools module - agent discovery, schedule, self-reflection, user communication, and LLM-invoked system tools."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .acp_results import ACPGetResultTool
    from .current_time import CurrentTimeTool
    from .enqueue_background_task import EnqueueBackgroundTaskTool
    from .list_agents import ListAgentsTool
    from .list_tools import ListToolsTool
    from .new_conversation import NewConversationTool
    from .resolve_workflow_approval import ResolveWorkflowApprovalTool
    from .resume_routine import ResumeRoutineTool
    from .run_workflow import RunWorkflowTool
    from .schedule_results import GetScheduleRunResultTool, ListScheduleRunsTool
    from .self_reflect import SELF_REFLECT_TOOL_SPEC, SelfReflection, execute_self_reflect
    from .self_reflect_tool import SelfReflectTool


def __getattr__(name: str):
    """Lazy-load LLM-invoked tool classes to avoid circular imports with node_helpers."""
    _tool_modules = {
        "ACPGetResultTool": "acp_results",
        "SelfReflection": "self_reflect",
        "execute_self_reflect": "self_reflect",
        "SELF_REFLECT_TOOL_SPEC": "self_reflect",
        "ListToolsTool": "list_tools",
        "ListAgentsTool": "list_agents",
        "NewConversationTool": "new_conversation",
        "CurrentTimeTool": "current_time",
        "EnqueueBackgroundTaskTool": "enqueue_background_task",
        "SelfReflectTool": "self_reflect_tool",
        "ListScheduleRunsTool": "schedule_results",
        "GetScheduleRunResultTool": "schedule_results",
        "RunWorkflowTool": "run_workflow",
        "ResumeRoutineTool": "resume_routine",
        "ResolveWorkflowApprovalTool": "resolve_workflow_approval",
    }
    if name in _tool_modules:
        import importlib

        mod = importlib.import_module(f".{_tool_modules[name]}", __package__)
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SelfReflection",
    "execute_self_reflect",
    "SELF_REFLECT_TOOL_SPEC",
    # LLM-invoked system tools (lazy-loaded)
    "ACPGetResultTool",
    "ListToolsTool",
    "ListAgentsTool",
    "NewConversationTool",
    "CurrentTimeTool",
    "EnqueueBackgroundTaskTool",
    "SelfReflectTool",
    "ListScheduleRunsTool",
    "GetScheduleRunResultTool",
    "RunWorkflowTool",
    "ResumeRoutineTool",
    "ResolveWorkflowApprovalTool",
]
