"""LangGraph nodes for Progenitor."""

from . import (
    intake,
    time_check,
    enqueue_due,
    decide,
    planner,
    pick_next,
    worktree_prep,
    act,
    validate,
    register,
    commit_core,
    respond,
    idle,
    cycle_limit,
    # New nodes for loop.spec.md dual-loop architecture
    observe,
    think,
    execute,
    verify_loop,
    learn,
)

# Error types for consistent error handling across nodes
from .errors import (
    NodeError,
    RecoverableError,
    FatalError,
    ValidationError,
    LLMError,
    ToolError,
    StateError,
    ConfigurationError,
)

# Dependency injection
from .dependencies import (
    NodeDependencies,
    LLMClientProtocol,
    StateManagerProtocol,
    ToolRegistryProtocol,
    create_all_nodes,
    create_node_subset,
    create_test_dependencies,
)

# Telemetry decorators
from .telemetry_decorators import (
    traced_node,
    traced_method,
    timed,
    NodeInstrumentationMixin,
)

__all__ = [
    "intake",
    "time_check",
    "enqueue_due",
    "decide",
    "planner",
    "pick_next",
    "worktree_prep",
    "act",
    "validate",
    "register",
    "commit_core",
    "respond",
    "idle",
    "cycle_limit",
    # New loop.spec.md nodes
    "observe",
    "think",
    "execute",
    "verify_loop",
    "learn",
    # Error types
    "NodeError",
    "RecoverableError",
    "FatalError",
    "ValidationError",
    "LLMError",
    "ToolError",
    "StateError",
    "ConfigurationError",
    # Dependency injection
    "NodeDependencies",
    "LLMClientProtocol",
    "StateManagerProtocol",
    "ToolRegistryProtocol",
    "create_all_nodes",
    "create_node_subset",
    "create_test_dependencies",
    # Telemetry decorators
    "traced_node",
    "traced_method",
    "timed",
    "NodeInstrumentationMixin",
]

