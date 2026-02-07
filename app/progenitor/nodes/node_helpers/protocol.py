"""
Node Helpers Protocol and Base Types.

Defines the interface for tools that can be executed inline by nodes.
"""

import re
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
    user_input: str = ""


@dataclass
class ToolResult:
    """Result from a tool execution."""
    success: bool
    data: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    state_updates: Optional[dict[str, Any]] = None

    @classmethod
    def ok(cls, data: dict[str, Any], state_updates: Optional[dict[str, Any]] = None) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, data=data, state_updates=state_updates)

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

    @property
    def keywords(self) -> list[str]:
        """Keyword phrases for fast-path matching (lowercase)."""
        ...

    @property
    def trigger_patterns(self) -> list[str]:
        """Regex patterns for fast-path matching."""
        ...

    def matches_input(self, user_input: str) -> bool:
        """Check if user input matches this tool's triggers."""
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

    @property
    def keywords(self) -> list[str]:
        """Keyword phrases that trigger fast-path matching (lowercase).

        Override in subclasses to enable fast-path detection.
        Return an empty list to only be reachable via the LLM slow path.
        """
        return []

    @property
    def trigger_patterns(self) -> list[str]:
        """Regex patterns for more complex fast-path matching.

        Override in subclasses for pattern-based triggers.
        Patterns are matched case-insensitively against user input.
        """
        return []

    def matches_input(self, user_input: str) -> bool:
        """Check if user input matches this tool's keywords or patterns."""
        text = (user_input or "").lower()
        if not text.strip():
            return False

        for keyword in self.keywords:
            if keyword in text:
                return True

        for pattern in self.trigger_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True

        return False

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
