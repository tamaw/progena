"""
Dependency Injection Container for Progenitor Nodes.

Provides a centralized container for all node dependencies,
reducing manual wiring and improving testability.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Protocol, runtime_checkable

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
        system_prompt: Optional[str] = None,
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

    def find_by_name(self, name: str) -> Optional[Any]:
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
    worktrees_dir: Optional[Path] = None
    copilot_config_path: Optional[Path] = None

    # Optional overrides for specific components
    custom_nodes: dict[str, Callable] = field(default_factory=dict)

    def __post_init__(self):
        """Set defaults based on config if not provided."""
        if self.worktrees_dir is None:
            self.worktrees_dir = self.config.storage.worktree_dir

        if self.copilot_config_path is None:
            self.copilot_config_path = self.config.copilot.config_path


# Type alias for node functions
NodeFunction = Callable[[ProgenitorState], Coroutine[Any, Any, ProgenitorState]]


def create_all_nodes(deps: NodeDependencies) -> dict[str, NodeFunction]:
    """Create all node functions with shared dependencies.

    Args:
        deps: Container with all required dependencies.

    Returns:
        Dictionary mapping node names to their async functions.
    """
    from ..nodes import (
        decide,
        planner,
        intake,
        observe,
        think,
        act,
        execute,
        verify_loop,
        learn,
        respond,
        idle,
        commit_core,
        time_check,
        enqueue_due,
        pick_next,
        cycle_limit,
        validate,
        worktree_prep,
    )

    nodes = {
        # Outer loop nodes
        "decide": decide.create_node(
            deps.llm_client,
            deps.tool_registry,
            deps.state_manager,
        ),
        "planner": planner.create_node(
            deps.llm_client,
            deps.state_manager,
        ),
        "intake": intake.create_node(deps.state_manager),
        "time_check": time_check.create_node(),
        "enqueue_due": enqueue_due.create_node(deps.state_manager),
        "pick_next": pick_next.create_node(),
        "cycle_limit": cycle_limit.create_node(
            deps.config.runtime.max_actions_per_cycle
        ),
        "validate": validate.create_node(
            deps.state_manager,
            deps.config.storage.state_dir,
        ),
        "respond": respond.create_node(deps.state_manager),
        "idle": idle.create_node(
            deps.config.runtime.idle_backoff_seconds
        ),
        "commit_core": commit_core.create_node(deps.state_manager),

        # Inner loop nodes (loop.spec.md)
        "observe": observe.create_node(deps.llm_client),
        "think": think.create_node(deps.llm_client),
        "act": act.create_node(deps.llm_client),
        "execute": execute.create_node(
            deps.tool_registry,
            deps.state_manager,
        ),
        "verify_loop": verify_loop.create_node(deps.llm_client),
        "learn": learn.create_node(deps.llm_client),

        # Build infrastructure
        "worktree_prep": worktree_prep.create_node(deps.worktrees_dir),
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
    llm_client: Optional[LLMClientProtocol] = None,
    state_manager: Optional[StateManagerProtocol] = None,
    tool_registry: Optional[ToolRegistryProtocol] = None,
    config: Optional[ProgenitorConfig] = None,
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
