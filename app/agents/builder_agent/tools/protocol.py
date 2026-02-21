"""
System Tool Protocol and Base Types.

Defined under app.progenitor.tools to avoid import cycles with app.progenitor.nodes.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol

if TYPE_CHECKING:
    from ..state.models import ProgenitorState


@dataclass
class ToolContext:
    """Context provided to tools during execution."""

    tool_registry: Any
    llm_client: Any
    state: Optional["ProgenitorState"] = None
    state_manager: Any = None
    user_input: str = ""
    tool_args: dict[str, Any] | None = None


@dataclass
class ToolResult:
    """Result from a tool execution."""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    state_updates: dict[str, Any] | None = None
    should_retry_slow_path: bool = False

    @classmethod
    def ok(cls, data: dict[str, Any], state_updates: dict[str, Any] | None = None) -> "ToolResult":
        """Create a successful result."""
        return cls(success=True, data=data, state_updates=state_updates)

    @classmethod
    def fail(cls, error: str, should_retry_slow_path: bool = False) -> "ToolResult":
        """Create a failed result."""
        return cls(success=False, error=error, should_retry_slow_path=should_retry_slow_path)


class SystemTool(Protocol):
    """Protocol for tools that can be executed inline."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def keywords(self) -> list[str]: ...

    @property
    def trigger_patterns(self) -> list[str]: ...

    def matches_input(self, user_input: str) -> bool: ...

    async def execute(self, context: ToolContext) -> ToolResult: ...

    def to_dict(self) -> dict[str, str]: ...


class BaseSystemTool(ABC):
    """Base class for tools with common functionality."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        pass

    @property
    def keywords(self) -> list[str]:
        return []

    @property
    def trigger_patterns(self) -> list[str]:
        return []

    def matches_input(self, user_input: str) -> bool:
        text = (user_input or "").lower()
        if not text.strip():
            return False

        for keyword in self.keywords:
            if keyword in text:
                return True

        return any(re.search(pattern, text, re.IGNORECASE) for pattern in self.trigger_patterns)

    @abstractmethod
    async def execute(self, context: ToolContext) -> ToolResult:
        pass

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "type": "system_tool",
        }

