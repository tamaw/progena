"""
Constants and Enums for Progenitor.

Centralized location for magic strings, phase names, and route targets.
Reduces duplication and improves type safety across the codebase.
"""

from enum import StrEnum

from .config import (  # noqa: F401
    DEFAULT_BUILD_CONFIDENCE_THRESHOLD,
    DEFAULT_MAX_INNER_ITERATIONS,
    DEFAULT_MAX_OUTER_ITERATIONS,
    DEFAULT_VERIFICATION_TARGET_SCORE,
)


class BuildPhase(StrEnum):
    """Phases of the inner build loop."""
    OBSERVE = "observe"
    THINK = "think"
    PLAN = "plan"
    BUILD = "build"
    EXECUTE = "execute"
    VERIFY = "verify"
    LEARN = "learn"

    def __str__(self) -> str:
        return f"{self.__class__.__name__}.{self.name}"


class RouteTarget(StrEnum):
    """Valid routing targets for graph transitions."""
    # Outer loop targets
    INTAKE = "intake"
    DECIDE = "decide"
    PICK_NEXT = "pick_next"
    WORKTREE_PREP = "worktree_prep"
    ACT = "act"
    VALIDATE = "validate"
    COMMIT_CORE = "commit_core"
    RESPOND = "respond"
    IDLE = "idle"
    CYCLE_LIMIT = "cycle_limit"

    # Inner loop targets
    OBSERVE = "observe"
    THINK = "think"
    PLAN = "plan"
    BUILD = "build"
    EXECUTE = "execute"
    VERIFY = "verify"
    LEARN = "learn"

    # Terminal states
    END = "end"
    DONE = "done"


class SystemToolName(StrEnum):
    """Names of available system tools."""
    LIST_TOOLS = "list_tools"
    BUILD_STATUS = "build_status"
    REDISCOVER_AGENTS = "rediscover_agents"
    LIST_AGENTS = "list_agents"
    SCHEDULE_TASK = "schedule_task"
    LIST_SCHEDULE = "list_schedule"
    CURRENT_TIME = "current_time"
    SELF_REFLECT = "self_reflect"
    NEW_CONVERSATION = "new_conversation"
    COMPRESS_CONVERSATION = "compress_conversation"
    SPAWN_GITHUB_AGENT = "spawn_github_agent"
