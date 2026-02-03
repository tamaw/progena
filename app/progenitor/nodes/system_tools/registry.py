"""
System Tools Registry.

Central registry for all system tools with lookup and execution.
"""

import logging
from typing import Any, Optional, TYPE_CHECKING

from .protocol import ToolContext, ToolResult, SystemTool
from .tools import (
    ListToolsTool,
    RediscoverAgentsTool,
    ListAgentsTool,
    BuildStatusTool,
    CurrentTimeTool,
    ListScheduleTool,
    SelfReflectTool,
    ScheduleTaskTool,
)

if TYPE_CHECKING:
    from ...state.models import ProgenitorState

logger = logging.getLogger(__name__)

# Registry of all system tools (singleton instances)
_SYSTEM_TOOLS: dict[str, SystemTool] = {}


def _init_registry() -> dict[str, SystemTool]:
    """Initialize the system tools registry."""
    tools = [
        ListToolsTool(),
        RediscoverAgentsTool(),
        ListAgentsTool(),
        BuildStatusTool(),
        CurrentTimeTool(),
        ListScheduleTool(),
        SelfReflectTool(),
        ScheduleTaskTool(),
    ]
    return {tool.name: tool for tool in tools}


def get_system_tool(name: str) -> Optional[SystemTool]:
    """Get a system tool by name."""
    global _SYSTEM_TOOLS
    if not _SYSTEM_TOOLS:
        _SYSTEM_TOOLS = _init_registry()
    return _SYSTEM_TOOLS.get(name)


def get_all_system_tools() -> list[SystemTool]:
    """Get all registered system tools."""
    global _SYSTEM_TOOLS
    if not _SYSTEM_TOOLS:
        _SYSTEM_TOOLS = _init_registry()
    return list(_SYSTEM_TOOLS.values())


def get_system_tool_names() -> list[str]:
    """Get names of all registered system tools."""
    global _SYSTEM_TOOLS
    if not _SYSTEM_TOOLS:
        _SYSTEM_TOOLS = _init_registry()
    return list(_SYSTEM_TOOLS.keys())


async def execute_system_tool(
    tool_name: str,
    tool_registry: Any,
    llm_client: Any,
    state: Optional["ProgenitorState"] = None,
    state_manager: Any = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Execute a system tool by name and return (result, error).

    This is the main entry point for executing system tools.

    Args:
        tool_name: Name of the system tool to execute.
        tool_registry: Registry of available tools and agents.
        llm_client: LLM client for tools that need AI capabilities.
        state: Current graph state (in-memory).
        state_manager: StateManager for reading persisted state files.

    Returns:
        Tuple of (result_dict, error_string). One will always be None.
    """
    tool = get_system_tool(tool_name)

    if tool is None:
        return None, f"Unknown system tool: {tool_name}"

    context = ToolContext(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state=state,
        state_manager=state_manager,
    )

    try:
        result = await tool.execute(context)

        if result.success:
            return result.data, None
        else:
            return None, result.error
    except Exception as e:
        logger.error("Error executing system tool %s: %s", tool_name, e, exc_info=True)
        return None, f"Error executing {tool_name}: {str(e)}"
