"""LangGraph nodes for Progenitor.

CQ-006: Standardized type hints for node functions.

All node functions follow this pattern:
    - Input: ProgenitorState (TypedDict)
    - Output: ProgenitorState (partial dict with only changed keys)

Nodes return partial state updates (dicts with subset of keys) rather than
full ProgenitorState objects. LangGraph merges these into the full state.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from ..state.models import ProgenitorState

# CQ-006: Standard type aliases for node functions
# Nodes receive full state and return partial updates (dict with changed keys only)
NodeStateUpdate = dict[str, Any]  # Partial state update (subset of ProgenitorState keys)
NodeFunction = Callable[[ProgenitorState], Coroutine[Any, Any, NodeStateUpdate]]
NodeFactory = Callable[..., NodeFunction]

# Submodules
from . import (
    act,
    build,  # Extracted from graph_inner_loop.py
    commit_core,
    cycle_limit,
    decide,
    execute,
    heuristics,
    idle,
    intake,
    learn,
    merge,  # Sequential merge with conflict resolution
    node_helpers,
    # New nodes for loop.spec.md dual-loop architecture
    observe,
    pick_next,
    planner,
    respond,
    think,
    time_check,
    validate,
    verify_loop,
    worktree_prep,
)

# Dependency injection
from .dependencies import (
    LLMClientProtocol,
    NodeDependencies,
    StateManagerProtocol,
    ToolRegistryProtocol,
    create_all_nodes,
    create_node_subset,
    create_test_dependencies,
)

# Error types for consistent error handling across nodes
from .errors import (
    ConfigurationError,
    FatalError,
    LLMError,
    NodeError,
    RecoverableError,
    StateError,
    ToolError,
    ValidationError,
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
    "merge",  # Sequential merge with conflict resolution
    # Submodules
    "heuristics",
    "node_helpers",
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
]

