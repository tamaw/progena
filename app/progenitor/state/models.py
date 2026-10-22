"""Resolve model identities embedded in checkpoints written before domain moves.

Application code imports models from their domains. These lazy names exist only
so LangGraph can deserialize historical checkpoints without a database rewrite.
"""

from importlib import import_module

_MODEL_MODULES = {
    "ScheduleEntry": "schedules.models",
    "ScheduleRunStatus": "schedules.models",
    "ScheduleRunRecord": "schedules.models",
    "AgentStatus": "registry.models",
    "AgentRegistryEntry": "registry.models",
    "ACPAgentConfig": "acp.models",
    "A2ATaskStatus": "a2a.models",
    "A2ATaskRecord": "a2a.models",
    "ACPTaskStatus": "acp.models",
    "ACPTaskRecord": "acp.models",
    "AsyncSubagentTaskStatus": "async_subagents.models",
    "AsyncSubagentTaskRecord": "async_subagents.models",
    "ConversationMessage": "sessions.models",
    "BackgroundTaskEntry": "orchestration.state",
}


def __getattr__(name: str):
    module = _MODEL_MODULES.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f"app.progenitor.{module}"), name)
