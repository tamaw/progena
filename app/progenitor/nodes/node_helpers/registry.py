"""
Tool Registry.

Central registry for all progenitor system tools with lookup, matching, and execution.
"""

import logging
from typing import TYPE_CHECKING, Any, Optional

from ...tools.protocol import SystemTool, ToolContext

if TYPE_CHECKING:
    from ...state.models import ProgenitorState

logger = logging.getLogger(__name__)

# Registry of all tools (singleton instances)
_SYSTEM_TOOLS: dict[str, SystemTool] = {}


def _init_registry() -> dict[str, SystemTool]:
    """Initialize the tools registry (lazy imports to avoid circular deps)."""
    from ...tools.agent_discovery import AgentDiscoveryTool
    from ...tools.build import BuildTool
    from ...tools.build_status import BuildStatusTool
    from ...tools.copilot import CopilotTool
    from ...tools.copilot_acp import CopilotACPTool
    from ...tools.compress_conversation import CompressConversationTool
    from ...tools.current_time import CurrentTimeTool
    from ...tools.git import GitTool
    from ...tools.list_agents import ListAgentsTool
    from ...tools.list_schedule import ListScheduleTool
    from ...tools.list_tools import ListToolsTool
    from ...tools.mcp import MCPTool
    from ...tools.memory import MemoryTool
    from ...tools.new_conversation import NewConversationTool
    from ...tools.rediscover_agents import RediscoverAgentsTool
    from ...tools.registry import RegistryTool
    from ...tools.schedule import ScheduleTool
    from ...tools.schedule_task import ScheduleTaskTool
    from ...tools.shell import ShellCheckTool, ShellExecTool
    from ...tools.semantic_registry import SemanticRegistryTool
    from ...tools.self_reflect import SelfReflectModuleTool
    from ...tools.self_reflect_tool import SelfReflectTool
    from ...tools.user_communication import UserCommunicationTool

    tools = [
        BuildTool(),
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
        MemoryTool(),
        ShellCheckTool(),
        ShellExecTool(),
        ShellCheckTool(),
        ShellExecTool(),
        # Module-level/system wrappers (status/diagnostic/tools/* coverage)
        RegistryTool(),
        MCPTool(),
        ScheduleTool(),
        SemanticRegistryTool(),
        UserCommunicationTool(),
        GitTool(),
        CopilotTool(),
        CopilotACPTool(),
        AgentDiscoveryTool(),
        SelfReflectModuleTool(),
    ]
    return {tool.name: tool for tool in tools}


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
    """Execute a tool by name and return (result, error, state_updates, should_retry_slow_path).

    This is the main entry point for executing tools from nodes.

    Args:
        tool_name: Name of the tool to execute.
        tool_registry: Registry of available tools and agents.
        llm_client: LLM client for tools that need AI capabilities.
        state: Current graph state (in-memory).
        state_manager: StateManager for reading persisted state files.
        user_input: The user's original input (needed by some tools).

    Returns:
        Tuple of (result_dict, error_string, state_updates, should_retry_slow_path).
        state_updates is a dict of state keys to merge, or None.
        should_retry_slow_path indicates if the caller should retry via a different strategy.
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
        else:
            return None, result.error, None, result.should_retry_slow_path
    except Exception as e:
        logger.error("Error executing tool %s: %s", tool_name, e, exc_info=True)
        return None, f"Error executing {tool_name}: {str(e)}", None, False
