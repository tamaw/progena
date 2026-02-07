"""
Tool Registry.

Central registry for all progenitor system tools with lookup, matching, and execution.
"""

import logging
from typing import Any, Optional, TYPE_CHECKING

from .protocol import ToolContext, ToolResult, SystemTool

if TYPE_CHECKING:
    from ...state.models import ProgenitorState

logger = logging.getLogger(__name__)

# Registry of all tools (singleton instances)
_SYSTEM_TOOLS: dict[str, SystemTool] = {}


def _init_registry() -> dict[str, SystemTool]:
    """Initialize the tools registry (lazy imports to avoid circular deps)."""
    from ...tools.list_tools import ListToolsTool
    from ...tools.rediscover_agents import RediscoverAgentsTool
    from ...tools.list_agents import ListAgentsTool
    from ...tools.new_conversation import NewConversationTool
    from ...tools.build_status import BuildStatusTool
    from ...tools.current_time import CurrentTimeTool
    from ...tools.list_schedule import ListScheduleTool
    from ...tools.self_reflect_tool import SelfReflectTool
    from ...tools.schedule_task import ScheduleTaskTool
    from ...tools.compress_conversation import CompressConversationTool

    tools = [
        ListToolsTool(),
        RediscoverAgentsTool(),
        ListAgentsTool(),
        NewConversationTool(),
        BuildStatusTool(),
        CurrentTimeTool(),
        ListScheduleTool(),
        SelfReflectTool(),
        ScheduleTaskTool(),
        CompressConversationTool(),
    ]
    return {tool.name: tool for tool in tools}


def _ensure_registry() -> dict[str, SystemTool]:
    """Ensure registry is initialized and return it."""
    global _SYSTEM_TOOLS
    if not _SYSTEM_TOOLS:
        _SYSTEM_TOOLS = _init_registry()
    return _SYSTEM_TOOLS


def get_system_tool(name: str) -> Optional[SystemTool]:
    """Get a tool by name."""
    return _ensure_registry().get(name)


def get_all_system_tools() -> list[SystemTool]:
    """Get all registered tools."""
    return list(_ensure_registry().values())


def get_system_tool_names() -> list[str]:
    """Get names of all registered tools."""
    return list(_ensure_registry().keys())


def match_tool_by_input(user_input: str) -> Optional[str]:
    """Fast-path: find a system tool matching user input by keywords/patterns.

    Iterates over all registered system tools and checks each tool's
    keywords and trigger_patterns against the user input.

    Returns:
        The tool name if a match is found, otherwise None.
    """
    if not user_input or not user_input.strip():
        return None

    for tool in _ensure_registry().values():
        if tool.matches_input(user_input):
            return tool.name

    return None


async def execute_system_tool(
    tool_name: str,
    tool_registry: Any,
    llm_client: Any,
    state: Optional["ProgenitorState"] = None,
    state_manager: Any = None,
    user_input: str = "",
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """Execute a tool by name and return (result, error, state_updates).

    This is the main entry point for executing tools from nodes.

    Args:
        tool_name: Name of the tool to execute.
        tool_registry: Registry of available tools and agents.
        llm_client: LLM client for tools that need AI capabilities.
        state: Current graph state (in-memory).
        state_manager: StateManager for reading persisted state files.
        user_input: The user's original input (needed by some tools).

    Returns:
        Tuple of (result_dict, error_string, state_updates).
        state_updates is a dict of state keys to merge, or None.
    """
    tool = get_system_tool(tool_name)

    if tool is None:
        return None, f"Unknown system tool: {tool_name}", None

    context = ToolContext(
        tool_registry=tool_registry,
        llm_client=llm_client,
        state=state,
        state_manager=state_manager,
        user_input=user_input,
    )

    try:
        result = await tool.execute(context)

        if result.success:
            return result.data, None, result.state_updates
        else:
            return None, result.error, None
    except Exception as e:
        logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
        return None, f"Error executing {tool_name}: {str(e)}", None
