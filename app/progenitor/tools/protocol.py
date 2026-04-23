"""
System Tool Protocol and Base Types.

Defined under app.progenitor.tools to avoid import cycles with app.progenitor.nodes.
"""

from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol, cast

from pydantic import BaseModel, Field

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


class RequestToolArgs(BaseModel):
    """Default argument model for natural-language tools."""

    request: str = Field(description="Natural language request for the tool.")


def _normalize_tool_schema(value: Any) -> Any:
    """Trim Pydantic JSON schema down to the subset we expose to tool callers."""
    if isinstance(value, dict):
        normalized = {
            key: _normalize_tool_schema(item) for key, item in value.items() if key != "title"
        }
        any_of = normalized.get("anyOf")
        if isinstance(any_of, list):
            non_null_options = [
                option
                for option in any_of
                if not (
                    isinstance(option, dict)
                    and option.get("type") == "null"
                    and set(option.keys()) == {"type"}
                )
            ]
            if len(non_null_options) == 1 and len(non_null_options) != len(any_of):
                merged = deepcopy(cast(dict[str, Any], non_null_options[0]))
                for key, item in normalized.items():
                    if key == "anyOf":
                        continue
                    if key == "default" and item is None:
                        continue
                    merged[key] = item
                normalized = merged
        if normalized.get("default") is None:
            normalized.pop("default", None)
        return normalized
    if isinstance(value, list):
        return [_normalize_tool_schema(item) for item in value]
    return value


def parameters_from_args_model(args_model: type[BaseModel]) -> dict[str, Any]:
    """Render a tool argument model as a normalized JSON schema object."""
    return cast(dict[str, Any], _normalize_tool_schema(args_model.model_json_schema()))


def build_tool_spec(
    *,
    name: str,
    description: str,
    args_model: type[BaseModel],
) -> dict[str, Any]:
    """Build a tool registration spec from a Pydantic argument model."""
    return {
        "name": name,
        "description": description,
        "parameters": parameters_from_args_model(args_model),
    }


def legacy_parameters_from_args_model(args_model: type[BaseModel]) -> dict[str, dict[str, Any]]:
    """Render a JSON-schema model into the legacy per-field parameter shape."""
    parameters = parameters_from_args_model(args_model)
    properties = parameters.get("properties")
    if not isinstance(properties, dict):
        return {}

    required_fields = parameters.get("required")
    required = set(required_fields) if isinstance(required_fields, list) else set()

    legacy: dict[str, dict[str, Any]] = {}
    for field_name, spec in properties.items():
        if not isinstance(spec, dict):
            continue
        entry = dict(spec)
        entry["required"] = field_name in required
        legacy[field_name] = entry
    return legacy


class SystemTool(Protocol):
    """Protocol for tools that can be executed inline."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str: ...

    @property
    def args_model(self) -> type[BaseModel]: ...

    @property
    def parameters(self) -> dict[str, Any]: ...

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

    @abstractmethod
    async def execute(self, context: ToolContext) -> ToolResult:
        pass

    @property
    def args_model(self) -> type[BaseModel]:
        return RequestToolArgs

    @property
    def parameters(self) -> dict[str, Any]:
        return parameters_from_args_model(self.args_model)

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "type": "system_tool",
        }
