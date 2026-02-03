"""LangGraph nodes for Progenitor.

CQ-006: Standardized type hints for node functions.

All node functions follow this pattern:
    - Input: ProgenitorState (TypedDict)
    - Output: ProgenitorState (partial dict with only changed keys)

Nodes return partial state updates (dicts with subset of keys) rather than
full ProgenitorState objects. LangGraph merges these into the full state.
"""

from typing import Callable, Coroutine, Any
from ..state.models import ProgenitorState

# CQ-006: Standard type aliases for node functions
# Nodes receive full state and return partial updates (dict with changed keys only)
NodeStateUpdate = dict[str, Any]  # Partial state update (subset of ProgenitorState keys)
NodeFunction = Callable[[ProgenitorState], Coroutine[Any, Any, NodeStateUpdate]]
NodeFactory = Callable[..., NodeFunction]

from . import (
    intake,
    time_check,
    decide,
    planner,
    pick_next,
    worktree_prep,
    act,
    validate,
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
    build,  # Extracted from graph_inner_loop.py
)

# Submodules
from . import heuristics
from . import system_tools

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
    # CQ-006: Type aliases for consistent node signatures
    "NodeStateUpdate",
    "NodeFunction",
    "NodeFactory",
    # Node modules
    "intake",
    "time_check",
    "decide",
    "planner",
    "pick_next",
    "worktree_prep",
    "act",
    "validate",
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
    "build",  # Extracted from graph_inner_loop.py
    # Submodules
    "heuristics",
    "system_tools",
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

