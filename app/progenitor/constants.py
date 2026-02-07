"""
Constants and Enums for Progenitor.

Centralized location for magic strings, phase names, and route targets.
Reduces duplication and improves type safety across the codebase.
"""

from enum import Enum


class BuildPhase(str, Enum):
    """Phases of the inner build loop."""
    OBSERVE = "observe"
    THINK = "think"
    PLAN = "plan"
    BUILD = "build"
    EXECUTE = "execute"
    VERIFY = "verify"
    LEARN = "learn"


class RouteTarget(str, Enum):
    """Valid routing targets for graph transitions."""
    # Outer loop targets
    INTAKE = "intake"
    TIME_CHECK = "time_check"
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


class SystemToolName(str, Enum):
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


# Default thresholds and limits
DEFAULT_MAX_INNER_ITERATIONS = 5
DEFAULT_MAX_OUTER_ITERATIONS = 10
DEFAULT_BUILD_CONFIDENCE_THRESHOLD = 0.95
DEFAULT_VERIFICATION_TARGET_SCORE = 0.8
DEFAULT_MAX_CONVERSATION_HISTORY = 10

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0
DEFAULT_RETRY_MULTIPLIER = 2.0
DEFAULT_RETRY_JITTER = 0.5
