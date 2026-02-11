"""
Dependency Injection Container for Progenitor Nodes.

Provides a centralized container for all node dependencies,
reducing manual wiring and improving testability.
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..config import ProgenitorConfig
from ..state.models import ProgenitorState

# ============================================================================
# Protocols for dependency interfaces
# ============================================================================

@runtime_checkable
class LLMClientProtocol(Protocol):
    """Protocol for LLM client implementations."""

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict],
        available_capabilities: list[dict],
    ) -> dict[str, Any]:
        """Make a routing decision."""
        ...

    async def plan(
        self,
        user_input: str,
        decision: dict,
        current_backlog: list[dict],
    ) -> list[dict]:
        """Generate work items."""
        ...

    async def generate_build_spec(
        self,
        work_item: dict,
        context: dict,
    ) -> dict:
        """Generate build specification."""
        ...

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        """Generate text completion."""
        ...


@runtime_checkable
class StateManagerProtocol(Protocol):
    """Protocol for state manager implementations."""

    async def get_backlog(self) -> list:
        """Get the backlog."""
        ...

    async def save_backlog(self, backlog: list) -> None:
        """Save the backlog."""
        ...

    async def log_decision(self, decision: dict) -> None:
        """Log a decision."""
        ...

    async def log_conversation(self, message: Any) -> None:
        """Log a conversation message."""
        ...


@runtime_checkable
class ToolRegistryProtocol(Protocol):
    """Protocol for tool registry implementations."""

    def get_all_capabilities(self) -> list[dict]:
        """Get all available capabilities."""
        ...

    def find_by_name(self, name: str) -> Any | None:
        """Find a tool by name."""
        ...


# ============================================================================
# Dependency Container
# ============================================================================

@dataclass
class NodeDependencies:
    """Container for all node dependencies.

    Provides a single place to configure all dependencies needed by nodes.
    This simplifies node creation and improves testability by allowing
    easy mocking of dependencies.

    Usage:
        deps = NodeDependencies(
            llm_client=my_llm_client,
            state_manager=my_state_manager,
            tool_registry=my_registry,
            config=my_config,
        )

        nodes = create_all_nodes(deps)
    """

    llm_client: LLMClientProtocol
    state_manager: StateManagerProtocol
    tool_registry: ToolRegistryProtocol
    config: ProgenitorConfig
    worktrees_dir: Path | None = None

    # Optional overrides for specific components
    custom_nodes: dict[str, Callable] = field(default_factory=dict)

    def __post_init__(self):
        """Set defaults based on config if not provided."""
        if self.worktrees_dir is None:
            self.worktrees_dir = self.config.storage.worktree_dir




# Type alias for node functions (partial state updates)
NodeFunction = Callable[[ProgenitorState], Coroutine[Any, Any, dict[str, Any]]]


def create_all_nodes(deps: NodeDependencies) -> dict[str, NodeFunction]:
    """Create all node functions with shared dependencies.

    Args:
        deps: Container with all required dependencies.

    Returns:
        Dictionary mapping node names to their async functions.
    """
    from ..nodes import (
        act,
        commit_core,
        cycle_limit,
        decide,
        enqueue_due,
        execute,
        idle,
        intake,
        learn,
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

    nodes = {
        # Outer loop nodes
        "decide": decide.create_node(
            deps.llm_client,
            deps.tool_registry,
            deps.state_manager,
            deps.config,
        ),
        "planner": planner.create_node(
            deps.llm_client,
            deps.state_manager,
        ),
        "intake": intake.create_node(deps.state_manager),
        # CQ-012: time_check requires timezone parameter
        "time_check": time_check.create_node(
            timezone=deps.config.runtime.timezone
        ),
        "enqueue_due": enqueue_due.create_node(deps.state_manager),
        "pick_next": pick_next.create_node(),
        # CQ-012: cycle_limit expects full config object
        "cycle_limit": cycle_limit.create_node(deps.config),
        # CQ-012: validate expects full config object
        "validate": validate.create_node(deps.config, deps.llm_client, deps.state_manager),
        "respond": respond.create_node(deps.state_manager),
        "idle": idle.create_node(
            deps.config,
        ),
        "commit_core": commit_core.create_node(deps.state_manager),

        # Inner loop nodes (loop.spec.md)
        "observe": observe.create_node(deps.llm_client),
        "think": think.create_node(deps.llm_client),
        "act": act.create_node(
            deps.llm_client,
            deps.tool_registry,
            deps.config,
            deps.worktrees_dir or deps.config.storage.worktree_dir,
        ),
        "execute": execute.create_node(
            deps.tool_registry,
            deps.state_manager,
        ),
        "verify_loop": verify_loop.create_node(deps.llm_client),
        "learn": learn.create_node(deps.llm_client),

        # Build infrastructure
        "worktree_prep": worktree_prep.create_node(
            deps.worktrees_dir or deps.config.storage.worktree_dir
        ),
    }

    # Apply any custom node overrides
    nodes.update(deps.custom_nodes)

    return nodes


def create_node_subset(
    deps: NodeDependencies,
    node_names: list[str]
) -> dict[str, NodeFunction]:
    """Create only specified nodes (useful for testing).

    Args:
        deps: Container with all required dependencies.
        node_names: List of node names to create.

    Returns:
        Dictionary with only the requested nodes.
    """
    all_nodes = create_all_nodes(deps)
    return {name: all_nodes[name] for name in node_names if name in all_nodes}


# ============================================================================
# Factory functions for common setups
# ============================================================================

def create_test_dependencies(
    llm_client: LLMClientProtocol | None = None,
    state_manager: StateManagerProtocol | None = None,
    tool_registry: ToolRegistryProtocol | None = None,
    config: ProgenitorConfig | None = None,
) -> NodeDependencies:
    """Create dependencies with test defaults.

    Useful for unit tests where you want to inject mocks.
    Any non-provided dependency will use a minimal mock.
    """
    from unittest.mock import AsyncMock, MagicMock

    if config is None:
        config = ProgenitorConfig()

    if llm_client is None:
        llm_client = MagicMock(spec=LLMClientProtocol)
        llm_client.decide = AsyncMock(return_value={
            "action": "answer_directly",
            "reasoning": "Test",
        })
        llm_client.plan = AsyncMock(return_value=[])
        llm_client.generate = AsyncMock(return_value="Test response")

    if state_manager is None:
        state_manager = MagicMock(spec=StateManagerProtocol)
        state_manager.get_backlog = AsyncMock(return_value=[])
        state_manager.save_backlog = AsyncMock()
        state_manager.log_decision = AsyncMock()
        state_manager.log_conversation = AsyncMock()

    if tool_registry is None:
        tool_registry = MagicMock(spec=ToolRegistryProtocol)
        tool_registry.get_all_capabilities = MagicMock(return_value=[])
        tool_registry.find_by_name = MagicMock(return_value=None)

    return NodeDependencies(
        llm_client=llm_client,
        state_manager=state_manager,
        tool_registry=tool_registry,
        config=config,
    )
