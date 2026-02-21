"""
Protocol Interfaces for Progenitor Dependencies.

ARCH-005: Define Protocol interfaces for all major dependencies.

This module provides Protocol definitions that:
- Enable dependency injection
- Support easy mocking in tests
- Document expected interfaces
- Allow alternative implementations

Protocols use structural subtyping (duck typing) so implementations
don't need to explicitly inherit from them.
"""

from collections.abc import AsyncIterator
from typing import Any, Protocol, TypeVar, runtime_checkable

# ============================================================================
# LLM Client Protocols
# ============================================================================

@runtime_checkable
class LLMClientProtocol(Protocol):
    """Protocol for LLM client implementations.

    Implementations should provide:
    - Text generation
    - Decision making
    - Planning
    - Build spec generation
    - Streaming support
    """

    async def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> str:
        """Generate text completion.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.

        Returns:
            Generated text.
        """
        ...

    async def decide(
        self,
        user_input: str,
        conversation_history: list[dict[str, Any]],
        tool_context: str,
    ) -> dict[str, Any]:
        """Make a routing decision.

        Args:
            user_input: The user's input.
            conversation_history: Recent conversation messages.
            tool_context: Formatted string describing available tools and agents.

        Returns:
            Decision dict with action, reasoning, etc.
        """
        ...

    async def plan(
        self,
        user_input: str,
        decision: dict[str, Any],
        current_backlog: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Generate work items from a decision.

        Args:
            user_input: The original user input.
            decision: The routing decision.
            current_backlog: Current backlog items.

        Returns:
            List of work item dicts.
        """
        ...

    async def generate_build_spec(
        self,
        work_item: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a build specification.

        Args:
            work_item: The work item to build.
            context: Additional context.

        Returns:
            Build specification dict.
        """
        ...

    async def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream text generation.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.

        Yields:
            Generated text chunks.
        """
        ...


@runtime_checkable
class StreamingLLMProtocol(Protocol):
    """Protocol for streaming-capable LLM clients."""

    async def stream(
        self,
        prompt: str,
        system_prompt: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream text generation."""
        ...


# ============================================================================
# State Management Protocols
# ============================================================================

@runtime_checkable
class StateManagerProtocol(Protocol):
    """Protocol for state manager implementations.

    Handles persistence, logging, and state coordination.
    """

    async def initialize(self) -> None:
        """Initialize the state manager."""
        ...

    async def get_backlog(self) -> list[Any]:
        """Get the current backlog."""
        ...

    async def save_backlog(self, backlog: list[Any]) -> None:
        """Save the backlog."""
        ...

    async def add_work_item(self, item: Any) -> None:
        """Add a work item to the backlog."""
        ...

    async def update_work_item(self, item: Any) -> None:
        """Update a work item."""
        ...

    async def get_schedule(self) -> list[Any]:
        """Get scheduled tasks."""
        ...

    async def save_schedule(self, schedule: list[Any]) -> None:
        """Save scheduled tasks."""
        ...

    async def get_state(self) -> dict[str, Any]:
        """Get current state dict."""
        ...

    async def save_state(self, state: dict[str, Any]) -> None:
        """Save current state."""
        ...

    async def commit_state(self) -> None:
        """Commit all cached state to disk."""
        ...

    async def log_decision(self, decision: dict[str, Any]) -> None:
        """Log a decision."""
        ...

    async def log_conversation(self, message: Any) -> None:
        """Log a conversation message."""
        ...


@runtime_checkable
class StatePersistenceProtocol(Protocol):
    """Protocol for low-level state persistence (ARCH-006).

    Focused only on reading/writing JSON with atomic operations.
    """

    async def read(self, name: str) -> dict[str, Any]:
        """Read a state file."""
        ...

    async def write(self, name: str, data: dict[str, Any]) -> None:
        """Write a state file atomically."""
        ...

    async def append_jsonl(self, name: str, entry: dict[str, Any]) -> None:
        """Append to a JSONL log file."""
        ...


@runtime_checkable
class BacklogManagerProtocol(Protocol):
    """Protocol for backlog business logic (ARCH-006)."""

    def get_actionable_items(self) -> list[Any]:
        """Get items that can be worked on."""
        ...

    async def add_item(self, item: Any) -> None:
        """Add a work item."""
        ...

    async def update_item(self, item: Any) -> None:
        """Update a work item."""
        ...

    def find_by_id(self, item_id: str) -> Any | None:
        """Find an item by ID."""
        ...


@runtime_checkable
class StateLoggerProtocol(Protocol):
    """Protocol for state logging (ARCH-006)."""

    async def log(self, category: str, entry: dict[str, Any]) -> None:
        """Log an entry to a category."""
        ...

    async def read_recent(
        self,
        category: str,
        limit: int = 100
    ) -> list[dict[str, Any]]:
        """Read recent log entries."""
        ...


# ============================================================================
# Tool and Registry Protocols
# ============================================================================

@runtime_checkable
class ToolRegistryProtocol(Protocol):
    """Protocol for tool registry implementations."""

    async def load(self) -> None:
        """Load registry from disk."""
        ...

    async def save(self) -> None:
        """Save registry to disk."""
        ...

    def get_all_capabilities(self) -> list[str]:
        """Get all registered capabilities."""
        ...

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """Find a tool or agent by name."""
        ...

    def find_agent_by_capability(
        self,
        capability: str
    ) -> dict[str, Any] | None:
        """Find an agent providing a capability."""
        ...

    def find_tool_by_capability(
        self,
        capability: str
    ) -> dict[str, Any] | None:
        """Find a tool providing a capability."""
        ...

    async def register_agent(self, entry: Any) -> None:
        """Register an agent."""
        ...

    async def register_tool(self, entry: dict[str, Any]) -> None:
        """Register a tool."""
        ...

    async def unregister(self, name: str) -> bool:
        """Unregister an agent or tool."""
        ...


@runtime_checkable
class SemanticSearchProtocol(Protocol):
    """Protocol for semantic search capability (FEAT-001)."""

    def find_by_capability_semantic(
        self,
        query: str,
        threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """Find tools by semantic similarity.

        Args:
            query: Natural language capability query.
            threshold: Minimum similarity threshold.

        Returns:
            List of matching tools/agents.
        """
        ...

    def compute_similarity(
        self,
        query: str,
        capability: str,
    ) -> float:
        """Compute similarity between query and capability."""
        ...


# ============================================================================
# Observability Protocols
# ============================================================================

@runtime_checkable
class TracerProtocol(Protocol):
    """Protocol for tracing implementations."""

    def start_span(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """Start a new span."""
        ...

    def end_span(self, span: Any) -> None:
        """End a span."""
        ...

    def add_event(
        self,
        span: Any,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Add an event to a span."""
        ...

    def record_exception(self, span: Any, exception: Exception) -> None:
        """Record an exception on a span."""
        ...


@runtime_checkable
class MetricsProtocol(Protocol):
    """Protocol for metrics implementations."""

    def increment(
        self,
        name: str,
        labels: dict[str, str] | None = None,
        value: int = 1,
    ) -> None:
        """Increment a counter."""
        ...

    def record_histogram(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Record a histogram value."""
        ...

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: dict[str, str] | None = None,
    ) -> None:
        """Set a gauge value."""
        ...


# ============================================================================
# Service Protocols
# ============================================================================

@runtime_checkable
class ProgenitorServiceProtocol(Protocol):
    """Protocol for Progenitor service implementations."""

    async def initialize(self) -> None:
        """Initialize the service."""
        ...

    async def handle_message(
        self,
        message: str,
        request_id: str | None = None,
    ) -> str:
        """Handle a user message."""
        ...

    async def handle_message_stream(
        self,
        message: str,
        request_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Handle a message with streaming."""
        ...

    async def shutdown(self) -> None:
        """Shutdown the service."""
        ...

    @property
    def is_initialized(self) -> bool:
        """Check if initialized."""
        ...


@runtime_checkable
class CircuitBreakerProtocol(Protocol):
    """Protocol for circuit breaker implementations (FEAT-004)."""

    @property
    def state(self) -> str:
        """Get current state: 'closed', 'open', or 'half-open'."""
        ...

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        ...

    async def call(
        self,
        func: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Execute a function through the circuit breaker."""
        ...

    def record_success(self) -> None:
        """Record a successful call."""
        ...

    def record_failure(self) -> None:
        """Record a failed call."""
        ...


# ============================================================================
# Type aliases for common patterns
# ============================================================================

# Generic async callable type
T_co = TypeVar('T_co', covariant=True)
T = TypeVar('T')
AsyncCallable = Any  # Callable[..., Coroutine[Any, Any, T]]


# ============================================================================
# Factory function types
# ============================================================================

class NodeFactoryProtocol(Protocol[T_co]):
    """Protocol for node factory functions."""

    def __call__(self, *args: Any, **kwargs: Any) -> T_co:
        """Create a node function."""
        ...
