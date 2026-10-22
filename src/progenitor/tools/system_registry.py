"""
System tool registry.

Discovers Progenitor system tools and provides lookup/execution helpers for the
DeepAgent tool adapter and direct LLM tool-call path.
"""

import importlib
import inspect
import logging
import pkgutil
from types import ModuleType
from typing import TYPE_CHECKING, Any, Optional

from .protocol import BaseSystemTool, SystemTool, ToolContext

if TYPE_CHECKING:
    from src.progenitor.orchestration.state import ProgenitorState

logger = logging.getLogger(__name__)

_SYSTEM_TOOLS: dict[str, SystemTool] = {}


def _iter_tool_modules() -> list[ModuleType]:
    """Iterate importable modules under src.progenitor.tools."""
    import src.progenitor.tools as tools_pkg

    modules: list[ModuleType] = []
    for info in pkgutil.iter_modules(tools_pkg.__path__, tools_pkg.__name__ + "."):
        name = info.name.rsplit(".", 1)[-1]
        if name.startswith("__"):
            continue

        try:
            modules.append(importlib.import_module(info.name))
        except Exception as e:
            logger.debug("Skipping tools module %s (import failed: %s)", info.name, e)

    modules.sort(key=lambda m: m.__name__)
    return modules


def _discover_tool_instances() -> list[BaseSystemTool]:
    """Discover and instantiate all no-arg BaseSystemTool implementations."""
    discovered: list[BaseSystemTool] = []

    for module in _iter_tool_modules():
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if obj is BaseSystemTool:
                continue
            if not issubclass(obj, BaseSystemTool):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue

            try:
                instance = obj()
            except Exception as e:
                logger.debug(
                    "Skipping tool class %s.%s (init failed: %s)",
                    module.__name__,
                    getattr(obj, "__name__", str(obj)),
                    e,
                )
                continue
            discovered.append(instance)

    discovered.sort(key=lambda t: (t.name, t.__class__.__name__))
    return discovered


def _init_registry() -> dict[str, SystemTool]:
    """Initialize the tools registry by discovering tools dynamically."""
    tools = _discover_tool_instances()

    registry: dict[str, SystemTool] = {}
    for tool in tools:
        if tool.name in registry:
            logger.warning(
                "Duplicate system tool name '%s' (%s ignored; keeping %s)",
                tool.name,
                tool.__class__.__name__,
                registry[tool.name].__class__.__name__,
            )
            continue
        registry[tool.name] = tool

    return registry


def _ensure_registry() -> dict[str, SystemTool]:
    """Ensure registry is initialized and return it."""
    global _SYSTEM_TOOLS
    if not _SYSTEM_TOOLS:
        _SYSTEM_TOOLS = _init_registry()
    return _SYSTEM_TOOLS


def get_system_tool(name: str) -> SystemTool | None:
    """Get a tool by name."""
    return _ensure_registry().get(name)


def get_all_system_tools() -> list[SystemTool]:
    """Get all registered tools."""
    return list(_ensure_registry().values())


def get_system_tool_names() -> list[str]:
    """Get names of all registered tools."""
    return list(_ensure_registry().keys())


async def execute_system_tool(
    tool_name: str,
    tool_registry: Any,
    llm_client: Any,
    state: Optional["ProgenitorState"] = None,
    state_manager: Any = None,
    user_input: str = "",
    tool_args: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, bool]:
    """Execute a tool by name.

    Returns (result, error, state_updates, should_retry_slow_path).
    """
    tool = get_system_tool(tool_name)

    if tool is None:
        return None, f"Unknown system tool: {tool_name}", None, False

    context = ToolContext(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state=state,
        state_manager=state_manager,
        user_input=user_input,
        tool_args=tool_args,
    )

    try:
        result = await tool.execute(context)

        if result.success:
            return result.data, None, result.state_updates, False
        return None, result.error, None, result.should_retry_slow_path
    except Exception as e:
        logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
        return None, f"Error executing {tool_name}: {str(e)}", None, False
