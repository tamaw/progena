"""
Node Helpers Protocol and Base Types.

Defines the interface for tools that can be executed inline by nodes.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state.models import ProgenitorState


@dataclass
class ToolContext:
    """Context provided to tools during execution."""
    tool_registry: Any
    llm_client: Any
    state: Optional["ProgenitorState"] = None
    state_manager: Any = None


@dataclass
class ToolResult:
    """Result from a tool execution."""
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @classmethod
    def ok(cls, data: dict[str, Any]) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        """Create a failed result."""
        return cls(success=False, error=error)


class SystemTool(Protocol):
    """Protocol for tools that can be executed inline."""

    @property
    def name(self) -> str:
        """Tool name used for identification."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        ...

    async def execute(self, context: ToolContext) -> ToolResult:
        """Execute the tool and return result."""
        ...

    def to_dict(self) -> dict[str, str]:
        """Convert tool metadata to dictionary."""
        ...


class BaseSystemTool(ABC):
    """Base class for tools with common functionality."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used for identification."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        pass

    @abstractmethod
    async def execute(self, context: ToolContext) -> ToolResult:
        """Execute the tool and return result."""
        pass

    def to_dict(self) -> dict[str, str]:
        """Convert tool metadata to dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "type": "system_tool",
        }
