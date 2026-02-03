"""
Node Dependencies - Dependency Injection Container for Graph Nodes.

Provides a consistent interface for injecting dependencies into node factories.
Replaces inconsistent function signatures with a single container pattern.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ProgenitorConfig
    from .llm.client import LLMClient
    from .tools.registry import ToolRegistry
    from .state.manager import StateManager


# Type alias for event pusher callback
EventPusher = Callable[[str, str, Optional[dict[str, Any]]], Coroutine[Any, Any, None]]


class StateManagerProtocol(Protocol):
    """Protocol for state manager to avoid circular imports."""

    async def get_state(self) -> dict[str, Any]: ...
    async def save_state(self, state: dict[str, Any]) -> None: ...
    async def log_decision(self, decision: dict[str, Any]) -> None: ...
    async def add_schedule_entry(self, entry: Any) -> None: ...
    async def save_build_state(self, state: dict[str, Any]) -> None: ...


class ToolRegistryProtocol(Protocol):
    """Protocol for tool registry to avoid circular imports."""

    def get_tools(self) -> list[dict[str, Any]]: ...
    def get_agents(self) -> list[dict[str, Any]]: ...
    def get_all_capabilities(self) -> list[dict[str, Any]]: ...


class LLMClientProtocol(Protocol):
    """Protocol for LLM client to avoid circular imports."""

    async def chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        response_format: Optional[dict[str, str]] = None,
    ) -> str: ...

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str: ...

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict[str, str]],
        available_capabilities: list[dict[str, Any]],
    ) -> dict[str, Any]: ...


@dataclass
class NodeDependencies:
    """Dependency container for node factories.

    Provides a consistent interface for all node factories, simplifying
    dependency injection and making node signatures uniform.

    Usage:
        deps = NodeDependencies(
            llm_client=llm_client,
            state_manager=state_manager,
            tool_registry=tool_registry,
            config=config,
        )

        # All nodes use the same interface:
        intake_node = intake.create_node(deps)
        decide_node = decide.create_node(deps)
        act_node = act.create_node(deps)

    Attributes:
        llm_client: LLM client for model calls.
        state_manager: State persistence manager.
        tool_registry: Registry of available tools and agents.
        config: Runtime configuration.
        worktrees_dir: Path to git worktrees directory.
        event_pusher: Callback for pushing events to clients.
    """

    llm_client: Optional[Any] = None  # LLMClientProtocol
    state_manager: Optional[Any] = None  # StateManagerProtocol
    tool_registry: Optional[Any] = None  # ToolRegistryProtocol
    config: Optional[Any] = None  # ProgenitorConfig
    worktrees_dir: Optional[Path] = None
    event_pusher: Optional[EventPusher] = None

    def __post_init__(self):
        """Validate dependencies after initialization."""
        # Convert string paths to Path objects
        if self.worktrees_dir is not None and isinstance(self.worktrees_dir, str):
            self.worktrees_dir = Path(self.worktrees_dir)

    def require_llm_client(self) -> Any:
        """Get LLM client or raise if not configured."""
        if self.llm_client is None:
            raise ValueError("LLM client is required but not configured")
        return self.llm_client

    def require_state_manager(self) -> Any:
        """Get state manager or raise if not configured."""
        if self.state_manager is None:
            raise ValueError("State manager is required but not configured")
        return self.state_manager

    def require_tool_registry(self) -> Any:
        """Get tool registry or raise if not configured."""
        if self.tool_registry is None:
            raise ValueError("Tool registry is required but not configured")
        return self.tool_registry

    def require_config(self) -> Any:
        """Get config or raise if not configured."""
        if self.config is None:
            raise ValueError("Config is required but not configured")
        return self.config

    def require_worktrees_dir(self) -> Path:
        """Get worktrees directory or raise if not configured."""
        if self.worktrees_dir is None:
            raise ValueError("Worktrees directory is required but not configured")
        return self.worktrees_dir

    def with_overrides(self, **kwargs) -> "NodeDependencies":
        """Create a new NodeDependencies with some values overridden.

        Useful for creating node-specific variations without mutating the original.

        Example:
            base_deps = NodeDependencies(llm_client=client, config=config)
            node_deps = base_deps.with_overrides(event_pusher=my_pusher)
        """
        return NodeDependencies(
            llm_client=kwargs.get("llm_client", self.llm_client),
            state_manager=kwargs.get("state_manager", self.state_manager),
            tool_registry=kwargs.get("tool_registry", self.tool_registry),
            config=kwargs.get("config", self.config),
            worktrees_dir=kwargs.get("worktrees_dir", self.worktrees_dir),
            event_pusher=kwargs.get("event_pusher", self.event_pusher),
        )

    @classmethod
    def minimal(cls, **kwargs) -> "NodeDependencies":
        """Create minimal dependencies for testing.

        All dependencies default to None, override only what's needed.
        """
        return cls(**kwargs)


def create_dependencies_from_graph_args(
    state_manager,
    llm_client,
    tool_registry,
    config,
    worktrees_dir,
    event_pusher=None,
) -> NodeDependencies:
    """Create NodeDependencies from the typical graph construction arguments.

    This is a convenience function to bridge between the current graph construction
    style and the new dependency injection pattern.
    """
    return NodeDependencies(
        llm_client=llm_client,
        state_manager=state_manager,
        tool_registry=tool_registry,
        config=config,
        worktrees_dir=Path(worktrees_dir) if worktrees_dir else None,
        event_pusher=event_pusher,
    )
